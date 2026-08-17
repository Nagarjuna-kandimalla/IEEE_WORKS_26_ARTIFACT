#!/usr/bin/env python3
"""Replace mixed-scale global tails with per-process LightGBM ensembles."""

from __future__ import annotations

import argparse
import json

import joblib
import numpy as np
import pandas as pd

from calibration import apply_policy_sequentially, select_allocation_policy
from common import MIB, ROOT, load_config, write_json
from modeling import (
    crossfit_per_process_predictions,
    fit_final_per_process_predictions,
    materialize_model_features,
)
from run_design3 import prediction_frame


TAIL_QUANTILES = (0.9, 0.95, 0.99, 0.995)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1996)
    parser.add_argument("--n-jobs", type=int, default=8)
    parser.add_argument(
        "--variant",
        choices=("A", "A+P", "A+P+C"),
        default="A+P+C",
    )
    return parser.parse_args()


def main() -> None:
    args = arguments()
    result_root = ROOT / "results" / f"seed_{args.seed}"
    model_root = ROOT / "models" / f"seed_{args.seed}"
    initial = pd.read_csv(
        result_root / "initial_causal_features.tsv",
        sep="\t",
        low_memory=False,
    )
    test = pd.read_csv(
        result_root / "test_causal_features.tsv",
        sep="\t",
        low_memory=False,
    )
    c_initial = pd.read_csv(
        result_root / "c_hat_initial_oof.tsv", sep="\t"
    )
    c_test = pd.read_csv(result_root / "c_hat_test.tsv", sep="\t")
    if not (
        initial["logical_task_id"].astype(str).to_numpy()
        == c_initial["logical_task_id"].astype(str).to_numpy()
    ).all():
        raise ValueError("initial C-hat rows are not aligned")
    if not (
        test["logical_task_id"].astype(str).to_numpy()
        == c_test["logical_task_id"].astype(str).to_numpy()
    ).all():
        raise ValueError("test C-hat rows are not aligned")
    initial["c_hat_bytes"] = c_initial["c_hat_bytes"].to_numpy(dtype=float)
    test["c_hat_bytes"] = c_test["c_hat_bytes"].to_numpy(dtype=float)
    initial_features = materialize_model_features(initial)
    test_features = materialize_model_features(test)
    target_mib = (
        pd.to_numeric(
            initial_features[load_config()["rss_target"]], errors="raise"
        ).to_numpy(dtype=float)
        / MIB
    )

    variant = args.variant
    variant_path = {
        "A": "a",
        "A+P": "a_plus_p",
        "A+P+C": "a_plus_p_plus_c",
    }[variant]
    variant_root = result_root / variant_path
    oof_frame = pd.read_csv(
        variant_root / "initial_oof_predictions.tsv", sep="\t"
    )
    task_frame = pd.read_csv(
        variant_root / "task_predictions.tsv", sep="\t"
    )
    fallback_oof = {
        quantile: oof_frame[
            f"q{int(round(quantile * 1000)):03d}_oof_mib"
        ].to_numpy(dtype=float)
        for quantile in TAIL_QUANTILES
    }
    fallback_test = {
        quantile: task_frame[
            f"q{int(round(quantile * 1000)):03d}_prediction_mib"
        ].to_numpy(dtype=float)
        for quantile in TAIL_QUANTILES
    }
    checkpoint_path = variant_root / "process_tail_oof_checkpoint.npz"
    if checkpoint_path.exists():
        checkpoint = np.load(checkpoint_path)
        checkpoint_ids = checkpoint["logical_task_id"].astype(str)
        current_ids = initial_features["logical_task_id"].astype(str).to_numpy()
        if not np.array_equal(checkpoint_ids, current_ids):
            raise ValueError("process-tail OOF checkpoint rows differ")
        tail_oof = {
            quantile: checkpoint[
                f"q{int(round(quantile * 1000)):03d}"
            ]
            for quantile in TAIL_QUANTILES
        }
        tail_oof_metadata = {
            "scope": "per_process",
            "view": variant,
            "quantiles": list(TAIL_QUANTILES),
            "source": "validated_checkpoint",
        }
    else:
        tail_oof, tail_oof_metadata = crossfit_per_process_predictions(
            initial_features,
            target_mib,
            view=variant,
            quantiles=list(TAIL_QUANTILES),
            n_jobs=args.n_jobs,
            fallback_predictions=fallback_oof,
        )
        np.savez_compressed(
            checkpoint_path,
            logical_task_id=initial_features[
                "logical_task_id"
            ].astype(str).to_numpy(),
            **{
                f"q{int(round(quantile * 1000)):03d}": values
                for quantile, values in tail_oof.items()
            },
        )
    tail_test, tail_final_metadata = fit_final_per_process_predictions(
        initial_features,
        test_features,
        target_mib,
        view=variant,
        quantiles=list(TAIL_QUANTILES),
        n_jobs=args.n_jobs,
        model_root=model_root / f"{variant_path}_process_tail",
        fallback_predictions=fallback_test,
    )

    point_oof = oof_frame["q500_oof_mib"].to_numpy(dtype=float)
    point_test = task_frame["point_q50_prediction_mib"].to_numpy(dtype=float)
    oof_predictions = {0.5: point_oof}
    test_predictions = {0.5: point_test}
    previous_oof = {}
    previous_test = {}
    for quantile in TAIL_QUANTILES:
        label = int(round(quantile * 1000))
        previous_oof[quantile] = oof_frame[
            f"q{label:03d}_oof_mib"
        ].to_numpy(dtype=float)
        previous_test[quantile] = task_frame[
            f"q{label:03d}_prediction_mib"
        ].to_numpy(dtype=float)
        oof_predictions[quantile] = tail_oof[quantile]
        test_predictions[quantile] = tail_test[quantile]

    ordered = [0.5, *TAIL_QUANTILES]
    oof_stack = np.maximum.accumulate(
        np.column_stack([oof_predictions[value] for value in ordered]),
        axis=1,
    )
    test_stack = np.maximum.accumulate(
        np.column_stack([test_predictions[value] for value in ordered]),
        axis=1,
    )
    for position, quantile in enumerate(ordered):
        oof_predictions[quantile] = oof_stack[:, position]
        test_predictions[quantile] = test_stack[:, position]

    scaler = joblib.load(model_root / "signature_scaler.joblib")
    policy, candidates = select_allocation_policy(
        initial_features,
        oof_predictions,
        scaler,
        mode="global" if variant == "A" else "hierarchical",
    )
    allocation = apply_policy_sequentially(
        initial_features,
        test_features,
        oof_predictions,
        test_predictions,
        scaler,
        policy,
        update_during_test=(variant != "A"),
    )
    final_tasks = prediction_frame(
        variant=variant,
        seed=args.seed,
        test=test_features,
        predictions=test_predictions,
        allocation=allocation,
        c_hat=c_test["c_hat_bytes"].to_numpy(dtype=float),
        policy=policy,
        standalone_history_q50=(
            task_frame["standalone_history_q50_mib"].to_numpy(dtype=float)
            if variant == "A+P+C"
            else None
        ),
        consumed_rss_q50=(
            task_frame["consumed_derived_q50_mib"].to_numpy(dtype=float)
            if variant == "A+P+C"
            else None
        ),
    )
    for quantile in TAIL_QUANTILES:
        label = int(round(quantile * 1000))
        final_tasks[f"global_q{label:03d}_prediction_mib"] = previous_test[
            quantile
        ]
        oof_frame[f"global_q{label:03d}_oof_mib"] = previous_oof[quantile]
        oof_frame[f"q{label:03d}_oof_mib"] = oof_predictions[quantile]
    final_tasks.to_csv(
        variant_root / "task_predictions.tsv", sep="\t", index=False
    )
    oof_frame["q500_oof_mib"] = oof_predictions[0.5]
    oof_frame.to_csv(
        variant_root / "initial_oof_predictions.tsv",
        sep="\t",
        index=False,
    )
    candidates.to_csv(
        variant_root / "calibration_candidates.csv", index=False
    )
    write_json(variant_root / "selected_policy.json", policy)
    write_json(
        variant_root / "process_tail_selected_policy.json",
        policy,
    )

    manifest_path = result_root / "run_manifest.json"
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    manifest["variant_metadata"][variant]["per_process_tail_oof"] = (
        tail_oof_metadata
    )
    manifest["variant_metadata"][variant]["per_process_tail_final"] = (
        tail_final_metadata
    )
    manifest["variant_metadata"][variant]["allocation_policy"] = policy
    manifest["variant_metadata"][variant][
        "tail_model_scope"
    ] = "per_process"
    manifest["postprocessing_iteration"] = {
        "reason": (
            f"replace mixed-scale {variant} global tails with per-process "
            "tail ensembles"
        ),
        "test_labels_used_for_selection": False,
    }
    write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "variant": variant,
                "policy": policy,
                "processes": len(tail_final_metadata["processes"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
