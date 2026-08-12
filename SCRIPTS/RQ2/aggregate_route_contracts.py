#!/usr/bin/env python3
"""Compare uniform and legacy-mixed CAMP routing across repeated seeds."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

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
CONTRACTS = ("uniform_global_tail", "uniform_process_tail", "legacy_mixed")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def describe(values: pd.Series) -> dict[str, float | int]:
    array = values.to_numpy(dtype=float)
    mean = float(array.mean())
    sd = float(array.std(ddof=1))
    margin = float(
        t.ppf(0.975, len(array) - 1) * sd / math.sqrt(len(array))
    )
    return {
        "n": int(len(array)),
        "mean": mean,
        "sample_sd": sd,
        "ci95_lower": mean - margin,
        "ci95_upper": mean + margin,
        "minimum": float(array.min()),
        "maximum": float(array.max()),
    }


def metrics(rows: pd.DataFrame, prefix: str) -> dict[str, float | int]:
    actual = rows["actual_peak_mib"].to_numpy(dtype=float)
    request = rows[f"first_allocation_mib_{prefix}"].to_numpy(dtype=float)
    point = rows[f"raw_prediction_mib_{prefix}"].to_numpy(dtype=float)
    runtime = (
        pd.to_numeric(rows["runtime_seconds"], errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype=float)
    )
    under = request < actual
    return {
        "underallocations": int(under.sum()),
        "underallocations_per_1000": float(1000.0 * under.mean()),
        "coverage_percent": float(100.0 * (~under).mean()),
        "total_requested_gib": float(request.sum() / 1024.0),
        "request_to_peak_ratio": float(request.sum() / actual.sum()),
        "unused_memory_time_gib_hours": float(
            np.sum(np.maximum(request - actual, 0.0) * runtime)
            / 1024.0
            / 3600.0
        ),
        "point_mdape_percent": float(
            100.0 * np.median(np.abs(point - actual) / actual)
        ),
    }


def paired_rows(access_path: Path, camp_path: Path) -> pd.DataFrame:
    columns = [
        "logical_task_id",
        "workflow",
        "process",
        "replay_position",
        "raw_prediction_mib",
        "first_allocation_mib",
        "actual_peak_mib",
        "runtime_seconds",
    ]
    access = pd.read_csv(access_path, sep="\t", usecols=columns)
    camp = pd.read_csv(camp_path, sep="\t", usecols=columns)
    paired = access.merge(
        camp,
        on=["logical_task_id", "workflow", "process", "replay_position"],
        validate="one_to_one",
        suffixes=("_access", "_camp"),
    )
    if len(paired) != len(access) or len(paired) != len(camp):
        raise ValueError("route comparison contains unpaired tasks")
    for column in ("actual_peak_mib", "runtime_seconds"):
        left = paired[f"{column}_access"]
        right = paired[f"{column}_camp"]
        if not np.allclose(left, right, equal_nan=True):
            raise ValueError(f"paired {column} differs")
        paired[column] = left
    return paired


def percent_change(camp: float, access: float) -> float:
    return 100.0 * (camp - access) / access


def main() -> None:
    rows = []
    source_paths: set[Path] = set()
    for split, directory in SPLITS.items():
        for seed in SEEDS:
            result_root = (
                ROOT / "results" / f"split_{directory}" / f"seed_{seed}"
            )
            access_path = result_root / "a_plus_p" / "task_predictions.tsv"
            camp_paths = {
                "uniform_global_tail": (
                    result_root
                    / "a_plus_p_plus_c_global_base"
                    / "task_predictions.tsv"
                ),
                "uniform_process_tail": (
                    result_root
                    / "a_plus_p_plus_c"
                    / "task_predictions.tsv"
                ),
                "legacy_mixed": (
                    result_root
                    / (
                        "a_plus_p_plus_c"
                        if split == "85/15"
                        else "a_plus_p_plus_c_global_base"
                    )
                    / "task_predictions.tsv"
                ),
            }
            source_paths.add(access_path)
            source_paths.update(camp_paths.values())
            for contract, camp_path in camp_paths.items():
                paired = paired_rows(access_path, camp_path)
                access = metrics(paired, "access")
                camp = metrics(paired, "camp")
                rows.append(
                    {
                        "split": split,
                        "seed": seed,
                        "contract": contract,
                        "test_rows": int(len(paired)),
                        "access_underallocations": access["underallocations"],
                        "camp_underallocations": camp["underallocations"],
                        "underallocation_reduction_percent": (
                            100.0
                            * (
                                access["underallocations"]
                                - camp["underallocations"]
                            )
                            / access["underallocations"]
                        ),
                        "coverage_gain_percentage_points": (
                            camp["coverage_percent"]
                            - access["coverage_percent"]
                        ),
                        "request_change_percent": percent_change(
                            camp["total_requested_gib"],
                            access["total_requested_gib"],
                        ),
                        "request_to_peak_change_percent": percent_change(
                            camp["request_to_peak_ratio"],
                            access["request_to_peak_ratio"],
                        ),
                        "unused_memory_time_change_percent": percent_change(
                            camp["unused_memory_time_gib_hours"],
                            access["unused_memory_time_gib_hours"],
                        ),
                        "point_mdape_change_percentage_points": (
                            camp["point_mdape_percent"]
                            - access["point_mdape_percent"]
                        ),
                    }
                )
    repetitions = pd.DataFrame(rows)
    repetitions.to_csv(
        OUTPUT / "route_contract_repetition_effects.csv",
        index=False,
    )

    metrics_to_summarize = [
        "access_underallocations",
        "camp_underallocations",
        "underallocation_reduction_percent",
        "coverage_gain_percentage_points",
        "request_change_percent",
        "request_to_peak_change_percent",
        "unused_memory_time_change_percent",
        "point_mdape_change_percentage_points",
    ]
    summaries = []
    for keys, group in repetitions.groupby(
        ["contract", "split"], sort=False
    ):
        for metric in metrics_to_summarize:
            summaries.append(
                {
                    "contract": keys[0],
                    "split": keys[1],
                    "metric": metric,
                    **describe(group[metric]),
                }
            )
    summary = pd.DataFrame(summaries)
    summary.to_csv(
        OUTPUT / "route_contract_variance_summary.csv",
        index=False,
    )

    status = {
        "status": "complete",
        "contracts": {
            "uniform_global_tail": (
                "same global-tail A+P+C architecture at all splits"
            ),
            "uniform_process_tail": (
                "same final APC-only per-process tail architecture at all "
                "splits; no Access allocation floor"
            ),
            "legacy_mixed": (
                "global tail at 30/70, 50/50, 70/30 and 90/10; process tail "
                "at 85/15; diagnostic only and invalid for paper claims"
            ),
        },
        "seeds": SEEDS,
        "source_checksums": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in sorted(source_paths)
        },
    }
    (OUTPUT / "route_contract_manifest.json").write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
