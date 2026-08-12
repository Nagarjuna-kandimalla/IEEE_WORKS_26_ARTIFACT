#!/usr/bin/env python3
"""Normalize unchanged Sizey task CSVs to the common task-result schema."""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

import numpy as np
import pandas as pd

from common import MIB, ROOT, common_task_metrics, read_tsv


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--cohort",
        choices=("primary", "extended"),
        default="primary",
    )
    return parser.parse_args()


def parse_predictions(value: object) -> list[float]:
    text = str(value)
    text = re.sub(r"np\.float\d*\(([^()]*)\)", r"\1", text)
    parsed = ast.literal_eval(text)
    if not isinstance(parsed, list) or not parsed:
        raise ValueError(f"invalid Sizey prediction list: {value}")
    return [float(item) for item in parsed]


def locate_task_csv(result_directory: Path) -> Path:
    candidates = sorted(result_directory.glob("*_tasks.csv"))
    if len(candidates) != 1:
        raise ValueError(
            f"expected one Sizey task CSV in {result_directory}, found "
            f"{len(candidates)}"
        )
    return candidates[0]


def main() -> None:
    args = arguments()
    run_root = (
        ROOT
        / "results"
        / "raw_sizey"
        / args.cohort
        / f"seed_{args.seed}"
        / args.workflow
    )
    task_csv = locate_task_csv(run_root / "work" / "results")
    native = pd.read_csv(task_csv)
    native = native.loc[native["Method"] == "Sizey"].copy()
    manifest = read_tsv(
        ROOT
        / "data"
        / "split_manifests"
        / args.cohort
        / f"{args.workflow}_{args.seed}.tsv"
    )
    manifest = manifest.loc[manifest["role"] == "test"].copy()

    outputs: list[pd.DataFrame] = []
    for process, process_native in native.groupby("Task_Name", sort=False):
        process_manifest = manifest.loc[
            manifest["process"].astype(str) == str(process)
        ].sort_values("replay_position")
        process_native = process_native.reset_index(drop=True)
        if len(process_native) != len(process_manifest):
            raise ValueError(
                f"{args.workflow}/{process}: Sizey emitted "
                f"{len(process_native)} rows for {len(process_manifest)} tests"
            )
        prediction_lists = [
            parse_predictions(value)
            for value in process_native["Predictions"]
        ]
        output = pd.DataFrame(
            {
                "logical_task_id": process_manifest[
                    "logical_task_id"
                ].astype(str).to_numpy(),
                "workflow": args.workflow,
                "process": str(process),
                "seed": args.seed,
                "replay_position": pd.to_numeric(
                    process_manifest["replay_position"], errors="raise"
                ).astype(int).to_numpy(),
                "method": "Sizey",
                "feature_view": "native_A_with_peak_feedback",
                "raw_prediction_mib": pd.to_numeric(
                    process_native["Raw_Predictions"], errors="raise"
                ).to_numpy()
                / MIB,
                "first_allocation_mib": np.asarray(
                    [values[0] for values in prediction_lists]
                )
                / MIB,
                "actual_peak_mib": pd.to_numeric(
                    process_native["Actual_Memory"], errors="raise"
                ).to_numpy()
                / MIB,
                "runtime_seconds": pd.to_numeric(
                    process_native["Task_Runtime"], errors="coerce"
                ).to_numpy()
                / 1000.0,
                "native_retry_count": pd.to_numeric(
                    process_native["Failures"], errors="raise"
                ).astype(int).to_numpy(),
                "native_final_allocation_mib": np.asarray(
                    [values[-1] for values in prediction_lists]
                )
                / MIB,
                "history_support_count": np.nan,
                "history_scope": "sizey_native_process_model",
            }
        )
        outputs.append(output)

    normalized = common_task_metrics(pd.concat(outputs, ignore_index=True))
    output_path = run_root / "normalized_task_predictions.tsv"
    normalized.to_csv(output_path, sep="\t", index=False)
    print(
        f"normalized {len(normalized)} Sizey predictions for "
        f"{args.workflow} to {output_path}"
    )


if __name__ == "__main__":
    main()
