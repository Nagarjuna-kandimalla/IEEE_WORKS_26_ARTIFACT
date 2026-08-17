#!/usr/bin/env python3
"""Build fresh history sidecar and SQLite state from causal Design 3 rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = ROOT.parents[1] / "WORKS_ML"
sys.path.insert(0, str(LEGACY_ROOT))

from camp_ml.online_history import SQLiteHistoryStore, TaskOutcome


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def text(row: pd.Series, column: str, default: str = "") -> str:
    value = row.get(column, default)
    return default if pd.isna(value) else str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initial-features", type=Path, required=True)
    parser.add_argument("--test-features", type=Path)
    parser.add_argument("--history-tsv", type=Path, required=True)
    parser.add_argument("--history-db", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--excluded-workflow",
        action="append",
        default=[],
    )
    args = parser.parse_args()
    for path in (args.history_tsv, args.history_db, args.manifest):
        if path.exists():
            raise SystemExit(f"refusing to overwrite fresh artifact: {path}")

    initial = pd.read_csv(
        args.initial_features,
        sep="\t",
        low_memory=False,
    )
    initial["history_role"] = "initial"
    frames = [initial]
    if args.test_features is not None:
        test = pd.read_csv(
            args.test_features,
            sep="\t",
            low_memory=False,
        )
        test["history_role"] = "test"
        frames.append(test)
    frame = pd.concat(frames, ignore_index=True, sort=False)
    frame["history_order"] = np.arange(len(frame), dtype=int)
    if frame["logical_task_id"].astype(str).duplicated().any():
        raise SystemExit("fresh history contains duplicate logical task IDs")
    excluded = {value.strip().lower() for value in args.excluded_workflow}
    observed = set(frame["workflow"].astype(str).str.lower())
    overlap = excluded & observed
    if overlap:
        raise SystemExit(
            f"cold-start target workflows leaked into history: {sorted(overlap)}"
        )

    required = [
        "logical_task_id",
        "history_order",
        "history_role",
        "workflow",
        "process",
        "version",
        "system_config_id",
        "task_instance",
        "input_identity",
        "split_group_id",
        "static_input_bytes",
        "staged_original_input_bytes",
        "staged_intermediate_input_bytes",
        "staged_external_input_bytes",
        "requested_threads",
        "requested_java_heap_mb",
        "coverage",
        "interval_length_bp",
        "has_str_region",
        "worker_count",
        "worker_vcpu",
        "worker_memory_gib",
        "peak_memory_bytes",
        "ebpf_total_consumed_bytes",
        "decision_time",
        "completion_time",
        "runtime_seconds",
    ]
    missing = set(required) - set(frame.columns)
    if missing:
        raise SystemExit(f"fresh history lacks columns: {sorted(missing)}")
    output = frame[required].copy()
    args.history_tsv.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.history_tsv, sep="\t", index=False)

    outcomes = []
    for _, row in output.iterrows():
        outcomes.append(
            TaskOutcome(
                task_id=text(row, "logical_task_id"),
                workflow=text(row, "workflow"),
                process=text(row, "process"),
                version=text(row, "version"),
                decision_time=text(row, "decision_time"),
                completion_time=text(row, "completion_time"),
                static_input_bytes=int(row["static_input_bytes"]),
                peak_memory_bytes=int(row["peak_memory_bytes"]),
                consumed_bytes=int(row["ebpf_total_consumed_bytes"]),
                runtime_seconds=(
                    None
                    if pd.isna(row["runtime_seconds"])
                    else float(row["runtime_seconds"])
                ),
                oom_flag=False,
                system_config_id=text(row, "system_config_id"),
                source_run_id="fresh-canonical-seed1996",
                model_version="fresh-history-label-only",
                task_instance=text(row, "task_instance"),
                input_identity=text(row, "input_identity"),
            )
        )
    with SQLiteHistoryStore(args.history_db) as store:
        inserted = store.record_many(outcomes)
        if store.count() != len(output):
            raise SystemExit("fresh SQLite row count mismatch")
    manifest = {
        "rows": int(len(output)),
        "sqlite_rows_inserted": inserted,
        "workflows": {
            str(key): int(value)
            for key, value in output.groupby("workflow").size().items()
        },
        "excluded_workflows": sorted(excluded),
        "target_workflow_overlap": sorted(overlap),
        "initial_features_sha256": sha256(args.initial_features),
        "test_features_sha256": (
            sha256(args.test_features)
            if args.test_features is not None
            else None
        ),
        "history_tsv_sha256": sha256(args.history_tsv),
        "history_db_sha256": sha256(args.history_db),
    }
    args.manifest.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
