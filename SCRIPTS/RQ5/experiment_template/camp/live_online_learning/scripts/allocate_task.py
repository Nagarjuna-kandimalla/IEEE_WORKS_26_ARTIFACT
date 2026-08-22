#!/usr/bin/env python3
"""Persist one online CAMP decision and print its memory request."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import re
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


TRANSIENT_CONNECT_ERRNOS = {
    errno.EAGAIN,
    errno.EWOULDBLOCK,
    errno.ECONNREFUSED,
    errno.ENOENT,
}


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def identity_hash(process: str, task_instance: str, input_bytes: int) -> str:
    payload = f"{process}\0{task_instance}\0{input_bytes}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def request_prediction(socket_path: Path, task: dict[str, object]) -> dict:
    payload = {"task": task, "allocation_variant": "A+P+C"}
    deadline = time.monotonic() + 300.0
    delay = 0.01
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(600.0)
                client.connect(str(socket_path))
                client.sendall(
                    json.dumps(payload).encode("utf-8") + b"\n"
                )
                response = b""
                while True:
                    block = client.recv(65536)
                    if not block:
                        break
                    response += block
            break
        except OSError as error:
            if error.errno not in TRANSIENT_CONNECT_ERRNOS:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"prediction service unavailable: {socket_path}"
                ) from error
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 0.5)
    decoded = json.loads(response.decode("utf-8"))
    if not decoded.get("ok"):
        raise RuntimeError(
            f"CAMP prediction failed: {decoded.get('error')}"
        )
    return dict(decoded["result"])


def previous_attempt(
    decision_dir: Path,
    *,
    attempt: int,
    workflow: str,
    process: str,
    task_instance: str,
    input_identity: str,
) -> dict | None:
    """Find the prior logical attempt even when Nextflow changes its hash."""
    if attempt <= 1:
        return None
    matches = []
    for path in decision_dir.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            int(item.get("attempt", 0)) == attempt - 1
            and str(item.get("workflow")) == workflow
            and str(item.get("process")) == process
            and str(item.get("task_instance")) == task_instance
            and str(item.get("input_identity")) == input_identity
        ):
            matches.append(item)
    if not matches:
        return None
    return max(matches, key=lambda item: str(item["decision_time"]))


def parser() -> argparse.ArgumentParser:
    item = argparse.ArgumentParser(description=__doc__)
    item.add_argument("--run-id", required=True)
    item.add_argument("--task-key", required=True)
    item.add_argument("--attempt", type=int, required=True)
    item.add_argument("--workflow", required=True)
    item.add_argument("--process", required=True)
    item.add_argument("--version", required=True)
    item.add_argument("--system-config-id", required=True)
    item.add_argument("--task-instance", required=True)
    item.add_argument("--input-identity")
    item.add_argument("--input-bytes", type=int, required=True)
    item.add_argument("--staged-original-input-bytes", type=int)
    item.add_argument("--staged-intermediate-input-bytes", type=int)
    item.add_argument("--staged-external-input-bytes", type=int)
    item.add_argument("--requested-threads", type=int, required=True)
    item.add_argument("--worker-count", type=int, default=13)
    item.add_argument("--worker-vcpu", type=int, default=32)
    item.add_argument("--worker-memory-gib", type=float, default=244.140625)
    item.add_argument(
        "--allocation-variant",
        choices=("A", "A+P", "A+P+C"),
        default="A+P+C",
    )
    item.add_argument("--decision-dir", type=Path, required=True)
    item.add_argument("--prediction-socket", type=Path, required=True)
    item.add_argument("--retry-multiplier", type=float, default=1.5)
    item.add_argument("--maximum-allocation-mb", type=int, default=244000)
    item.add_argument("--minimum-allocation-mb", type=int, default=0)
    item.add_argument("--minimum-allocation-reason", default="")
    return item


def main() -> None:
    args = parser().parse_args()
    if args.attempt < 1:
        raise SystemExit("--attempt must be at least 1")
    if args.input_bytes <= 0:
        raise SystemExit("--input-bytes must be positive")
    if args.retry_multiplier < 1.0:
        raise SystemExit("--retry-multiplier must be at least 1")

    task_key = safe_name(args.task_key)
    decision_path = args.decision_dir / (
        f"{task_key}.attempt_{args.attempt}.json"
    )
    if decision_path.exists():
        saved = json.loads(decision_path.read_text(encoding="utf-8"))
        print(int(saved["recommended_allocation_mb"]))
        return

    input_identity = args.input_identity or identity_hash(
        args.process,
        args.task_instance,
        args.input_bytes,
    )
    task_id = (
        f"{args.run_id}:{args.process}:{args.task_instance}:"
        f"attempt_{args.attempt}:{task_key}"
    )
    task = {
        "task_id": task_id,
        "decision_time": datetime.now(timezone.utc).isoformat(),
        "workflow": args.workflow,
        "process": args.process,
        "version": args.version,
        "system_config_id": args.system_config_id,
        "task_instance": args.task_instance,
        "input_identity": input_identity,
        "static_input_bytes": args.input_bytes,
        "staged_original_input_bytes": args.staged_original_input_bytes,
        "staged_intermediate_input_bytes": (
            args.staged_intermediate_input_bytes
        ),
        "staged_external_input_bytes": args.staged_external_input_bytes,
        "requested_threads": args.requested_threads,
        "worker_count": args.worker_count,
        "worker_vcpu": args.worker_vcpu,
        "worker_memory_gib": args.worker_memory_gib,
    }
    result = request_prediction(args.prediction_socket, task)
    base_request = float(result["recommended_allocation_mb"])
    prior = previous_attempt(
        args.decision_dir,
        attempt=args.attempt,
        workflow=args.workflow,
        process=args.process,
        task_instance=args.task_instance,
        input_identity=input_identity,
    )
    retry_floor = (
        float(prior["recommended_allocation_mb"])
        * args.retry_multiplier
        if prior is not None
        else 0.0
    )
    allocation = int(math.ceil(max(base_request, retry_floor)))
    if allocation > args.maximum_allocation_mb:
        raise SystemExit(
            "CAMP prediction is unschedulable on a standard worker: "
            f"request={allocation} MiB, maximum={args.maximum_allocation_mb} "
            "MiB. No silent cap or model fallback was applied."
        )
    result.update(
        {
            "run_id": args.run_id,
            "task_key": task_key,
            "attempt": args.attempt,
            "task_instance": args.task_instance,
            "input_identity": input_identity,
            "base_model_allocation_mb": base_request,
            "retry_floor_mb": retry_floor,
            "retry_predecessor_task_id": (
                None if prior is None else str(prior["task_id"])
            ),
            "maximum_allocation_mb": args.maximum_allocation_mb,
            "declared_process_minimum_mb": args.minimum_allocation_mb,
            "declared_process_minimum_reason": (
                args.minimum_allocation_reason
            ),
            "declared_process_minimum_applied": False,
            "recommended_allocation_mb": allocation,
        }
    )
    result["record_after_completion"]["allocated_memory_mb"] = allocation
    args.decision_dir.mkdir(parents=True, exist_ok=True)
    temporary = decision_path.with_name(
        f".{decision_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(decision_path)
    finally:
        temporary.unlink(missing_ok=True)
    print(allocation)


if __name__ == "__main__":
    main()
