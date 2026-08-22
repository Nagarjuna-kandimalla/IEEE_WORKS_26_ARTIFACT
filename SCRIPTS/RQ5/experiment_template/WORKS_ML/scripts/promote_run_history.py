#!/usr/bin/env python3
"""Combine frozen inference history and one run's outcomes for a later run."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from camp_ml.online_history import SQLiteHistoryStore, TaskOutcome


OUTCOME_FIELDS = (
    "task_id",
    "workflow",
    "process",
    "version",
    "decision_time",
    "completion_time",
    "static_input_bytes",
    "peak_memory_bytes",
    "consumed_bytes",
    "runtime_seconds",
    "oom_flag",
    "system_config_id",
    "source_run_id",
    "static_prediction_mb",
    "history_prediction_mb",
    "allocated_memory_mb",
    "model_version",
    "canonical_operation",
    "task_instance",
    "input_identity",
)


def database_rows(path: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            f"SELECT {', '.join(OUTCOME_FIELDS)} FROM task_outcomes"
        ).fetchall()
    finally:
        connection.close()


def outcome(row: sqlite3.Row) -> TaskOutcome:
    values = dict(row)
    values["oom_flag"] = bool(values["oom_flag"])
    return TaskOutcome(**values)


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-history", type=Path, required=True)
    parser.add_argument("--run-outcomes", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--expected-workflow")
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()

    for path in (args.base_history, args.run_outcomes):
        if not path.is_file():
            raise SystemExit(f"missing input database: {path}")
    destination = args.destination.resolve()
    if destination.exists() and not args.reuse:
        raise SystemExit(f"destination already exists: {destination}")

    rows = database_rows(args.run_outcomes)
    if not rows:
        raise SystemExit("run outcome database is empty")
    workflows = sorted({str(row["workflow"]) for row in rows})
    if (
        args.expected_workflow
        and workflows != [args.expected_workflow]
    ):
        raise SystemExit(
            f"unexpected outcome workflows: {', '.join(workflows)}"
        )

    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(args.base_history.resolve())
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    with SQLiteHistoryStore(destination) as store:
        before = store.count()
        imported = store.record_many(outcome(row) for row in rows)
        after = store.count()
    supported = sum(
        row["peak_memory_bytes"] is not None
        and row["consumed_bytes"] is not None
        for row in rows
    )

    manifest = {
        "base_history": str(args.base_history.resolve()),
        "run_outcomes": str(args.run_outcomes.resolve()),
        "destination": str(destination),
        "destination_rows_before_merge": before,
        "outcome_rows_processed": imported,
        "destination_rows": after,
        "workflows": workflows,
        "outcome_supported_rows": int(supported),
        "base_sha256": checksum(args.base_history),
        "outcomes_sha256": checksum(args.run_outcomes),
        "destination_sha256": checksum(destination),
        "policy": "available only to later workflow runs",
    }
    manifest_path = destination.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"base_rows={before}")
    print(f"outcome_rows={imported}")
    print(f"promoted_rows={after}")
    print(f"promoted_history={destination}")
    print(f"manifest={manifest_path}")


if __name__ == "__main__":
    main()
