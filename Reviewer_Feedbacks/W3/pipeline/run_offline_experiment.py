#!/usr/bin/env python3
"""Run the isolated CAMP offline causal experiment."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from calibration import apply_policy_sequentially, select_allocation_policy
from common import (
    MIB,
    ROOT,
    file_sha256,
    load_config,
    load_experiment_rows,
    experiment_sort,
    resolve_config_path,
    training_sort,
    write_json,
)
from history import SignatureScaler, materialize_causal_history
from modeling import (
    crossfit_predictions,
    fit_final_predictions,
    materialize_model_features,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1996)
    parser.add_argument("--split-manifest-root", type=Path)
    parser.add_argument("--split-seed", type=int, default=1996)
    parser.add_argument("--expected-train-rows", type=int)
    parser.add_argument("--expected-test-rows", type=int)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument(
        "--n-jobs",
        type=int,
        default=int(os.environ.get("SLURM_CPUS_PER_TASK", "8")),
    )
    return parser.parse_args()


def validate_population(
    rows: pd.DataFrame,
    initial: pd.DataFrame,
    test: pd.DataFrame,
    expected_train_rows: int | None = None,
    expected_test_rows: int | None = None,
) -> None:
    config = load_config()
    checks = {
        "rows": (len(rows), int(config["expected_rows"])),
        "train_rows": (
            len(initial),
            int(expected_train_rows if expected_train_rows is not None else config["expected_train_rows"]),
        ),
        "test_rows": (
            len(test),
            int(expected_test_rows if expected_test_rows is not None else config["expected_test_rows"]),
        ),
        "workflows": (
            rows["workflow"].nunique(),
            int(config["expected_workflows"]),
        ),
        "processes": (
            rows[["workflow", "process"]].drop_duplicates().shape[0],
            int(config["expected_processes"]),
        ),
    }
    failures = [
        f"{name}: observed={observed}, expected={expected}"
        for name, (observed, expected) in checks.items()
        if observed != expected
    ]
    if failures:
        raise ValueError("; ".join(failures))
    expected_consumed = (
        pd.to_numeric(rows["ebpf_read_return_bytes"], errors="raise")
        + pd.to_numeric(
            rows["ebpf_mmap_page_fault_bytes"], errors="raise"
        )
    )
    observed_consumed = pd.to_numeric(
        rows["ebpf_total_consumed_bytes"], errors="raise"
    )
    if not np.array_equal(
        expected_consumed.to_numpy(dtype=np.int64),
        observed_consumed.to_numpy(dtype=np.int64),
    ):
        raise ValueError("consumed-byte formula does not match source fields")


def retry_policy(
    first_allocation_mib: float,
    actual_peak_mib: float,
    row: pd.Series,
) -> tuple[int, float]:
    multiplier = float(load_config()["allocation"]["retry_multiplier"])
    neighbour_p99 = (
        float(row.get("history_i2_peak_p99_bytes", np.nan)) / MIB
    )
    exact_p99 = float(row.get("history_i0_peak_p99_bytes", np.nan)) / MIB
    candidates = [
        value
        for value in (neighbour_p99, exact_p99)
        if np.isfinite(value) and value > 0
    ]
    allocation = max(float(first_allocation_mib), 1.0)
    retries = 0
    while allocation < actual_peak_mib and retries < 20:
        allocation = max(
            [math.ceil(allocation * multiplier)]
            + [math.ceil(value) for value in candidates]
        )
        retries += 1
    return retries, float(allocation)


def prediction_frame(
    *,
    variant: str,
    seed: int,
    test: pd.DataFrame,
    predictions: dict[float, np.ndarray],
    allocation: pd.DataFrame,
    c_hat: np.ndarray,
    policy: dict[str, Any],
    standalone_history_q50: np.ndarray | None = None,
    consumed_rss_q50: np.ndarray | None = None,
) -> pd.DataFrame:
    actual = (
        pd.to_numeric(test["peak_memory_bytes"], errors="raise").to_numpy()
        / MIB
    )
    first = allocation["first_allocation_mib"].to_numpy(dtype=float)
    retry_values = [
        retry_policy(first_value, actual_value, row)
        for first_value, actual_value, (_, row) in zip(
            first, actual, test.iterrows()
        )
    ]
    result = pd.DataFrame(
        {
            "logical_task_id": test["logical_task_id"].astype(str).to_numpy(),
            "workflow": test["workflow"].astype(str).to_numpy(),
            "process": test["process"].astype(str).to_numpy(),
            "seed": seed,
            "replay_position": pd.to_numeric(
                test["experiment_position"], errors="raise"
            )
            .astype(int)
            .to_numpy(),
            "method": "CAMP",
            "feature_view": variant,
            "raw_prediction_mib": predictions[0.5],
            "point_q50_prediction_mib": predictions[0.5],
            "standalone_history_q50_mib": (
                standalone_history_q50
                if standalone_history_q50 is not None
                else np.full(len(test), np.nan)
            ),
            "consumed_derived_q50_mib": (
                consumed_rss_q50
                if consumed_rss_q50 is not None
                else np.full(len(test), np.nan)
            ),
            "q900_prediction_mib": predictions[0.9],
            "q950_prediction_mib": predictions[0.95],
            "q990_prediction_mib": predictions[0.99],
            "q995_prediction_mib": predictions[0.995],
            "c_hat_bytes": (
                c_hat
                if variant == "A+P+C"
                else np.full(len(test), np.nan)
            ),
            "actual_consumed_bytes": pd.to_numeric(
                test[load_config()["consumed_target"]], errors="raise"
            ).to_numpy(),
            "actual_peak_mib": actual,
            "runtime_seconds": pd.to_numeric(
                test["runtime_seconds"], errors="coerce"
            ).to_numpy(),
            "history_scope": test["history_scope"].astype(str).to_numpy(),
            "history_support_count": pd.to_numeric(
                test["history_scope_support"], errors="coerce"
            ).to_numpy(),
            "history_confidence": pd.to_numeric(
                test["history_scope_confidence"], errors="coerce"
            ).to_numpy(),
            "allocation_residual_quantile": float(
                policy.get("residual_quantile", np.nan)
            ),
            "allocation_base_policy": str(
                policy.get("base_policy", "per_process")
            ),
        }
    )
    for column in allocation:
        result[column] = allocation[column].to_numpy()
    result["initial_oom"] = first < actual
    result["native_retry_count"] = [value[0] for value in retry_values]
    result["native_final_allocation_mib"] = [
        value[1] for value in retry_values
    ]
    result["native_unresolved_oom"] = (
        result["native_final_allocation_mib"] < result["actual_peak_mib"]
    )
    return result


def main() -> None:
    args = arguments()
    started = time.time()
    config = load_config()
    allowed_seeds = [int(value) for value in config.get("variance_seeds", [config["seed"]])]
    if args.seed not in allowed_seeds:
        raise ValueError(
            f"replicate seed {args.seed} is not in {allowed_seeds}"
        )
    os.environ["CAMP_REPLICATE_SEED"] = str(args.seed)

    rows = load_experiment_rows(
        args.seed,
        manifest_root=args.split_manifest_root,
        split_seed=args.split_seed,
    )
    initial = training_sort(rows.loc[rows["role"] == "train"]).reset_index(
        drop=True
    )
    test = experiment_sort(rows.loc[rows["role"] == "test"]).reset_index(drop=True)
    validate_population(
        rows,
        initial,
        test,
        args.expected_train_rows,
        args.expected_test_rows,
    )

    output_root = args.output_root or ROOT / "results" / f"seed_{args.seed}"
    model_root = args.model_root or ROOT / "models" / f"seed_{args.seed}"
    output_root.mkdir(parents=True, exist_ok=True)
    model_root.mkdir(parents=True, exist_ok=True)

    scaler = SignatureScaler.fit(initial)
    joblib.dump(scaler, model_root / "signature_scaler.joblib")
    initial_history, test_history = materialize_causal_history(
        initial, test, scaler
    )
    initial_history.to_csv(
        output_root / "initial_causal_features.tsv", sep="\t", index=False
    )
    test_history.to_csv(
        output_root / "test_causal_features.tsv", sep="\t", index=False
    )

    initial_features = materialize_model_features(initial_history)
    test_features = materialize_model_features(test_history)
    consumed_target = pd.to_numeric(
        initial_features[config["consumed_target"]], errors="raise"
    ).to_numpy(dtype=float)
    c_oof, c_oof_metadata = crossfit_predictions(
        initial_features,
        consumed_target,
        view="A+P",
        quantiles=[0.5],
        n_jobs=args.n_jobs,
    )
    c_test, c_final_metadata = fit_final_predictions(
        initial_features,
        test_features,
        consumed_target,
        view="A+P",
        quantiles=[0.5],
        n_jobs=args.n_jobs,
        model_root=model_root / "consumed_c_hat",
    )
    initial_features["c_hat_bytes"] = c_oof[0.5]
    test_features["c_hat_bytes"] = c_test[0.5]
    initial_features = materialize_model_features(initial_features)
    test_features = materialize_model_features(test_features)

    pd.DataFrame(
        {
            "logical_task_id": initial_features["logical_task_id"],
            "role": "train_oof",
            "c_hat_bytes": c_oof[0.5],
            "actual_consumed_bytes": consumed_target,
        }
    ).to_csv(output_root / "c_hat_initial_oof.tsv", sep="\t", index=False)
    pd.DataFrame(
        {
            "logical_task_id": test_features["logical_task_id"],
            "role": "test",
            "c_hat_bytes": c_test[0.5],
            "actual_consumed_bytes": pd.to_numeric(
                test_features[config["consumed_target"]], errors="raise"
            ),
        }
    ).to_csv(output_root / "c_hat_test.tsv", sep="\t", index=False)

    target_mib = (
        pd.to_numeric(
            initial_features[config["rss_target"]], errors="raise"
        ).to_numpy(dtype=float)
        / MIB
    )
    quantiles = [float(value) for value in config["models"]["quantiles"]]
    consumed_ratio_scale = 1_000_000.0
    consumed_ratio_target = (
        pd.to_numeric(
            initial_features[config["rss_target"]], errors="raise"
        ).to_numpy(dtype=float)
        / np.maximum(consumed_target, 1.0)
        * consumed_ratio_scale
    )
    ratio_oof, ratio_oof_metadata = crossfit_predictions(
        initial_features,
        consumed_ratio_target,
        view="A+P",
        quantiles=[0.5],
        n_jobs=args.n_jobs,
    )
    ratio_test, ratio_final_metadata = fit_final_predictions(
        initial_features,
        test_features,
        consumed_ratio_target,
        view="A+P",
        quantiles=[0.5],
        n_jobs=args.n_jobs,
        model_root=model_root / "memory_per_consumed_ratio",
    )
    consumed_rss_oof_mib = (
        c_oof[0.5] * ratio_oof[0.5] / consumed_ratio_scale / MIB
    )
    consumed_rss_test_mib = (
        c_test[0.5] * ratio_test[0.5] / consumed_ratio_scale / MIB
    )
    pd.DataFrame(
        {
            "logical_task_id": initial_features["logical_task_id"],
            "ratio_hat_scaled": ratio_oof[0.5],
            "consumed_derived_rss_mib": consumed_rss_oof_mib,
            "actual_peak_mib": target_mib,
        }
    ).to_csv(
        output_root / "consumed_rss_initial_oof.tsv",
        sep="\t",
        index=False,
    )
    pd.DataFrame(
        {
            "logical_task_id": test_features["logical_task_id"],
            "ratio_hat_scaled": ratio_test[0.5],
            "consumed_derived_rss_mib": consumed_rss_test_mib,
            "actual_peak_mib": (
                pd.to_numeric(
                    test_features[config["rss_target"]], errors="raise"
                ).to_numpy(dtype=float)
                / MIB
            ),
        }
    ).to_csv(
        output_root / "consumed_rss_test.tsv",
        sep="\t",
        index=False,
    )

    trained: dict[str, dict[str, Any]] = {}
    for variant in config["variants"]:
        variant_path = {
            "A": "a",
            "A+P": "a_plus_p",
            "A+P+C": "a_plus_p_plus_c",
        }[variant]
        oof_predictions, oof_metadata = crossfit_predictions(
            initial_features,
            target_mib,
            view=variant,
            quantiles=quantiles,
            n_jobs=args.n_jobs,
        )
        test_predictions, final_metadata = fit_final_predictions(
            initial_features,
            test_features,
            target_mib,
            view=variant,
            quantiles=quantiles,
            n_jobs=args.n_jobs,
            model_root=model_root / variant_path,
        )
        trained[variant] = {
            "path": variant_path,
            "oof_predictions": oof_predictions,
            "test_predictions": test_predictions,
            "oof_metadata": oof_metadata,
            "final_metadata": final_metadata,
        }

    standalone_history_oof = trained["A+P"]["oof_predictions"][0.5].copy()
    standalone_history_test = trained["A+P"]["test_predictions"][0.5].copy()
    stack_metadata = {
        "enabled": False,
        "contract": (
            "A+P+C q50 is the direct A+P+C model; standalone A predictions "
            "are never blended into APC"
        ),
    }

    variant_metadata = {}
    for variant in config["variants"]:
        details = trained[variant]
        variant_path = str(details["path"])
        oof_predictions = details["oof_predictions"]
        test_predictions = details["test_predictions"]
        variant_output = output_root / variant_path
        variant_output.mkdir(parents=True, exist_ok=True)
        mode = "global" if variant == "A" else "hierarchical"
        policy, candidates = select_allocation_policy(
            initial_features,
            oof_predictions,
            scaler,
            mode=mode,
        )
        candidates.to_csv(
            variant_output / "calibration_candidates.csv", index=False
        )
        write_json(variant_output / "selected_policy.json", policy)
        allocation = apply_policy_sequentially(
            initial_features,
            test_features,
            oof_predictions,
            test_predictions,
            scaler,
            policy,
            update_during_test=(variant != "A"),
        )
        task_predictions = prediction_frame(
            variant=variant,
            seed=args.seed,
            test=test_features,
            predictions=test_predictions,
            allocation=allocation,
            c_hat=c_test[0.5],
            policy=policy,
            standalone_history_q50=(
                standalone_history_test if variant == "A+P+C" else None
            ),
            consumed_rss_q50=(
                consumed_rss_test_mib if variant == "A+P+C" else None
            ),
        )
        task_predictions.to_csv(
            variant_output / "task_predictions.tsv", sep="\t", index=False
        )
        oof_frame = pd.DataFrame(
            {
                "logical_task_id": initial_features["logical_task_id"],
                "workflow": initial_features["workflow"],
                "process": initial_features["process"],
                "actual_peak_mib": target_mib,
            }
        )
        for quantile, values in oof_predictions.items():
            oof_frame[f"q{int(round(quantile * 1000)):03d}_oof_mib"] = (
                values
            )
        if variant == "A+P+C":
            oof_frame["standalone_history_q500_oof_mib"] = (
                standalone_history_oof
            )
            oof_frame["consumed_derived_q500_oof_mib"] = (
                consumed_rss_oof_mib
            )
        oof_frame.to_csv(
            variant_output / "initial_oof_predictions.tsv",
            sep="\t",
            index=False,
        )
        variant_metadata[variant] = {
            "oof": details["oof_metadata"],
            "final": details["final_metadata"],
            "allocation_policy": policy,
            "point_stack": stack_metadata if variant == "A+P+C" else None,
            "model_updates_during_test": False,
            "history_updates_during_test": variant != "A",
            "calibration_updates_during_test": variant != "A",
        }

    manifest = {
        "experiment": config["experiment_name"],
        "seed": args.seed,
        "source_data": config["source_data"],
        "source_sha256": file_sha256(
            resolve_config_path(config["source_data"])
        ),
        "initial_rows": int(len(initial)),
        "test_rows": int(len(test)),
        "variants": config["variants"],
        "consumed_formula": config["consumed_formula"],
        "c_hat_oof": c_oof_metadata,
        "c_hat_final": c_final_metadata,
        "memory_per_consumed_ratio_oof": ratio_oof_metadata,
        "memory_per_consumed_ratio_final": ratio_final_metadata,
        "variant_metadata": variant_metadata,
        "leakage_contract": (
            "current peak RSS and current consumed bytes are labels only; "
            "test history is snapshotted before each row is revealed"
        ),
        "elapsed_seconds": time.time() - started,
    }
    write_json(output_root / "run_manifest.json", manifest)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
