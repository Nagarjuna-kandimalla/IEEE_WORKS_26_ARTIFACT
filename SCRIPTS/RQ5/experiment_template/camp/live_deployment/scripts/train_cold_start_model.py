#!/usr/bin/env python3
"""Train and validate the workflow-cold CAMP global fallback."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OFFLINE = ROOT.parent / "original_offline_replay"
sys.path.insert(0, str(OFFLINE / "scripts"))

from camp_live.cold_start import (
    COLD_QUANTILES,
    fit_predict,
    materialize_cold_features,
)
from history import HistoryIndex


def global_snapshot(
    reference: pd.DataFrame,
    scaler,
) -> dict[str, float | str]:
    index = HistoryIndex(nearest_k=48, scope_window=512)
    vectors = scaler.transform(reference)
    for position, (_, row) in enumerate(reference.iterrows()):
        index.add(row, vectors[position])
    generic = {
        "logical_task_id": "workflow_cold",
        "workflow": "__unseen_workflow__",
        "process": "__unseen_process__",
        "version": "__unseen_version__",
        "system_config_id": "sc13",
        "task_instance": "workflow_cold",
        "input_identity": "workflow_cold",
        "split_group_id": None,
        "static_input_bytes": 1,
        "staged_original_input_bytes": np.nan,
        "staged_intermediate_input_bytes": np.nan,
        "staged_external_input_bytes": np.nan,
        "requested_threads": 1,
        "requested_java_heap_mb": np.nan,
        "coverage": np.nan,
        "interval_length_bp": np.nan,
        "has_str_region": False,
        "worker_count": 13,
        "worker_vcpu": 32,
        "worker_memory_gib": 244.140625,
    }
    vector = scaler.transform(pd.DataFrame([generic]))[0]
    return index.snapshot(generic, vector)


def replace_with_global_snapshot(
    frame: pd.DataFrame,
    snapshot: dict[str, float | str],
) -> pd.DataFrame:
    result = frame.copy()
    for key, value in snapshot.items():
        if key.startswith("history_i5_") or key in {
            "history_scope",
            "history_scope_support",
            "history_scope_confidence",
        }:
            result[key] = value
    return result


def select_policy(
    frame: pd.DataFrame,
    actual_mib: np.ndarray,
    predictions: dict[float, np.ndarray],
) -> tuple[dict, pd.DataFrame]:
    records = []
    allocations = {}
    workflows = frame["workflow"].astype(str).to_numpy()
    p95_by_workflow = {
        workflow: np.quantile(
            frame.loc[
                frame["workflow"].astype(str).ne(workflow),
            ]
            .sort_values("history_order")
            .tail(512)["peak_memory_bytes"]
            .to_numpy(dtype=float)
            / 2**20,
            0.95,
        )
        for workflow in sorted(set(workflows))
    }
    global_p95_mib = np.asarray(
        [p95_by_workflow[workflow] for workflow in workflows]
    )
    ood_safety_floor = np.ceil(
        np.sqrt(
            np.maximum(predictions[0.99], 1.0)
            * np.maximum(global_p95_mib, 1.0)
        )
    )
    residual_probabilities = (0.9, 0.95, 0.975, 0.99, 0.995)
    for quantile in COLD_QUANTILES:
        base = np.maximum(predictions[quantile], 1.0)
        ratio = actual_mib / base
        for probability in residual_probabilities:
            multiplier = max(
                float(np.quantile(ratio, probability, method="higher")),
                1.0,
            )
            allocation = np.maximum(
                np.ceil(base * multiplier),
                ood_safety_floor,
            )
            workflow_coverages = {
                workflow: float(
                    np.mean(
                        allocation[workflows == workflow]
                        >= actual_mib[workflows == workflow]
                    )
                )
                for workflow in sorted(set(workflows))
            }
            key = (quantile, probability)
            allocations[key] = allocation
            records.append(
                {
                    "model_quantile": quantile,
                    "residual_probability": probability,
                    "multiplier": multiplier,
                    "coverage": float(
                        np.mean(allocation >= actual_mib)
                    ),
                    "minimum_workflow_coverage": min(
                        workflow_coverages.values()
                    ),
                    "request_to_peak_ratio": float(
                        allocation.sum() / actual_mib.sum()
                    ),
                    "oom_tasks": int(
                        np.sum(allocation < actual_mib)
                    ),
                    **{
                        f"coverage_{workflow}": value
                        for workflow, value in workflow_coverages.items()
                    },
                }
            )
    candidates = pd.DataFrame.from_records(records)
    eligible = candidates.loc[
        (candidates["coverage"] >= 0.99)
        & (candidates["minimum_workflow_coverage"] >= 0.975)
    ]
    if len(eligible):
        chosen = eligible.sort_values(
            [
                "request_to_peak_ratio",
                "oom_tasks",
                "model_quantile",
                "residual_probability",
            ]
        ).iloc[0]
        status = "met"
    else:
        chosen = candidates.sort_values(
            [
                "minimum_workflow_coverage",
                "coverage",
                "request_to_peak_ratio",
            ],
            ascending=[False, False, True],
        ).iloc[0]
        status = "unmet_best_available"
    policy = {
        "scope": "workflow_cold_global",
        "feature_view": "A+P+C",
        "model_quantile": float(chosen["model_quantile"]),
        "residual_probability": float(
            chosen["residual_probability"]
        ),
        "multiplier": float(chosen["multiplier"]),
        "constraint_status": status,
        "coverage": float(chosen["coverage"]),
        "minimum_workflow_coverage": float(
            chosen["minimum_workflow_coverage"]
        ),
        "request_to_peak_ratio": float(
            chosen["request_to_peak_ratio"]
        ),
        "oom_tasks": int(chosen["oom_tasks"]),
        "selection_method": "leave_one_complete_workflow_out",
        "test_labels_used_for_live_bowtie2": False,
        "ood_safety_floor": {
            "applies_when": "history_i4_support == 0",
            "formula": (
                "sqrt(model_q99_mib * history_i5_peak_p95_mib)"
            ),
            "log_space_global_weight": 0.5,
        },
    }
    return policy, candidates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-features", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--n-jobs", type=int, default=8)
    args = parser.parse_args()

    import joblib

    scaler = joblib.load(args.model_root / "signature_scaler.joblib")
    frame = pd.read_csv(
        args.history_features,
        sep="\t",
        low_memory=False,
    ).sort_values("history_order").reset_index(drop=True)
    workflows = sorted(frame["workflow"].astype(str).unique())
    actual_consumed = frame[
        "ebpf_total_consumed_bytes"
    ].to_numpy(dtype=float)
    actual_mib = (
        frame["peak_memory_bytes"].to_numpy(dtype=float) / 2**20
    )

    c_oof = np.full(len(frame), np.nan, dtype=float)
    fit_frames = {}
    validation_frames = {}
    for workflow in workflows:
        fit_mask = ~frame["workflow"].astype(str).eq(workflow).to_numpy()
        validation_mask = ~fit_mask
        snapshot = global_snapshot(frame.loc[fit_mask], scaler)
        fit_frame = replace_with_global_snapshot(
            frame.loc[fit_mask],
            snapshot,
        )
        validation = replace_with_global_snapshot(
            frame.loc[validation_mask],
            snapshot,
        )
        fit_frames[workflow] = fit_frame
        validation_frames[workflow] = validation
        fit_features = materialize_cold_features(fit_frame)
        validation_features = materialize_cold_features(validation)
        predicted = fit_predict(
            fit_features,
            validation_features,
            actual_consumed[fit_mask],
            view="A",
            quantiles=(0.5,),
            n_jobs=args.n_jobs,
        )
        c_oof[validation_mask] = predicted[0.5]
    if not np.isfinite(c_oof).all():
        raise SystemExit("cold-start C-hat OOF predictions are incomplete")

    rss_oof = {
        quantile: np.full(len(frame), np.nan, dtype=float)
        for quantile in COLD_QUANTILES
    }
    for workflow in workflows:
        fit_mask = ~frame["workflow"].astype(str).eq(workflow).to_numpy()
        validation_mask = ~fit_mask
        fit_frame = fit_frames[workflow].copy()
        fit_frame["c_hat_bytes"] = c_oof[fit_mask]
        validation = validation_frames[workflow].copy()
        validation["c_hat_bytes"] = c_oof[validation_mask]
        predicted = fit_predict(
            materialize_cold_features(fit_frame),
            materialize_cold_features(validation),
            actual_mib[fit_mask],
            view="A+P+C",
            quantiles=COLD_QUANTILES,
            n_jobs=args.n_jobs,
        )
        for quantile in COLD_QUANTILES:
            rss_oof[quantile][validation_mask] = predicted[quantile]
    if not all(np.isfinite(values).all() for values in rss_oof.values()):
        raise SystemExit("cold-start RSS OOF predictions are incomplete")

    policy, candidates = select_policy(frame, actual_mib, rss_oof)
    args.output_root.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(
        args.output_root / "calibration_candidates.csv",
        index=False,
    )
    oof = pd.DataFrame(
        {
            "logical_task_id": frame["logical_task_id"],
            "workflow": frame["workflow"],
            "process": frame["process"],
            "actual_peak_mib": actual_mib,
            "actual_consumed_bytes": actual_consumed,
            "c_hat_bytes": c_oof,
        }
    )
    for quantile, values in rss_oof.items():
        oof[f"q{int(round(quantile * 1000)):03d}_mib"] = values
    selected = np.ceil(
        rss_oof[policy["model_quantile"]] * policy["multiplier"]
    )
    workflow_values = frame["workflow"].astype(str)
    p95_by_workflow = {
        workflow: np.quantile(
            frame.loc[
                workflow_values.ne(workflow),
            ]
            .sort_values("history_order")
            .tail(512)["peak_memory_bytes"]
            .to_numpy(dtype=float)
            / 2**20,
            0.95,
        )
        for workflow in sorted(workflow_values.unique())
    }
    global_p95_mib = workflow_values.map(
        p95_by_workflow
    ).to_numpy(dtype=float)
    oof["global_peak_p95_mib"] = global_p95_mib
    oof["ood_safety_floor_mib"] = np.ceil(
        np.sqrt(
            np.maximum(rss_oof[0.99], 1.0)
            * np.maximum(global_p95_mib, 1.0)
        )
    )
    selected = np.maximum(selected, oof["ood_safety_floor_mib"])
    oof["selected_allocation_mib"] = selected
    oof["initial_oom"] = selected < actual_mib
    oof.to_csv(
        args.output_root / "workflow_holdout_predictions.tsv",
        sep="\t",
        index=False,
    )

    full_snapshot = global_snapshot(frame, scaler)
    full_frame = replace_with_global_snapshot(frame, full_snapshot)
    full_features = materialize_cold_features(full_frame)
    fit_predict(
        full_features,
        full_features.iloc[:1],
        actual_consumed,
        view="A",
        quantiles=(0.5,),
        n_jobs=args.n_jobs,
        model_root=args.output_root / "models" / "consumed_c_hat",
    )
    full_features["c_hat_bytes"] = c_oof
    full_features = materialize_cold_features(full_features)
    fit_predict(
        full_features,
        full_features.iloc[:1],
        actual_mib,
        view="A+P+C",
        quantiles=COLD_QUANTILES,
        n_jobs=args.n_jobs,
        model_root=args.output_root / "models" / "a_plus_p_plus_c",
    )
    (args.output_root / "policy.json").write_text(
        json.dumps(policy, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "design": "camp_workflow_cold",
        "rows": int(len(frame)),
        "workflows": workflows,
        "model_seeds": [20260728, 20260729, 20260730],
        "quantiles": list(COLD_QUANTILES),
        "consumed_formula": (
            "ebpf_read_return_bytes + ebpf_mmap_page_fault_bytes"
        ),
        "policy": policy,
        "workflow_identity_feature_used": False,
        "validation": "leave_one_complete_workflow_out",
    }
    (args.output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
