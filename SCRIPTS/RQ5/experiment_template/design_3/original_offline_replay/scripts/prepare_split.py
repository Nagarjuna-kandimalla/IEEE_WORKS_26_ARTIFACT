#!/usr/bin/env python3
"""Adapt the original train/calibration/test manifest for Design 3."""

from __future__ import annotations

import json

import pandas as pd

from common import ROOT, file_sha256, load_config, resolve_config_path, write_json


def main() -> None:
    config = load_config()
    source_path = resolve_config_path(config["source_data"])
    split_path = resolve_config_path(config["source_split_manifest"])
    source = pd.read_csv(
        source_path,
        sep="\t",
        usecols=[
            "logical_task_id",
            "workflow",
            "process",
            "decision_time",
        ],
        low_memory=False,
    )
    split = pd.read_csv(split_path, sep="\t", low_memory=False)
    if split["logical_task_id"].duplicated().any():
        raise ValueError("original split contains duplicate task IDs")
    frame = source.merge(
        split[["logical_task_id", "split"]],
        on="logical_task_id",
        how="inner",
        validate="one_to_one",
    )
    if len(frame) != len(source):
        raise ValueError("original split does not cover the source")
    counts = frame["split"].value_counts().to_dict()
    expected = {
        "train": int(config["expected_original_train_rows"]),
        "calibration": int(config["expected_original_calibration_rows"]),
        "test": int(config["expected_test_rows"]),
    }
    if counts != expected:
        raise ValueError(f"original split mismatch: {counts} != {expected}")

    frame["role"] = frame["split"].map(
        {"train": "train", "calibration": "train", "test": "test"}
    )
    ordered = frame.sort_values(
        ["decision_time", "workflow", "process", "logical_task_id"],
        kind="mergesort",
    )
    replay_positions = pd.Series(
        range(len(ordered)),
        index=ordered.index,
        dtype=int,
    )
    frame["replay_position"] = replay_positions.reindex(frame.index)

    output_root = ROOT / "data" / "split_manifests"
    output_root.mkdir(parents=True, exist_ok=True)
    output_files = {}
    for workflow, group in frame.groupby("workflow", sort=True):
        path = output_root / f"{workflow}_{config['seed']}.tsv"
        group[
            ["logical_task_id", "role", "replay_position"]
        ].to_csv(path, sep="\t", index=False)
        output_files[str(workflow)] = {
            "path": str(path),
            "rows": int(len(group)),
            "sha256": file_sha256(path),
        }

    manifest = {
        "source_data": str(source_path),
        "source_sha256": file_sha256(source_path),
        "original_split_manifest": str(split_path),
        "original_split_sha256": file_sha256(split_path),
        "original_split_counts": counts,
        "design3_roles": frame["role"].value_counts().to_dict(),
        "role_mapping": {
            "train": "train",
            "calibration": "train",
            "test": "test",
        },
        "test_task_ids_unchanged": True,
        "output_files": output_files,
    }
    write_json(ROOT / "data" / "split_adapter_manifest.json", manifest)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
