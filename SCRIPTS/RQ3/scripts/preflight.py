#!/usr/bin/env python3
"""Validate the frozen matched cohort before any model is trained."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    "gatk",
    "minimap2",
    "sarek",
    "seqinspector",
    "taxprofiler",
    "viralmetagenome",
)
EXPECTED_ROWS = {
    "gatk": (150, 350),
    "minimap2": (1800, 4200),
    "sarek": (1792, 4188),
    "seqinspector": (1800, 4200),
    "taxprofiler": (1799, 4199),
    "viralmetagenome": (1800, 4200),
}
SIZEY_COMMIT = "e0dd09f09cd2bf5905c7143792e3500c948eb386"
PATCHED_MODELING_SHA256 = (
    "f8bcfa72447ecacab93b8d3e9ecb05b663270b7906b20208100725741d7c00ae"
)
EXPECTED_CAMP_SOURCE_SHA256 = {
    "common.py": "7bd8867c7a12eabd0287c4aba0d075d10e9b62122495260ad0509f76770af667",
    "history.py": "e199531d6abded01770d1821caeb2b8b64e62cac7a8a5dafbcb5c604cb52e513",
    "modeling.py": PATCHED_MODELING_SHA256,
    "calibration.py": "35c2947867e293f626302b09548cdb58aa6d09662b71b2530bb8f447819739b9",
    "run_design3.py": "14f09d476c013640aa754983098a48f2607ce702d66c020726aa83248a42df6a",
    "train_process_tail.py": "67fe3d1d498d4886f5fcf3e2642f1c55278f521b4a1333d63693b3306ba927bb",
}
REQUIRED_SOURCE_FIELDS = (
    "logical_task_id",
    "workflow",
    "process",
    "split_group_id",
    "decision_time",
    "static_input_bytes",
    "peak_memory_bytes",
    "runtime_seconds",
    "ebpf_read_return_bytes",
    "ebpf_mmap_page_fault_bytes",
    "ebpf_total_consumed_bytes",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fail(message: str) -> None:
    raise SystemExit(f"preflight failed: {message}")


def main() -> None:
    result_root = ROOT / "results" / "seed_1996"
    model_root = ROOT / "models" / "seed_1996"
    if result_root.exists() or model_root.exists():
        fail("fresh model/result directories already exist")
    if (ROOT / "scripts" / "apply_dual_bound.py").exists():
        fail("obsolete dual-bound A/AP floor script is present")

    config_path = ROOT / "config" / "experiment.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["variants"] != ["A", "A+P", "A+P+C"]:
        fail("feature-view contract changed")
    if config["allocation"]["final_contract"] != (
        "apc_only_process_tail_hierarchical_residual"
    ):
        fail("APC-only allocation contract is not frozen")

    source_path = ROOT / "inputs" / "canonical_tasks.tsv"
    source = pd.read_csv(source_path, sep="\t", low_memory=False)
    missing_fields = sorted(set(REQUIRED_SOURCE_FIELDS) - set(source.columns))
    if missing_fields:
        fail(f"source fields missing: {missing_fields}")
    if len(source) != 30478 or source["logical_task_id"].nunique() != 30478:
        fail("canonical cohort must contain 30,478 unique tasks")
    if tuple(sorted(source["workflow"].astype(str).unique())) != WORKFLOWS:
        fail("canonical workflow set changed")
    if source[list(REQUIRED_SOURCE_FIELDS)].isna().any().any():
        missing = source[list(REQUIRED_SOURCE_FIELDS)].isna().sum()
        fail(f"required source values are missing: {missing[missing > 0].to_dict()}")

    expected_consumed = (
        pd.to_numeric(source["ebpf_read_return_bytes"], errors="raise")
        + pd.to_numeric(
            source["ebpf_mmap_page_fault_bytes"], errors="raise"
        )
    )
    actual_consumed = pd.to_numeric(
        source["ebpf_total_consumed_bytes"], errors="raise"
    )
    if not np.array_equal(
        expected_consumed.to_numpy(dtype=np.int64),
        actual_consumed.to_numpy(dtype=np.int64),
    ):
        fail("C is not read-return bytes plus mmap page-fault bytes")

    source_index = source.set_index(
        source["logical_task_id"].astype(str), drop=False
    )
    split_frames = []
    sizey_hashes: dict[str, str] = {}
    split_hashes: dict[str, str] = {}
    for workflow in WORKFLOWS:
        split_path = ROOT / "data" / "split_manifests" / (
            f"{workflow}_1996.tsv"
        )
        split = pd.read_csv(split_path, sep="\t", low_memory=False)
        if split["logical_task_id"].duplicated().any():
            fail(f"{workflow}: duplicate task IDs in split")
        if set(split["role"]) != {"train", "test"}:
            fail(f"{workflow}: split roles are not train/test")
        train_expected, test_expected = EXPECTED_ROWS[workflow]
        counts = split["role"].value_counts().to_dict()
        if counts != {"test": test_expected, "train": train_expected}:
            fail(f"{workflow}: split counts changed: {counts}")
        workflow_ids = set(
            source.loc[
                source["workflow"].astype(str).eq(workflow),
                "logical_task_id",
            ].astype(str)
        )
        if set(split["logical_task_id"].astype(str)) != workflow_ids:
            fail(f"{workflow}: split does not cover canonical workflow rows")
        split_frames.append(split)
        split_hashes[workflow] = sha256(split_path)

        sizey_root = ROOT / "baseline" / "sizey" / workflow
        sizey_path = sizey_root / "normalized_task_predictions.tsv"
        sizey = pd.read_csv(sizey_path, sep="\t", low_memory=False)
        expected_test_ids = set(
            split.loc[split["role"].eq("test"), "logical_task_id"].astype(str)
        )
        observed_ids = set(sizey["logical_task_id"].astype(str))
        if len(sizey) != test_expected or observed_ids != expected_test_ids:
            fail(f"{workflow}: frozen Sizey rows do not match test split")
        ordered_source = source_index.loc[
            sizey["logical_task_id"].astype(str)
        ]
        expected_peak = (
            pd.to_numeric(
                ordered_source["peak_memory_bytes"], errors="raise"
            ).to_numpy(dtype=float)
            / (1024.0**2)
        )
        if not np.allclose(
            expected_peak,
            pd.to_numeric(
                sizey["actual_peak_mib"], errors="raise"
            ).to_numpy(dtype=float),
            rtol=0.0,
            atol=1e-9,
        ):
            fail(f"{workflow}: Sizey target values differ from canonical data")
        sizey_manifest = json.loads(
            (sizey_root / "run_manifest.json").read_text(encoding="utf-8")
        )
        if (
            sizey_manifest.get("sizey_git_commit") != SIZEY_COMMIT
            or sizey_manifest.get("source_modified") is not False
            or sizey_manifest.get("return_code") != 0
        ):
            fail(f"{workflow}: Sizey provenance is not the frozen baseline")
        sizey_hashes[workflow] = sha256(sizey_path)

    all_splits = pd.concat(split_frames, ignore_index=True)
    if len(all_splits) != len(source):
        fail("combined split population differs from canonical cohort")
    if int(all_splits["role"].eq("train").sum()) != 9141:
        fail("combined initial-training population is not 9,141")
    if int(all_splits["role"].eq("test").sum()) != 21337:
        fail("combined test population is not 21,337")

    source_hashes = {}
    for name in (
        "common.py",
        "history.py",
        "modeling.py",
        "calibration.py",
        "run_design3.py",
        "train_process_tail.py",
    ):
        local_path = ROOT / "scripts" / name
        local_hash = sha256(local_path)
        if local_hash != EXPECTED_CAMP_SOURCE_SHA256[name]:
            fail(f"frozen CAMP source snapshot differs: {name}")
        source_hashes[name] = local_hash

    manifest = {
        "status": "passed",
        "experiment": config["experiment_name"],
        "seed": 1996,
        "canonical_rows": len(source),
        "initial_rows": int(all_splits["role"].eq("train").sum()),
        "test_rows": int(all_splits["role"].eq("test").sum()),
        "workflows": list(WORKFLOWS),
        "processes": int(
            source[["workflow", "process"]].drop_duplicates().shape[0]
        ),
        "canonical_sha256": sha256(source_path),
        "split_sha256": split_hashes,
        "sizey_sha256": sizey_hashes,
        "sizey_git_commit": SIZEY_COMMIT,
        "sizey_reexecuted": False,
        "sizey_reuse_reason": (
            "code, cohort, seed, and task split are immutable; reuse avoids "
            "88-150 minute duplicate executions per large workflow"
        ),
        "camp_source_sha256": source_hashes,
        "modeling_compatibility_patch": {
            "scope": "metadata serialization only",
            "reason": (
                "construct a typed empty unseen-process Series before sorting"
            ),
            "prediction_calibration_allocation_semantics_changed": False,
        },
        "consumed_formula": (
            "ebpf_read_return_bytes + ebpf_mmap_page_fault_bytes"
        ),
        "apc_uses_a_or_ap_allocation_floor": False,
    }
    output = ROOT / "manifests" / "preflight_manifest.json"
    output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
