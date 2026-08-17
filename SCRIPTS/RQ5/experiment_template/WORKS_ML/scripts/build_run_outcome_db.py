#!/usr/bin/env python3
"""Build one run-local SQLite outcome database from per-task JSON files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from camp_ml.online_history import SQLiteHistoryStore, TaskOutcome


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outcome-dir", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--expected-workflow")
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()

    paths = sorted(args.outcome_dir.glob("*.outcome.json"))
    if not paths:
        raise SystemExit(f"no task outcome JSON files in {args.outcome_dir}")
    outcomes = []
    skipped_non_oom_failures = 0
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        item = TaskOutcome(**payload["task_outcome"])
        if (
            args.expected_workflow
            and item.workflow != args.expected_workflow
        ):
            raise SystemExit(
                f"unexpected workflow {item.workflow!r} in {path}"
            )
        exit_status = int(payload.get("exit_status", 0))
        if exit_status != 0 and not item.oom_flag:
            skipped_non_oom_failures += 1
            continue
        outcomes.append(item)

    if args.db.exists() and not args.reuse:
        raise SystemExit(f"outcome database already exists: {args.db}")
    with SQLiteHistoryStore(args.db) as store:
        store.record_many(outcomes)
        rows = store.count()
        supported = store.connection.execute(
            """
            SELECT COUNT(*) FROM task_outcomes
            WHERE peak_memory_bytes IS NOT NULL
              AND consumed_bytes IS NOT NULL
            """
        ).fetchone()[0]
        oom = store.connection.execute(
            "SELECT COALESCE(SUM(oom_flag), 0) FROM task_outcomes"
        ).fetchone()[0]
    print(f"outcome_json_files={len(paths)}")
    print(f"skipped_non_oom_failures={skipped_non_oom_failures}")
    print(f"outcome_db_rows={rows}")
    print(f"supported_rows={supported}")
    print(f"oom_rows={oom}")
    print(f"outcome_db={args.db.resolve()}")


if __name__ == "__main__":
    main()
