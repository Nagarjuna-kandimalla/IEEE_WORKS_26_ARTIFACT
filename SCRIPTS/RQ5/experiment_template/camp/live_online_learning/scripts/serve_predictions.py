#!/usr/bin/env python3
"""Serve hot-reloaded CAMP predictions with incremental history."""

from __future__ import annotations

import argparse
import json
import os
import socketserver
import sys
from pathlib import Path
from threading import RLock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from camp_online.engine import OnlineCampEngine


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            request = json.loads(
                self.rfile.readline(16 * 1024 * 1024).decode("utf-8")
            )
            result = self.server.predict(request["task"])
            response = {"ok": True, "result": result}
        except Exception as error:
            response = {
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
            }
        self.wfile.write(
            json.dumps(response, allow_nan=False).encode("utf-8") + b"\n"
        )


class Server(socketserver.UnixStreamServer):
    allow_reuse_address = True
    request_queue_size = 1024

    def __init__(
        self,
        address: str,
        pointer_path: Path,
        history_update_dir: Path,
        cold_start_root: Path,
        cold_start_policy: Path,
        policy_root: Path,
        config_path: Path,
        event_log: Path,
    ) -> None:
        self.pointer_path = pointer_path
        self.history_update_dir = history_update_dir
        self.history_update_dir.mkdir(parents=True, exist_ok=True)
        self.cold_start_root = cold_start_root
        self.cold_start_policy = cold_start_policy
        self.policy_root = policy_root
        self.config_path = config_path
        self.event_log = event_log
        self.pointer_signature = None
        self.engine = None
        self.last_sequence = 0
        self.lock = RLock()
        self.reload(force=True)
        super().__init__(address, Handler)

    def append_event(self, payload: dict) -> None:
        self.event_log.parent.mkdir(parents=True, exist_ok=True)
        with self.event_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def reload(self, force: bool = False) -> None:
        stat = self.pointer_path.stat()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if not force and signature == self.pointer_signature:
            return
        pointer = json.loads(
            self.pointer_path.read_text(encoding="utf-8")
        )
        candidate = OnlineCampEngine(
            Path(pointer["model_root"]),
            Path(pointer["history_features"]),
            self.cold_start_root,
            self.cold_start_policy,
            self.policy_root,
            self.config_path,
        )
        self.engine = candidate
        self.pointer_signature = signature
        # Reconcile only rows completed after the model's history snapshot.
        self.last_sequence = int(
            pointer.get(
                "history_sequence",
                pointer.get("eligible_count", 0),
            )
        )
        self.append_event(
            {
                "event": "predictor_model_loaded",
                "model_version": candidate.model_version,
                "history_rows": len(candidate.history_ids),
                "history_sequence": self.last_sequence,
            }
        )

    def sync_history(self) -> None:
        for path in sorted(self.history_update_dir.glob("*.json")):
            try:
                sequence = int(path.stem)
            except ValueError:
                continue
            if sequence <= self.last_sequence:
                continue
            if sequence != self.last_sequence + 1:
                break
            payload = json.loads(path.read_text(encoding="utf-8"))
            if int(payload["sequence"]) != sequence:
                raise ValueError(
                    f"history update sequence mismatch: {path}"
                )
            self.engine.ingest_history_row(payload["row"])
            self.last_sequence = sequence

    def predict(self, task: dict) -> dict:
        with self.lock:
            self.reload()
            self.sync_history()
            return self.engine.predict(task)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--current-pointer", type=Path, required=True)
    parser.add_argument("--history-update-dir", type=Path, required=True)
    parser.add_argument("--cold-start-root", type=Path, required=True)
    parser.add_argument("--cold-start-policy", type=Path, required=True)
    parser.add_argument("--policy-root", type=Path, required=True)
    parser.add_argument("--experiment-config", type=Path, required=True)
    parser.add_argument("--event-log", type=Path, required=True)
    args = parser.parse_args()

    socket_path = args.socket.resolve()
    ready_file = args.ready_file.resolve()
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    ready_file.unlink(missing_ok=True)
    try:
        with Server(
            str(socket_path),
            args.current_pointer.resolve(),
            args.history_update_dir.resolve(),
            args.cold_start_root.resolve(),
            args.cold_start_policy.resolve(),
            args.policy_root.resolve(),
            args.experiment_config.resolve(),
            args.event_log.resolve(),
        ) as server:
            temporary = ready_file.with_suffix(".tmp")
            temporary.write_text(
                (
                    f"pid={os.getpid()}\n"
                    f"socket={socket_path}\n"
                    f"model_version={server.engine.model_version}\n"
                    f"history_rows={len(server.engine.history_ids)}\n"
                ),
                encoding="utf-8",
            )
            temporary.replace(ready_file)
            server.serve_forever(poll_interval=0.2)
    finally:
        socket_path.unlink(missing_ok=True)
        ready_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
