#!/usr/bin/env python3
"""Validate one cold or warm run's model, history, and calibration contract."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd


def workflow_rows(path: Path, workflow: str) -> tuple[int, int]:
    frame = pd.read_csv(
        path,
        sep="\t",
        usecols=["logical_task_id", "workflow"],
        low_memory=False,
    )
    matches = (
        frame["workflow"].astype(str).str.lower().eq(workflow.lower())
    )
    return len(frame), int(matches.sum())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--history-features", type=Path, required=True)
    parser.add_argument("--history-db", type=Path, required=True)
    parser.add_argument("--base-training-frame", type=Path, required=True)
    parser.add_argument("--expected-workflow-rows", type=int, required=True)
    args = parser.parse_args()

    manifest_path = args.model_root / "online_manifest.json"
    calibration_path = args.model_root / "version_calibration.tsv"
    required = [
        manifest_path,
        calibration_path,
        args.history_features,
        args.history_db,
        args.base_training_frame,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"missing run inputs: {missing}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model_version = str(manifest["model_version"])
    calibration = pd.read_csv(
        calibration_path,
        sep="\t",
        low_memory=False,
    )
    calibration_versions = set(
        calibration["calibration_model_version"].dropna().astype(str)
    )
    if calibration_versions - {model_version}:
        raise SystemExit(
            "model/calibration version mismatch: "
            f"model={model_version}, "
            f"calibration={sorted(calibration_versions)}"
        )
    manifest_calibration_version = str(
        manifest["version_calibration_model_version"]
    )
    if manifest_calibration_version != model_version:
        raise SystemExit(
            "manifest calibration version mismatch: "
            f"model={model_version}, "
            f"manifest_calibration={manifest_calibration_version}"
        )
    manifest_calibration_rows = int(
        manifest.get("version_calibration_rows", -1)
    )
    if manifest_calibration_rows != len(calibration):
        raise SystemExit(
            "manifest/calibration row mismatch: "
            f"manifest={manifest_calibration_rows}, "
            f"file={len(calibration)}"
        )

    history_rows, history_target_rows = workflow_rows(
        args.history_features,
        args.workflow,
    )
    training_rows, training_target_rows = workflow_rows(
        args.base_training_frame,
        args.workflow,
    )
    connection = sqlite3.connect(
        f"file:{args.history_db.resolve()}?mode=ro",
        uri=True,
    )
    try:
        sqlite_rows, sqlite_target_rows = connection.execute(
            """
            SELECT COUNT(*),
                   SUM(
                       CASE WHEN lower(workflow) = lower(?)
                            THEN 1 ELSE 0 END
                   )
            FROM task_outcomes
            """,
            (args.workflow,),
        ).fetchone()
    finally:
        connection.close()
    sqlite_target_rows = int(sqlite_target_rows or 0)
    expected = args.expected_workflow_rows
    observed = {
        "history": history_target_rows,
        "sqlite": sqlite_target_rows,
        "training": training_target_rows,
    }
    if set(observed.values()) != {expected}:
        raise SystemExit(
            f"expected {expected} {args.workflow} rows in every state; "
            f"found {observed}"
        )
    if sqlite_rows != history_rows:
        raise SystemExit(
            "SQLite/history sidecar row mismatch: "
            f"sqlite={sqlite_rows}, history={history_rows}"
        )

    known = {
        str(value).lower() for value in manifest.get("known_workflows", [])
    }
    model_knows_target = args.workflow.lower() in known
    if model_knows_target != (expected > 0):
        raise SystemExit(
            "model cold/warm status does not match state: "
            f"known={model_knows_target}, expected_rows={expected}"
        )
    if expected > 0 and len(calibration) == 0:
        raise SystemExit("warm model has no version-aligned calibration rows")
    if expected == 0 and len(calibration) != 0:
        raise SystemExit("cold model unexpectedly has calibration rows")

    print(
        json.dumps(
            {
                "status": "PASS",
                "mode": "warm" if expected else "cold",
                "workflow": args.workflow.lower(),
                "model_version": model_version,
                "calibration_model_version": model_version,
                "calibration_rows": len(calibration),
                "history_rows": history_rows,
                "training_rows": training_rows,
                "target_rows": observed,
                "sqlite_rows": int(sqlite_rows),
                "model_knows_target": model_knows_target,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
