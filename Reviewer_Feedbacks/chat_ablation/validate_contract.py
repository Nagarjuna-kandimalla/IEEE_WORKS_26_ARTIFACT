#!/usr/bin/env python3
"""Check the packaged ablation cohort, feature views, and retained predictions."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "pipeline"))
from common import MIB, experiment_sort, load_experiment_rows
from modeling import (
    FORBIDDEN_CURRENT_COLUMNS, NUMERIC_CH, NUMERIC_CHAT, feature_columns,
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    rows = load_experiment_rows(1996)
    train = rows.loc[rows.role == "train"]
    test = experiment_sort(rows.loc[rows.role == "test"]).reset_index(drop=True)
    require(len(rows) == 33516 and len(train) == 28490 and len(test) == 5026,
            "Population or split size differs from the reference")
    require(rows.logical_task_id.is_unique, "Duplicate task identities")
    require(not (set(train.logical_task_id) & set(test.logical_task_id)),
            "Development and test tasks overlap")
    require(rows.workflow.nunique() == 6, "Expected six workflows")
    ids = test.logical_task_id.astype(str).to_numpy()
    peaks = pd.to_numeric(test.peak_memory_bytes).to_numpy(float) / MIB
    runtime = pd.to_numeric(test.runtime_seconds, errors="coerce").to_numpy(float)
    require(int(np.isnan(runtime).sum()) == 449, "Missing-runtime population differs")
    test_features = pd.read_csv(
        ROOT / "reference_predictions/seed_1996/test_causal_features.tsv",
        sep="\t", low_memory=False,
    )
    require(np.array_equal(test_features.logical_task_id.astype(str), ids),
            "Causal test features differ from split identities/order")

    variants = [
        ("A+P", "A+P", "a_plus_p_final_global_base", 85, False, False),
        ("A+P+CH", "A+P+CH", "a_plus_p_plus_ch", 46, True, False),
        ("A+P+CHAT", "A+P+CHAT", "a_plus_p_plus_chat", 52, False, True),
        ("A+P+CH+CHAT", "A+P+C", "a_plus_p_plus_c", 30, True, True),
    ]
    checked = {}
    for label, view, stem, count, has_ch, has_chat in variants:
        numeric, categorical = feature_columns(view)
        selected = set(numeric) | set(categorical)
        require(not (selected & FORBIDDEN_CURRENT_COLUMNS),
                f"Current-row outcome selected as feature: {label}")
        for columns, enabled in ((NUMERIC_CH, has_ch), (NUMERIC_CHAT, has_chat)):
            require((set(columns) <= selected) if enabled else not (set(columns) & selected),
                    f"Feature membership mismatch: {label}")
        frame = pd.read_csv(
            ROOT / "reference_predictions/seed_1996" / stem / "task_predictions.tsv",
            sep="\t", low_memory=False,
        )
        require(frame.logical_task_id.is_unique and
                np.array_equal(frame.logical_task_id.astype(str), ids),
                f"Task identities/order mismatch: {label}")
        require(np.allclose(frame.actual_peak_mib, peaks, rtol=1e-12, atol=1e-10),
                f"Recorded peak mismatch: {label}")
        require(np.allclose(pd.to_numeric(frame.runtime_seconds, errors="coerce"),
                            runtime, rtol=1e-12, atol=1e-10, equal_nan=True),
                f"Runtime mismatch: {label}")
        requests = frame.first_allocation_mib.to_numpy(float)
        require(np.isfinite(requests).all() and (requests > 0).all(),
                f"Invalid requests: {label}")
        under = requests < peaks
        require(int(under.sum()) == count, f"Underallocation count mismatch: {label}")
        flags = frame.initial_oom.astype(str).str.lower().eq("true").to_numpy()
        require(np.array_equal(flags, under), f"Recorded OOM indicator mismatch: {label}")
        checked[label] = {"rows": len(frame), "underallocations": int(under.sum())}

    result = {
        "status": "passed", "development_rows": len(train), "test_rows": len(test),
        "missing_runtime_rows": 449, "seed": 1996, "variants": checked,
        "verification_scope": "packaged inputs, feature membership, and retained predictions",
        "model_training_performed": False,
    }
    output = ROOT / "results/contract_validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print("Contract passed: 33,516 tasks, disjoint 28,490/5,026 split, paired peaks/runtimes,")
    print("four feature views, and native underallocations 85 / 46 / 52 / 30.")


if __name__ == "__main__":
    main()
