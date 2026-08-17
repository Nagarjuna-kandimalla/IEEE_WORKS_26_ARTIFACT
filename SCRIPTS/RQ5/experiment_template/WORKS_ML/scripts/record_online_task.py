#!/usr/bin/env python3
"""Record one completed audited task in the run-local CAMP history database."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from camp_ml.online_history import TaskOutcome


OOM_EXIT_CODES = {137}


def parse_peak_rss_bytes(path: Path) -> int | None:
    if not path.exists():
        return None
    pattern = re.compile(
        r"Maximum resident set size \(kbytes\):\s*(\d+)"
    )
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.search(line)
        if match:
            value = int(match.group(1)) * 1024
            return value if value > 0 else None
    return None


def parse_runtime_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    pattern = re.compile(
        r"Elapsed \(wall clock\) time "
        r"\(h:mm:ss or m:ss\):\s*([0-9:.]+)\s*$"
    )
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.search(line)
        if not match:
            continue
        parts = [float(part) for part in match.group(1).split(":")]
        if len(parts) == 3:
            value = parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 2:
            value = parts[0] * 60 + parts[1]
        else:
            value = parts[0]
        return value if value >= 0 else None
    return None


def parse_consumed_bytes(trace_dir: Path) -> int | None:
    paths = sorted(trace_dir.glob("ebpf_attribution_*.tsv"))
    if not paths:
        return None
    total = 0
    observed = False
    for path in paths:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                value = row.get("total_bytes")
                if value not in (None, ""):
                    total += int(float(value))
                    observed = True
                else:
                    read = int(float(row.get("read_bytes", "0") or 0))
                    mmap = int(float(row.get("mmap_bytes", "0") or 0))
                    total += read + mmap
                    observed = True
    return total if observed else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-json", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--outcome-dir", type=Path, required=True)
    parser.add_argument("--exit-status", type=int, default=0)
    args = parser.parse_args()

    decision = json.loads(args.decision_json.read_text(encoding="utf-8"))
    completion = datetime.now(timezone.utc).isoformat()
    time_report = args.trace_dir / "camp_time_v.txt"
    peak = parse_peak_rss_bytes(time_report)
    runtime = parse_runtime_seconds(time_report)
    consumed = parse_consumed_bytes(args.trace_dir)
    oom = args.exit_status in OOM_EXIT_CODES
    if args.exit_status == 0 and peak is None:
        raise SystemExit(
            f"successful task has no peak-RSS measurement: {args.trace_dir}"
        )
    task_outcome = TaskOutcome(
        task_id=str(decision["task_id"]),
        workflow=str(decision["workflow"]),
        process=str(decision["process"]),
        version=str(decision["version"]),
        decision_time=str(decision["decision_time"]),
        completion_time=completion,
        static_input_bytes=int(decision["static_input_bytes"]),
        peak_memory_bytes=peak,
        consumed_bytes=consumed,
        runtime_seconds=runtime,
        oom_flag=oom,
        system_config_id=str(
            decision["history_context"]["identity"]["system_config_id"]
        ),
        source_run_id=str(decision["run_id"]),
        static_prediction_mb=float(decision["static_q50_prediction_mb"]),
        history_prediction_mb=float(
            decision["history_q50_prediction_mb"]
        ),
        allocated_memory_mb=float(
            decision["recommended_allocation_mb"]
        ),
        model_version=str(decision["model_version"]),
        task_instance=str(decision["task_instance"]),
        input_identity=str(decision["input_identity"]),
    )
    args.outcome_dir.mkdir(parents=True, exist_ok=True)
    outcome_path = args.outcome_dir / (
        args.decision_json.stem + ".outcome.json"
    )
    temporary = outcome_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "task_outcome": asdict(task_outcome),
                "exit_status": args.exit_status,
                "trace_dir": str(args.trace_dir.resolve()),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(outcome_path)


if __name__ == "__main__":
    main()
