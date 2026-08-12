#!/usr/bin/env python3
"""Summarize the paired Access versus CAMP RQ2 replay."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binomtest

from common import load_config


ROOT = Path(__file__).resolve().parent
RESULT_ROOT = ROOT / "results" / "seed_1996"
ACCESS_PATH = RESULT_ROOT / "a_plus_p" / "task_predictions.tsv"
CAMP_PATH = RESULT_ROOT / "a_plus_p_plus_c" / "task_predictions.tsv"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--result-root", type=Path)
    parser.add_argument("--train-fraction", type=float)
    parser.add_argument("--test-fraction", type=float)
    parser.add_argument("--expected-train-rows", type=int)
    parser.add_argument("--expected-test-rows", type=int)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_paired() -> pd.DataFrame:
    columns = [
        "logical_task_id",
        "workflow",
        "process",
        "raw_prediction_mib",
        "first_allocation_mib",
        "actual_peak_mib",
        "runtime_seconds",
        "initial_oom",
    ]
    access_header = pd.read_csv(ACCESS_PATH, sep="\t", nrows=0).columns
    camp_header = pd.read_csv(CAMP_PATH, sep="\t", nrows=0).columns
    position = (
        "replay_position"
        if "replay_position" in access_header and "replay_position" in camp_header
        else "experiment_position"
    )
    access = pd.read_csv(ACCESS_PATH, sep="\t", usecols=[*columns, position])
    camp = pd.read_csv(CAMP_PATH, sep="\t", usecols=[*columns, position])
    if position != "replay_position":
        access = access.rename(columns={position: "replay_position"})
        camp = camp.rename(columns={position: "replay_position"})
    paired = access.merge(
        camp,
        on=["logical_task_id", "workflow", "process", "replay_position"],
        how="inner",
        validate="one_to_one",
        suffixes=("_access", "_camp"),
    )
    if len(paired) != len(access) or len(paired) != len(camp):
        raise ValueError("Access and CAMP task populations differ")
    for column in ("actual_peak_mib", "runtime_seconds"):
        left = pd.to_numeric(paired[f"{column}_access"], errors="raise")
        right = pd.to_numeric(paired[f"{column}_camp"], errors="raise")
        if not np.allclose(left, right, equal_nan=True):
            raise ValueError(f"paired {column} values differ")
        paired[column] = left
    paired["access_underallocated"] = (
        paired["first_allocation_mib_access"]
        < paired["actual_peak_mib"]
    )
    paired["camp_underallocated"] = (
        paired["first_allocation_mib_camp"]
        < paired["actual_peak_mib"]
    )
    return paired


def metrics(
    rows: pd.DataFrame,
    *,
    request_column: str,
    point_column: str,
    under_column: str,
) -> dict[str, float | int]:
    actual = rows["actual_peak_mib"].to_numpy(dtype=float)
    request = rows[request_column].to_numpy(dtype=float)
    point = rows[point_column].to_numpy(dtype=float)
    runtime = (
        pd.to_numeric(rows["runtime_seconds"], errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype=float)
    )
    under = rows[under_column].to_numpy(dtype=bool)
    return {
        "rows": int(len(rows)),
        "underallocations": int(under.sum()),
        "underallocations_per_1000": float(1000.0 * under.mean()),
        "first_attempt_coverage_percent": float(100.0 * (~under).mean()),
        "total_requested_gib": float(request.sum() / 1024.0),
        "request_to_observed_peak_ratio": float(request.sum() / actual.sum()),
        "unused_memory_time_gib_hours": float(
            np.sum(np.maximum(request - actual, 0.0) * runtime)
            / 1024.0
            / 3600.0
        ),
        "point_median_absolute_percentage_error": float(
            100.0 * np.median(np.abs(point - actual) / actual)
        ),
        "point_mean_absolute_error_mib": float(
            np.mean(np.abs(point - actual))
        ),
    }


def summarize_group(rows: pd.DataFrame) -> dict[str, object]:
    access = metrics(
        rows,
        request_column="first_allocation_mib_access",
        point_column="raw_prediction_mib_access",
        under_column="access_underallocated",
    )
    camp = metrics(
        rows,
        request_column="first_allocation_mib_camp",
        point_column="raw_prediction_mib_camp",
        under_column="camp_underallocated",
    )
    access_under = int(access["underallocations"])
    camp_under = int(camp["underallocations"])
    return {
        "rows": int(len(rows)),
        "access": access,
        "camp": camp,
        "underallocation_absolute_change": camp_under - access_under,
        "underallocation_reduction_percent": (
            float(100.0 * (access_under - camp_under) / access_under)
            if access_under
            else None
        ),
        "rescued_by_camp": int(
            (
                rows["access_underallocated"]
                & ~rows["camp_underallocated"]
            ).sum()
        ),
        "newly_underallocated_by_camp": int(
            (
                ~rows["access_underallocated"]
                & rows["camp_underallocated"]
            ).sum()
        ),
        "underallocated_by_both": int(
            (
                rows["access_underallocated"]
                & rows["camp_underallocated"]
            ).sum()
        ),
    }


def main() -> None:
    global RESULT_ROOT, ACCESS_PATH, CAMP_PATH
    args = arguments()
    RESULT_ROOT = args.result_root or ROOT / "results" / f"seed_{args.seed}"
    ACCESS_PATH = RESULT_ROOT / "a_plus_p" / "task_predictions.tsv"
    CAMP_PATH = (
        RESULT_ROOT / "a_plus_p_plus_c" / "task_predictions.tsv"
    )
    config = load_config()
    train_fraction = float(args.train_fraction if args.train_fraction is not None else config.get("train_fraction", 0.85))
    test_fraction = float(args.test_fraction if args.test_fraction is not None else config.get("test_fraction", 0.15))
    train_rows = int(args.expected_train_rows if args.expected_train_rows is not None else config["expected_train_rows"])
    test_rows = int(args.expected_test_rows if args.expected_test_rows is not None else config["expected_test_rows"])
    paired = load_paired()
    if len(paired) != test_rows:
        raise ValueError(
            f"paired rows={len(paired)} do not match expected={test_rows}"
        )
    overall = summarize_group(paired)
    discordant = (
        int(overall["rescued_by_camp"])
        + int(overall["newly_underallocated_by_camp"])
    )
    mcnemar = binomtest(
        int(overall["newly_underallocated_by_camp"]),
        n=discordant,
        p=0.5,
        alternative="two-sided",
    )
    per_workflow = {
        str(workflow): summarize_group(group)
        for workflow, group in paired.groupby("workflow", sort=True)
    }
    policies = {}
    for name, directory in (
        ("access", "a_plus_p"),
        ("camp", "a_plus_p_plus_c"),
    ):
        with (
            RESULT_ROOT / directory / "selected_policy.json"
        ).open(encoding="utf-8") as handle:
            policies[name] = json.load(handle)

    summary = {
        "experiment": "CAMP RQ2 split sensitivity",
        "seed": args.seed,
        "split": {
            "train_fraction": train_fraction,
            "test_fraction": test_fraction,
            "train_rows": train_rows,
            "test_rows": test_rows,
            "strategy": (
                "workflow-stratified chronological split-group prefix"
            ),
        },
        "comparison": (
            "complete Access allocator (A+P) versus complete CAMP "
            "allocator (A+P+C)"
        ),
        "overall": overall,
        "paired_mcnemar_exact": {
            "discordant_pairs": discordant,
            "camp_rescues": int(overall["rescued_by_camp"]),
            "camp_introductions": int(
                overall["newly_underallocated_by_camp"]
            ),
            "two_sided_p_value": float(mcnemar.pvalue),
        },
        "per_workflow": per_workflow,
        "selected_policies": policies,
        "sources": {
            "access_predictions": str(ACCESS_PATH),
            "access_sha256": sha256(ACCESS_PATH),
            "camp_predictions": str(CAMP_PATH),
            "camp_sha256": sha256(CAMP_PATH),
        },
    }
    with (RESULT_ROOT / "rq2_summary.json").open(
        "w", encoding="utf-8"
    ) as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    overall_rows = []
    for allocator in ("access", "camp"):
        overall_rows.append(
            {
                "allocator": allocator,
                **overall[allocator],
            }
        )
    pd.DataFrame(overall_rows).to_csv(
        RESULT_ROOT / "overall_metrics.csv", index=False
    )

    workflow_rows = []
    for workflow, values in per_workflow.items():
        for allocator in ("access", "camp"):
            workflow_rows.append(
                {
                    "workflow": workflow,
                    "allocator": allocator,
                    **values[allocator],
                }
            )
    pd.DataFrame(workflow_rows).to_csv(
        RESULT_ROOT / "per_workflow_metrics.csv", index=False
    )
    paired.to_csv(
        RESULT_ROOT / "paired_rq2_outcomes.tsv", sep="\t", index=False
    )

    access = overall["access"]
    camp = overall["camp"]
    reduction = overall["underallocation_reduction_percent"]
    request_change = (
        100.0
        * (
            camp["total_requested_gib"]
            - access["total_requested_gib"]
        )
        / access["total_requested_gib"]
    )
    waste_change = (
        100.0
        * (
            camp["unused_memory_time_gib_hours"]
            - access["unused_memory_time_gib_hours"]
        )
        / access["unused_memory_time_gib_hours"]
    )
    train_percent = int(round(100.0 * train_fraction))
    test_percent = int(round(100.0 * test_fraction))
    report = f"""# RQ2 {train_percent}/{test_percent} Seed {args.seed} Result

This experiment retrains the unchanged Design 3 pipeline on the earliest
{train_percent}% of split groups within each workflow and replays the
remaining {test_percent}%. The development population has {train_rows:,}
tasks and the holdout has {test_rows:,}.

| Metric | Access | CAMP |
|---|---:|---:|
| Underallocations | {access['underallocations']:,} | {camp['underallocations']:,} |
| First-attempt coverage | {access['first_attempt_coverage_percent']:.2f}% | {camp['first_attempt_coverage_percent']:.2f}% |
| Underallocations per 1,000 | {access['underallocations_per_1000']:.2f} | {camp['underallocations_per_1000']:.2f} |
| Total requested memory | {access['total_requested_gib']:.2f} GiB | {camp['total_requested_gib']:.2f} GiB |
| Request/peak | {access['request_to_observed_peak_ratio']:.2f}x | {camp['request_to_observed_peak_ratio']:.2f}x |
| Unused memory-time | {access['unused_memory_time_gib_hours']:.2f} GiB-h | {camp['unused_memory_time_gib_hours']:.2f} GiB-h |
| Point MdAPE | {access['point_median_absolute_percentage_error']:.2f}% | {camp['point_median_absolute_percentage_error']:.2f}% |
| Point MAE | {access['point_mean_absolute_error_mib']:.2f} MiB | {camp['point_mean_absolute_error_mib']:.2f} MiB |

CAMP changes underallocations by {reduction:.2f}% relative to Access, rescues
{overall['rescued_by_camp']:,} Access failures, newly underallocates
{overall['newly_underallocated_by_camp']:,} tasks, changes total request by
{request_change:.2f}%, and changes unused memory-time by {waste_change:.2f}%.
The exact paired McNemar p-value is {mcnemar.pvalue:.3e}.

This is a split-sensitivity result, not a replacement headline result. It is
the complete-allocator comparison used by RQ2, not a strict feature-only
ablation.
"""
    (RESULT_ROOT / "RESULTS.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
