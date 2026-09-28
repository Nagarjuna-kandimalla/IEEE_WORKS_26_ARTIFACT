#!/usr/bin/env python3
"""Create immutable comparison cohorts, Sizey inputs, and matched splits."""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.model_selection import train_test_split

from common import GIB, ROOT, file_sha256, load_config, read_tsv, write_json


REQUIRED_COLUMNS = {
    "logical_task_id",
    "workflow",
    "process",
    "version",
    "system_config_id",
    "static_input_bytes",
    "peak_memory_bytes",
    "runtime_seconds",
    "ebpf_read_return_bytes",
    "ebpf_mmap_page_fault_bytes",
    "ebpf_total_consumed_bytes",
}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cohorts",
        nargs="+",
        choices=("primary", "extended"),
        default=("primary", "extended"),
    )
    return parser.parse_args()


def validate_source(frame: pd.DataFrame) -> None:
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"source data is missing columns: {missing}")
    if frame["logical_task_id"].duplicated().any():
        raise ValueError("logical_task_id is not unique")
    if frame["static_input_bytes"].isna().any():
        raise ValueError("static_input_bytes contains missing values")
    if (pd.to_numeric(frame["static_input_bytes"], errors="coerce") <= 0).any():
        raise ValueError("static_input_bytes must be positive")
    if frame["peak_memory_bytes"].isna().any():
        raise ValueError("peak_memory_bytes contains missing values")
    if (pd.to_numeric(frame["peak_memory_bytes"], errors="coerce") <= 0).any():
        raise ValueError("peak_memory_bytes must be positive")
    expected = (
        pd.to_numeric(frame["ebpf_read_return_bytes"], errors="coerce")
        + pd.to_numeric(frame["ebpf_mmap_page_fault_bytes"], errors="coerce")
    )
    observed = pd.to_numeric(frame["ebpf_total_consumed_bytes"], errors="coerce")
    if not np.array_equal(expected.to_numpy(), observed.to_numpy()):
        raise ValueError("eBPF consumed-data formula does not hold")


def eligible_rows(
    source: pd.DataFrame,
    *,
    require_runtime: bool,
    minimum_process_rows: int,
) -> pd.DataFrame:
    frame = source.copy()
    frame["runtime_seconds"] = pd.to_numeric(
        frame["runtime_seconds"], errors="coerce"
    )
    if require_runtime:
        frame = frame.loc[
            frame["runtime_seconds"].notna() & (frame["runtime_seconds"] > 0)
        ].copy()
    group_columns = ["workflow", "process"]
    counts = frame.groupby(group_columns)["logical_task_id"].transform("size")
    frame = frame.loc[counts >= minimum_process_rows].copy()
    return frame.reset_index(drop=True)


def sizey_input(frame: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(
        {
            "logical_task_id": frame["logical_task_id"].astype(str),
            "process": frame["process"].astype(str),
            "rchar": pd.to_numeric(frame["static_input_bytes"], errors="raise"),
            "rss": pd.to_numeric(frame["peak_memory_bytes"], errors="raise"),
            "peak_rss": pd.to_numeric(frame["peak_memory_bytes"], errors="raise"),
            "realtime": pd.to_numeric(
                frame["runtime_seconds"], errors="coerce"
            )
            * 1000.0,
            # Required by Sizey's loader but unused by its default Sizey path.
            "memory": pd.to_numeric(
                frame["worker_memory_gib"], errors="coerce"
            ).fillna(256.0)
            * GIB,
        }
    )
    return result


def split_manifest(
    adapter: pd.DataFrame,
    workflow: str,
    seed: int,
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for process, group in adapter.groupby("process", sort=False):
        x = group[["rchar"]]
        y = group["rss"]
        runtime = group["realtime"]
        memory = group["memory"]
        x_train, x_test, _, _, _, _, _, _ = train_test_split(
            x,
            y,
            runtime,
            memory,
            test_size=0.7,
            random_state=seed,
        )
        train_indices = set(int(index) for index in x_train.index)
        test_position = {
            int(index): position
            for position, index in enumerate(x_test.index)
        }
        for index, row in group.iterrows():
            adapter_index = int(index)
            role = "train" if adapter_index in train_indices else "test"
            records.append(
                {
                    "logical_task_id": str(row["logical_task_id"]),
                    "workflow": workflow,
                    "process": str(process),
                    "seed": seed,
                    "role": role,
                    "replay_position": (
                        pd.NA
                        if role == "train"
                        else test_position[adapter_index]
                    ),
                    "adapter_row_index": adapter_index,
                }
            )
    manifest = pd.DataFrame.from_records(records)
    return manifest.sort_values(
        ["process", "role", "replay_position", "adapter_row_index"],
        na_position="first",
    ).reset_index(drop=True)


def prepare_cohort(
    source: pd.DataFrame,
    cohort: str,
    config: dict[str, object],
) -> dict[str, object]:
    cohort_key = (
        "primary_cohort" if cohort == "primary" else "extended_prediction_cohort"
    )
    cohort_config = config[cohort_key]
    frame = eligible_rows(
        source,
        require_runtime=bool(cohort_config["require_runtime"]),
        minimum_process_rows=int(cohort_config["minimum_process_rows"]),
    )
    expected_rows = int(cohort_config["expected_rows"])
    if len(frame) != expected_rows:
        raise ValueError(
            f"{cohort} cohort has {len(frame)} rows; expected {expected_rows}"
        )

    generated = ROOT / "data" / "generated" / cohort
    generated.mkdir(parents=True, exist_ok=True)
    frame.to_csv(generated / "canonical_tasks.tsv", sep="\t", index=False)

    input_root = ROOT / "data" / "sizey_inputs" / cohort
    split_root = ROOT / "data" / "split_manifests" / cohort
    input_root.mkdir(parents=True, exist_ok=True)
    split_root.mkdir(parents=True, exist_ok=True)

    workflow_summary: dict[str, object] = {}
    seeds = config["split"]["robustness_seeds"]
    for workflow, workflow_frame in frame.groupby("workflow", sort=True):
        workflow_frame = workflow_frame.reset_index(drop=True)
        adapter = sizey_input(workflow_frame)
        input_path = input_root / f"trace_{workflow}.csv"
        adapter.to_csv(input_path, index=False)

        seed_summary: dict[str, object] = {}
        for seed_value in seeds:
            seed = int(seed_value)
            manifest = split_manifest(adapter, str(workflow), seed)
            split_path = split_root / f"{workflow}_{seed}.tsv"
            manifest.to_csv(split_path, sep="\t", index=False)
            role_counts = manifest["role"].value_counts().to_dict()
            seed_summary[str(seed)] = {
                "train": int(role_counts.get("train", 0)),
                "test": int(role_counts.get("test", 0)),
                "manifest": str(split_path),
            }

        workflow_summary[str(workflow)] = {
            "rows": int(len(workflow_frame)),
            "processes": int(workflow_frame["process"].nunique()),
            "sizey_input": str(input_path),
            "seeds": seed_summary,
        }

    return {
        "rows": int(len(frame)),
        "workflows": int(frame["workflow"].nunique()),
        "processes": int(frame[["workflow", "process"]].drop_duplicates().shape[0]),
        "runtime_missing": int(frame["runtime_seconds"].isna().sum()),
        "workflow_summary": workflow_summary,
    }


def main() -> None:
    args = arguments()
    config = load_config()
    source_path = ROOT / config["source_data"]
    source = read_tsv(source_path)
    validate_source(source)

    cohort_results = {
        cohort: prepare_cohort(source, cohort, config)
        for cohort in args.cohorts
    }
    manifest = {
        "source_path": str(source_path),
        "source_sha256": file_sha256(source_path),
        "source_rows": int(len(source)),
        "source_workflows": int(source["workflow"].nunique()),
        "sizey_source": str(config["sizey_source"]),
        "sizey_git_commit": "e0dd09f09cd2bf5905c7143792e3500c948eb386",
        "python": sys.version,
        "platform": platform.platform(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "sklearn": sklearn.__version__,
        "cohorts": cohort_results,
    }
    write_json(ROOT / "data" / "source_manifest.json", manifest)
    print(pd.DataFrame(
        [
            {
                "cohort": cohort,
                "rows": values["rows"],
                "workflows": values["workflows"],
                "processes": values["processes"],
                "runtime_missing": values["runtime_missing"],
            }
            for cohort, values in cohort_results.items()
        ]
    ).to_string(index=False))


if __name__ == "__main__":
    main()
