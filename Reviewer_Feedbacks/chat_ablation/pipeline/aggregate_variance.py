#!/usr/bin/env python3
"""Aggregate five-seed paired Access/CAMP variance results."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.stats import t


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parents[1] / "RESULTS" / "RQ2" / "csv"
SPLITS = {
    "30/70": "30_70",
    "50/50": "50_50",
    "70/30": "70_30",
    "85/15": "85_15",
    "90/10": "90_10",
}
SEEDS = [1996, 1997, 1998, 1999, 2000]
ALLOCATOR_METRICS = [
    "underallocations",
    "underallocations_per_1000",
    "first_attempt_coverage_percent",
    "total_requested_gib",
    "request_to_observed_peak_ratio",
    "unused_memory_time_gib_hours",
    "point_median_absolute_percentage_error",
    "point_mean_absolute_error_mib",
]
EFFECT_METRICS = [
    "underallocation_reduction_percent",
    "coverage_gain_percentage_points",
    "request_change_percent",
    "request_to_peak_change_percent",
    "unused_memory_time_change_percent",
    "point_mdape_change_percentage_points",
    "point_mae_change_mib",
    "rescued_by_camp",
    "introduced_by_camp",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def describe(values: Iterable[float]) -> dict[str, float | int]:
    array = np.asarray(list(values), dtype=float)
    if not np.isfinite(array).all() or len(array) < 2:
        raise ValueError("variance summary requires finite repeated values")
    mean = float(array.mean())
    sd = float(array.std(ddof=1))
    sem = sd / math.sqrt(len(array))
    margin = float(t.ppf(0.975, df=len(array) - 1) * sem)
    return {
        "n": int(len(array)),
        "mean": mean,
        "sample_sd": sd,
        "sem": sem,
        "ci95_lower": mean - margin,
        "ci95_upper": mean + margin,
        "minimum": float(array.min()),
        "maximum": float(array.max()),
    }


def percentage_change(camp: float, access: float) -> float:
    if access == 0:
        return math.nan
    return 100.0 * (camp - access) / access


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    repetitions = []
    effects = []
    workflows = []
    policies = []
    population_ids: dict[str, set[str]] = {}
    source_paths = []

    for split, directory in SPLITS.items():
        for seed in SEEDS:
            result_root = (
                ROOT / "results" / f"split_{directory}" / f"seed_{seed}"
            )
            summary_path = result_root / "rq2_summary.json"
            manifest_path = result_root / "run_manifest.json"
            if not summary_path.exists() or not manifest_path.exists():
                raise FileNotFoundError(f"missing completed result: {result_root}")
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if int(summary["seed"]) != seed or int(manifest["seed"]) != seed:
                raise ValueError(f"{split} seed {seed}: seed metadata mismatch")
            expected_members = [
                value + 1000 * (seed - 1996)
                for value in (20260728, 20260729, 20260730)
            ]
            observed_members = manifest["variant_metadata"]["A+P+C"][
                "final"
            ]["model_seeds"]
            if observed_members != expected_members:
                raise ValueError(f"{split} seed {seed}: member seed mismatch")

            split_info = summary["split"]
            for allocator in ("access", "camp"):
                row = {
                    "split": split,
                    "seed": seed,
                    "train_rows": int(split_info["train_rows"]),
                    "test_rows": int(split_info["test_rows"]),
                    "allocator": allocator,
                    **summary["overall"][allocator],
                }
                repetitions.append(row)

            access = summary["overall"]["access"]
            camp = summary["overall"]["camp"]
            comparison = {
                "split": split,
                "seed": seed,
                "train_rows": int(split_info["train_rows"]),
                "test_rows": int(split_info["test_rows"]),
                "access_underallocations": int(access["underallocations"]),
                "camp_underallocations": int(camp["underallocations"]),
                "underallocation_reduction_percent": float(
                    summary["overall"]["underallocation_reduction_percent"]
                ),
                "coverage_gain_percentage_points": float(
                    camp["first_attempt_coverage_percent"]
                    - access["first_attempt_coverage_percent"]
                ),
                "request_change_percent": percentage_change(
                    float(camp["total_requested_gib"]),
                    float(access["total_requested_gib"]),
                ),
                "request_to_peak_change_percent": percentage_change(
                    float(camp["request_to_observed_peak_ratio"]),
                    float(access["request_to_observed_peak_ratio"]),
                ),
                "unused_memory_time_change_percent": percentage_change(
                    float(camp["unused_memory_time_gib_hours"]),
                    float(access["unused_memory_time_gib_hours"]),
                ),
                "point_mdape_change_percentage_points": float(
                    camp["point_median_absolute_percentage_error"]
                    - access["point_median_absolute_percentage_error"]
                ),
                "point_mae_change_mib": float(
                    camp["point_mean_absolute_error_mib"]
                    - access["point_mean_absolute_error_mib"]
                ),
                "rescued_by_camp": int(
                    summary["overall"]["rescued_by_camp"]
                ),
                "introduced_by_camp": int(
                    summary["overall"]["newly_underallocated_by_camp"]
                ),
            }
            effects.append(comparison)

            for workflow, values in summary["per_workflow"].items():
                for allocator in ("access", "camp"):
                    workflows.append(
                        {
                            "split": split,
                            "seed": seed,
                            "workflow": workflow,
                            "allocator": allocator,
                            **values[allocator],
                        }
                    )
            for allocator, policy in summary["selected_policies"].items():
                policies.append(
                    {
                        "split": split,
                        "seed": seed,
                        "allocator": allocator,
                        "base_policy": policy["base_policy"],
                        "residual_quantile": policy["residual_quantile"],
                        "constraint_status": policy["constraint_status"],
                    }
                )

            access_path = (
                result_root / "a_plus_p" / "task_predictions.tsv"
            )
            camp_path = (
                result_root / "a_plus_p_plus_c" / "task_predictions.tsv"
            )
            access_ids = set(
                pd.read_csv(
                    access_path,
                    sep="\t",
                    usecols=["logical_task_id"],
                )["logical_task_id"].astype(str)
            )
            camp_ids = set(
                pd.read_csv(
                    camp_path,
                    sep="\t",
                    usecols=["logical_task_id"],
                )["logical_task_id"].astype(str)
            )
            if access_ids != camp_ids:
                raise ValueError(f"{split} seed {seed}: unpaired tasks")
            if split in population_ids and access_ids != population_ids[split]:
                raise ValueError(f"{split}: holdout differs by seed")
            population_ids[split] = access_ids
            source_paths.extend([summary_path, manifest_path, access_path, camp_path])

    repetition_frame = pd.DataFrame(repetitions)
    effect_frame = pd.DataFrame(effects)
    workflow_frame = pd.DataFrame(workflows)
    policy_frame = pd.DataFrame(policies)
    repetition_frame.to_csv(
        OUTPUT / "repetition_allocator_metrics.csv", index=False
    )
    effect_frame.to_csv(OUTPUT / "repetition_paired_effects.csv", index=False)
    workflow_frame.to_csv(
        OUTPUT / "repetition_per_workflow_metrics.csv", index=False
    )
    policy_frame.to_csv(
        OUTPUT / "repetition_selected_policies.csv", index=False
    )

    long_rows = []
    for (split, allocator), group in repetition_frame.groupby(
        ["split", "allocator"], sort=False
    ):
        for metric in ALLOCATOR_METRICS:
            long_rows.append(
                {
                    "split": split,
                    "scope": allocator,
                    "metric": metric,
                    **describe(group[metric]),
                }
            )
    for split, group in effect_frame.groupby("split", sort=False):
        for metric in EFFECT_METRICS:
            long_rows.append(
                {
                    "split": split,
                    "scope": "camp_minus_access",
                    "metric": metric,
                    **describe(group[metric]),
                }
            )
    variance = pd.DataFrame(long_rows)
    variance.to_csv(OUTPUT / "variance_summary_long.csv", index=False)

    workflow_rows = []
    for keys, group in workflow_frame.groupby(
        ["split", "workflow", "allocator"], sort=False
    ):
        for metric in ALLOCATOR_METRICS:
            workflow_rows.append(
                {
                    "split": keys[0],
                    "workflow": keys[1],
                    "allocator": keys[2],
                    "metric": metric,
                    **describe(group[metric]),
                }
            )
    pd.DataFrame(workflow_rows).to_csv(
        OUTPUT / "per_workflow_variance_summary.csv",
        index=False,
    )

    policy_frequency = (
        policy_frame.groupby(
            [
                "split",
                "allocator",
                "base_policy",
                "residual_quantile",
                "constraint_status",
            ],
            dropna=False,
            sort=False,
        )
        .size()
        .rename("repetitions")
        .reset_index()
    )
    policy_frequency.to_csv(
        OUTPUT / "policy_selection_frequency.csv", index=False
    )

    source_checksums = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in source_paths
    }
    manifest = {
        "status": "complete",
        "splits": list(SPLITS),
        "outer_seeds": SEEDS,
        "repetitions_per_split": len(SEEDS),
        "total_repetitions": len(SPLITS) * len(SEEDS),
        "variance_interpretation": (
            "between-seed sample standard deviation and t-based 95% "
            "confidence interval of the mean; fixed holdout populations"
        ),
        "source_checksums": source_checksums,
    }
    (OUTPUT / "variance_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(variance.to_string(index=False))


if __name__ == "__main__":
    main()
