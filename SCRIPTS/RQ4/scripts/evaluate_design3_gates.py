#!/usr/bin/env python3
"""Evaluate Design 3 selective auditing at equal task-count budgets."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from calibration import apply_policy_sequentially
from common import ROOT, write_json
from design3_gates import (
    DEFAULT_LANE_FRACTIONS,
    add_gate_targets,
    build_gate_plan,
    fit_gate_models,
    grouped_rank,
    proportional_quotas,
    score_gate_models,
    select_from_plan,
)
from modeling import grouped_fold_assignments


MIB = 1024.0**2
QUANTILES = (0.5, 0.9, 0.95, 0.99, 0.995)
SCENARIOS = ("a_plus_p", "a_plus_p_plus_c")
POLICIES = ("three_gate", "uniform_random", "process_stratified_random")
CORE_METRICS = (
    "underallocation_recall",
    "shortfall_share",
    "heavy_information_recall",
    "peak_top5_recall",
    "consumed_surprise_recall",
)
LANE_MIXES = {
    "fixed_65_15_20": {"risk": 0.65, "information": 0.15, "discovery": 0.20},
    "risk_only": {"risk": 1.0, "information": 0.0, "discovery": 0.0},
    "equal_thirds": {
        "risk": 1.0 / 3.0,
        "information": 1.0 / 3.0,
        "discovery": 1.0 / 3.0,
    },
    "information_heavy_50_30_20": {
        "risk": 0.50,
        "information": 0.30,
        "discovery": 0.20,
    },
    "risk_heavy_80_10_10": {
        "risk": 0.80,
        "information": 0.10,
        "discovery": 0.10,
    },
    "discovery_heavy_50_10_40": {
        "risk": 0.50,
        "information": 0.10,
        "discovery": 0.40,
    },
}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1996)
    parser.add_argument("--repetitions", type=int, default=1000)
    parser.add_argument("--sensitivity-repetitions", type=int, default=200)
    parser.add_argument("--n-jobs", type=int, default=8)
    parser.add_argument(
        "--budgets",
        type=float,
        nargs="+",
        default=(0.05, 0.10, 0.20, 0.30, 0.50, 1.00),
    )
    return parser.parse_args()


def _assert_aligned(left: pd.DataFrame, right: pd.DataFrame, name: str) -> None:
    if not (
        left["logical_task_id"].astype(str).to_numpy()
        == right["logical_task_id"].astype(str).to_numpy()
    ).all():
        raise ValueError(f"unaligned Design 3 artifact: {name}")


def _prediction_map(
    frame: pd.DataFrame,
    *,
    oof: bool,
) -> dict[float, np.ndarray]:
    if oof:
        return {
            quantile: frame[
                f"q{int(round(quantile * 1000)):03d}_oof_mib"
            ].to_numpy(float)
            for quantile in QUANTILES
        }
    columns = {
        0.5: "point_q50_prediction_mib",
        0.9: "q900_prediction_mib",
        0.95: "q950_prediction_mib",
        0.99: "q990_prediction_mib",
        0.995: "q995_prediction_mib",
    }
    return {
        quantile: frame[column].to_numpy(float)
        for quantile, column in columns.items()
    }


def _prefix_predictions(
    base: pd.DataFrame,
    predictions: pd.DataFrame,
    prefix: str,
    *,
    oof: bool,
) -> pd.DataFrame:
    result = base.copy()
    suffix = "_oof_mib" if oof else "_prediction_mib"
    source_names = {
        0.5: f"q500{suffix}" if oof else "point_q50_prediction_mib",
        0.9: f"q900{suffix}",
        0.95: f"q950{suffix}",
        0.99: f"q990{suffix}",
        0.995: f"q995{suffix}",
    }
    for quantile, source in source_names.items():
        result[
            f"{prefix}_q{int(round(quantile * 1000)):03d}_prediction_mib"
        ] = predictions[source].to_numpy(float)
    for quantile in (0.9, 0.95, 0.99, 0.995):
        source = (
            f"global_q{int(round(quantile * 1000)):03d}_oof_mib"
            if oof
            else f"global_q{int(round(quantile * 1000)):03d}_prediction_mib"
        )
        if source in predictions:
            result[
                f"{prefix}_global_q{int(round(quantile * 1000)):03d}_prediction_mib"
            ] = predictions[source].to_numpy(float)
    return result


def crossfit_active_allocation(
    initial: pd.DataFrame,
    predictions: dict[float, np.ndarray],
    scaler,
    policy: dict[str, Any],
    folds: int,
) -> np.ndarray:
    """Apply one frozen allocation policy with fold-external histories."""

    assignments = grouped_fold_assignments(initial, folds)
    active_allocation = np.full(len(initial), np.nan, dtype=float)
    for fold in range(folds):
        fit_mask = assignments != fold
        validation_mask = assignments == fold
        fit = initial.loc[fit_mask].reset_index(drop=True)
        validation = initial.loc[validation_mask].reset_index(drop=True)
        fit_predictions = {
            value: values[fit_mask]
            for value, values in predictions.items()
        }
        validation_predictions = {
            value: values[validation_mask]
            for value, values in predictions.items()
        }
        allocation = apply_policy_sequentially(
            fit,
            validation,
            fit_predictions,
            validation_predictions,
            scaler,
            policy,
            update_during_test=False,
        )
        active_allocation[validation_mask] = allocation[
            "first_allocation_mib"
        ].to_numpy(float)
    if not np.isfinite(active_allocation).all():
        raise AssertionError("crossfit allocation contains missing values")
    return active_allocation


def load_analysis_frames(seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    root = ROOT / "results" / f"seed_{seed}"
    initial = pd.read_csv(
        root / "initial_causal_features.tsv",
        sep="\t",
        low_memory=False,
    )
    test = pd.read_csv(
        root / "test_causal_features.tsv",
        sep="\t",
        low_memory=False,
    )
    c_initial = pd.read_csv(root / "c_hat_initial_oof.tsv", sep="\t")
    c_test = pd.read_csv(root / "c_hat_test.tsv", sep="\t")
    ap_oof = pd.read_csv(
        root / "a_plus_p" / "initial_oof_predictions.tsv",
        sep="\t",
    )
    apc_oof = pd.read_csv(
        root / "a_plus_p_plus_c" / "initial_oof_predictions.tsv",
        sep="\t",
    )
    ap_test = pd.read_csv(
        root / "a_plus_p" / "task_predictions.tsv",
        sep="\t",
    )
    apc_test = pd.read_csv(
        root / "a_plus_p_plus_c" / "task_predictions.tsv",
        sep="\t",
    )
    for name, frame in (
        ("c-hat initial", c_initial),
        ("A+P OOF", ap_oof),
        ("APC OOF", apc_oof),
    ):
        _assert_aligned(initial, frame, name)
    for name, frame in (
        ("c-hat test", c_test),
        ("A+P test", ap_test),
        ("APC test", apc_test),
    ):
        _assert_aligned(test, frame, name)

    initial = _prefix_predictions(initial, ap_oof, "ap", oof=True)
    initial = _prefix_predictions(initial, apc_oof, "apc", oof=True)
    initial["c_hat_bytes"] = c_initial["c_hat_bytes"].to_numpy(float)
    initial["actual_consumed_bytes"] = c_initial[
        "actual_consumed_bytes"
    ].to_numpy(float)
    initial["actual_peak_mib"] = ap_oof["actual_peak_mib"].to_numpy(float)
    ap_oof_map = _prediction_map(ap_oof, oof=True)
    apc_oof_map = _prediction_map(apc_oof, oof=True)
    scaler = joblib.load(
        ROOT / "models" / f"seed_{seed}" / "signature_scaler.joblib"
    )
    policy_root = ROOT / "policies" / f"seed_{seed}"
    with (policy_root / "a_plus_p.json").open(encoding="utf-8") as handle:
        ap_policy = json.load(handle)
    with (policy_root / "a_plus_p_plus_c.json").open(
        encoding="utf-8"
    ) as handle:
        apc_policy = json.load(handle)
    ap_allocation = crossfit_active_allocation(
        initial,
        ap_oof_map,
        scaler,
        ap_policy,
        folds=5,
    )
    apc_allocation = crossfit_active_allocation(
        initial,
        apc_oof_map,
        scaler,
        apc_policy,
        folds=5,
    )
    initial["ap_active_request_mib"] = ap_allocation
    initial["apc_active_request_mib"] = apc_allocation

    test = _prefix_predictions(test, ap_test, "ap", oof=False)
    test = _prefix_predictions(test, apc_test, "apc", oof=False)
    test["c_hat_bytes"] = c_test["c_hat_bytes"].to_numpy(float)
    test["actual_consumed_bytes"] = c_test[
        "actual_consumed_bytes"
    ].to_numpy(float)
    test["actual_peak_mib"] = ap_test["actual_peak_mib"].to_numpy(float)
    test["ap_active_request_mib"] = ap_test[
        "first_allocation_mib"
    ].to_numpy(float)
    test["apc_active_request_mib"] = apc_test[
        "first_allocation_mib"
    ].to_numpy(float)
    return initial, test


def crossfit_gate_scores(
    initial: pd.DataFrame,
    scenario: str,
    *,
    seed: int,
    n_jobs: int,
) -> pd.DataFrame:
    assignments = grouped_fold_assignments(initial, 5)
    scored = [None] * len(initial)
    for fold in range(5):
        fit = initial.loc[assignments != fold].reset_index(drop=True)
        validation_positions = np.flatnonzero(assignments == fold)
        validation = initial.iloc[validation_positions].reset_index(drop=True)
        artifact = fit_gate_models(
            fit,
            scenario,
            seed=seed + fold,
            n_jobs=n_jobs,
        )
        fold_scores = score_gate_models(artifact, validation)
        for position, record in zip(
            validation_positions,
            fold_scores.to_dict(orient="records"),
        ):
            scored[int(position)] = record
    if any(value is None for value in scored):
        raise AssertionError("missing crossfit gate score")
    return pd.DataFrame.from_records(scored)


def add_evaluation_targets(
    frame: pd.DataFrame,
    scenario: str,
) -> pd.DataFrame:
    result = add_gate_targets(frame, scenario)
    result["process_peak_top5"] = result["process_peak_rank"].ge(0.95)
    result["ap_underallocation"] = (
        result["ap_active_request_mib"] < result["actual_peak_mib"]
    )
    result["apc_underallocation"] = (
        result["apc_active_request_mib"] < result["actual_peak_mib"]
    )
    result["rescued_by_apc"] = (
        result["ap_underallocation"] & ~result["apc_underallocation"]
    )
    result["introduced_by_apc"] = (
        ~result["ap_underallocation"] & result["apc_underallocation"]
    )
    result["apc_request_delta_mib"] = (
        result["apc_active_request_mib"] - result["ap_active_request_mib"]
    )
    return result


def stratified_random_plan(
    frame: pd.DataFrame,
    count: int,
) -> tuple[dict[tuple[str, str], np.ndarray], dict[tuple[str, str], int]]:
    groups = {
        (str(workflow), str(process)): group.index.to_numpy(dtype=int)
        for (workflow, process), group in frame.groupby(
            ["workflow", "process"],
            sort=True,
            observed=True,
        )
    }
    quotas = proportional_quotas(
        pd.Series({key: len(value) for key, value in groups.items()}),
        count,
        guarantee_one=True,
    )
    return groups, quotas


def select_stratified(
    groups: dict[tuple[str, str], np.ndarray],
    quotas: dict[tuple[str, str], int],
    length: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    selected = np.zeros(length, dtype=bool)
    inclusion = np.zeros(length, dtype=float)
    for key, positions in groups.items():
        quota = int(quotas[key])
        inclusion[positions] = quota / len(positions)
        if quota:
            selected[rng.choice(positions, quota, replace=False)] = True
    return selected, inclusion


def ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else float("nan")


def selected_metrics(
    targets: pd.DataFrame,
    selected: np.ndarray,
) -> dict[str, float]:
    count = int(selected.sum())
    underallocation = targets["active_underallocation"].to_numpy(bool)
    shortfall = targets["active_shortfall_mib"].to_numpy(float)
    information = targets["heavy_information_target"].to_numpy(bool)
    peak5 = targets["process_peak_top5"].to_numpy(bool)
    peak10 = targets["process_peak_top10"].to_numpy(bool)
    surprise = targets["consumed_surprise_top10"].to_numpy(bool)
    ap_oom = targets["ap_underallocation"].to_numpy(bool)
    apc_oom = targets["apc_underallocation"].to_numpy(bool)
    rescued = targets["rescued_by_apc"].to_numpy(bool)
    introduced = targets["introduced_by_apc"].to_numpy(bool)
    process_keys = (
        targets["workflow"].astype(str) + "::" + targets["process"].astype(str)
    )
    return {
        "selected_tasks": count,
        "underallocation_recall": ratio(
            np.sum(selected & underallocation),
            underallocation.sum(),
        ),
        "underallocation_precision": ratio(
            np.sum(selected & underallocation),
            count,
        ),
        "shortfall_share": ratio(
            shortfall[selected].sum(),
            shortfall.sum(),
        ),
        "heavy_information_recall": ratio(
            np.sum(selected & information),
            information.sum(),
        ),
        "heavy_information_precision": ratio(
            np.sum(selected & information),
            count,
        ),
        "peak_top5_recall": ratio(np.sum(selected & peak5), peak5.sum()),
        "peak_top10_recall": ratio(np.sum(selected & peak10), peak10.sum()),
        "consumed_surprise_recall": ratio(
            np.sum(selected & surprise),
            surprise.sum(),
        ),
        "process_coverage": ratio(
            process_keys[selected].nunique(),
            process_keys.nunique(),
        ),
        "selected_ap_underallocations": float(np.sum(selected & ap_oom)),
        "selected_apc_underallocations": float(np.sum(selected & apc_oom)),
        "selected_rescued_by_apc": float(np.sum(selected & rescued)),
        "selected_introduced_by_apc": float(np.sum(selected & introduced)),
        "selected_ap_request_mib": float(
            targets.loc[selected, "ap_active_request_mib"].sum()
        ),
        "selected_apc_request_mib": float(
            targets.loc[selected, "apc_active_request_mib"].sum()
        ),
        "selected_request_change_percent": 100.0
        * ratio(
            targets.loc[selected, "apc_active_request_mib"].sum()
            - targets.loc[selected, "ap_active_request_mib"].sum(),
            targets.loc[selected, "ap_active_request_mib"].sum(),
        ),
    }


def weighted_quantile(
    values: np.ndarray,
    weights: np.ndarray,
    quantile: float,
) -> float:
    order = np.argsort(values)
    ordered_values = values[order]
    ordered_weights = weights[order]
    total = ordered_weights.sum()
    if total <= 0:
        return float("nan")
    cumulative = np.cumsum(ordered_weights) / total
    return float(ordered_values[np.searchsorted(cumulative, quantile)])


def inclusion_diagnostics(
    targets: pd.DataFrame,
    selected: np.ndarray,
    inclusion: np.ndarray,
    *,
    clip_weight: float = 20.0,
) -> dict[str, float]:
    probabilities = inclusion[selected]
    if not len(probabilities) or np.any(probabilities <= 0):
        return {
            "zero_probability_selected": float(np.sum(probabilities <= 0)),
            "weight_max": float("nan"),
            "effective_sample_size": float("nan"),
        }
    weights = 1.0 / probabilities
    clipped = np.minimum(weights, clip_weight)
    consumed = targets.loc[selected, "actual_consumed_bytes"].to_numpy(float)
    full = targets["actual_consumed_bytes"].to_numpy(float)
    full_quantiles = np.quantile(full, [0.5, 0.95, 0.99])
    record = {
        "zero_probability_tasks": float(np.sum(inclusion <= 0)),
        "zero_probability_selected": 0.0,
        "inclusion_probability_min": float(probabilities.min()),
        "inclusion_probability_median": float(np.median(probabilities)),
        "weight_median": float(np.median(weights)),
        "weight_p95": float(np.quantile(weights, 0.95)),
        "weight_max": float(weights.max()),
        "effective_sample_size": float(
            weights.sum() ** 2 / np.square(weights).sum()
        ),
        "full_consumed_mean_bytes": float(full.mean()),
        "unweighted_consumed_mean_bytes": float(consumed.mean()),
        "horvitz_thompson_mean_bytes": float(
            np.sum(consumed * weights) / len(targets)
        ),
        "self_normalized_mean_bytes": float(
            np.sum(consumed * weights) / weights.sum()
        ),
        "clipped_self_normalized_mean_bytes": float(
            np.sum(consumed * clipped) / clipped.sum()
        ),
    }
    for quantile, truth in zip((0.5, 0.95, 0.99), full_quantiles):
        label = f"q{int(quantile * 100):02d}"
        estimate = weighted_quantile(consumed, weights, quantile)
        clipped_estimate = weighted_quantile(consumed, clipped, quantile)
        record[f"full_consumed_{label}_bytes"] = float(truth)
        record[f"weighted_consumed_{label}_bytes"] = estimate
        record[f"weighted_consumed_{label}_ape_percent"] = (
            100.0 * abs(estimate - truth) / max(float(truth), 1.0)
        )
        record[f"clipped_consumed_{label}_ape_percent"] = (
            100.0 * abs(clipped_estimate - truth) / max(float(truth), 1.0)
        )
    return record


def lane_metrics(
    targets: pd.DataFrame,
    selected: np.ndarray,
    lanes: np.ndarray,
) -> list[dict[str, Any]]:
    records = []
    for lane in ("risk", "heavy_information", "discovery", "process_exploration"):
        mask = selected & (lanes == lane)
        values = selected_metrics(targets, mask)
        records.append({"lane": lane, **values})
    return records


def summarize_repetitions(frame: pd.DataFrame) -> pd.DataFrame:
    keys = ["scenario", "budget", "policy"]
    numeric = [
        column
        for column in frame.columns
        if column not in keys + ["repetition"]
        and pd.api.types.is_numeric_dtype(frame[column])
    ]
    return (
        frame.groupby(keys, sort=True, observed=True)[numeric]
        .agg(["mean", "std", "median"])
        .reset_index()
        .set_axis(
            keys
            + [
                f"{column}_{statistic}"
                for column, statistic in frame.groupby(
                    keys,
                    observed=True,
                )[numeric]
                .agg(["mean", "std", "median"])
                .columns
            ],
            axis=1,
        )
    )


def holm_adjust(p_values: np.ndarray) -> np.ndarray:
    order = np.argsort(p_values)
    adjusted = np.empty(len(p_values), dtype=float)
    running = 0.0
    count = len(p_values)
    for rank, position in enumerate(order):
        value = min((count - rank) * p_values[position], 1.0)
        running = max(running, value)
        adjusted[position] = running
    return adjusted


def comparison_statistics(repetitions: pd.DataFrame) -> pd.DataFrame:
    records = []
    for (scenario, budget), group in repetitions.groupby(
        ["scenario", "budget"],
        sort=True,
    ):
        gate = group.loc[group["policy"] == "three_gate"].sort_values(
            "repetition"
        )
        for baseline in ("uniform_random", "process_stratified_random"):
            control = group.loc[group["policy"] == baseline].sort_values(
                "repetition"
            )
            for metric in CORE_METRICS:
                difference = gate[metric].to_numpy(float) - control[
                    metric
                ].to_numpy(float)
                finite = difference[np.isfinite(difference)]
                records.append(
                    {
                        "scenario": scenario,
                        "budget": budget,
                        "baseline": baseline,
                        "metric": metric,
                        "mean_difference": float(np.mean(finite)),
                        "ci95_low": float(np.quantile(finite, 0.025)),
                        "ci95_high": float(np.quantile(finite, 0.975)),
                        "one_sided_p": float(
                            (1 + np.sum(finite <= 0)) / (len(finite) + 1)
                        ),
                        "repetitions": int(len(finite)),
                    }
                )
    result = pd.DataFrame.from_records(records)
    result["holm_adjusted_p"] = np.nan
    for _, positions in result.groupby(
        ["scenario", "baseline", "metric"],
        sort=False,
    ).groups.items():
        index = list(positions)
        result.loc[index, "holm_adjusted_p"] = holm_adjust(
            result.loc[index, "one_sided_p"].to_numpy(float)
        )
    return result


def validation_metrics(
    scored: pd.DataFrame,
    scenario: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    targets = add_gate_targets(scored, scenario)
    records = []
    bins = []
    scopes = [("all", targets)] + [
        (str(workflow), group)
        for workflow, group in targets.groupby("workflow", sort=True)
    ]
    for scope, group in scopes:
        for label, probability in (
            ("active_underallocation", "gate_risk_probability"),
            ("heavy_information", "gate_information_probability"),
        ):
            actual = group[label if label == "active_underallocation" else "heavy_information_target"].astype(
                int
            )
            predicted = group[probability].to_numpy(float)
            records.append(
                {
                    "scenario": scenario,
                    "scope": scope,
                    "label": label,
                    "rows": len(group),
                    "positives": int(actual.sum()),
                    "prevalence": float(actual.mean()),
                    "average_precision": (
                        float(average_precision_score(actual, predicted))
                        if actual.nunique() == 2
                        else float("nan")
                    ),
                    "roc_auc": (
                        float(roc_auc_score(actual, predicted))
                        if actual.nunique() == 2
                        else float("nan")
                    ),
                    "brier_score": float(
                        brier_score_loss(actual, predicted)
                    ),
                }
            )
        all_actual = group["active_underallocation"].astype(int)
        temporary = pd.DataFrame(
            {
                "actual": all_actual,
                "probability": group["gate_risk_probability"],
            }
        )
        temporary["bin"] = pd.qcut(
            temporary["probability"],
            q=min(10, temporary["probability"].nunique()),
            duplicates="drop",
        )
        for number, (_, values) in enumerate(
            temporary.groupby("bin", observed=True, sort=True)
        ):
            bins.append(
                {
                    "scenario": scenario,
                    "scope": scope,
                    "bin": number,
                    "rows": len(values),
                    "mean_predicted_probability": float(
                        values["probability"].mean()
                    ),
                    "observed_frequency": float(values["actual"].mean()),
                }
            )
    return pd.DataFrame(records), pd.DataFrame(bins)


def run_policy_repetitions(
    scored: pd.DataFrame,
    scenario: str,
    budgets: list[float],
    repetitions: int,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    targets = add_evaluation_targets(scored, scenario)
    metric_records = []
    lane_records = []
    inclusion_records = []
    example_records = []
    for budget in budgets:
        count = min(int(round(len(targets) * budget)), len(targets))
        plan = build_gate_plan(
            targets,
            count,
            lane_fractions=DEFAULT_LANE_FRACTIONS,
        )
        groups, quotas = stratified_random_plan(targets, count)
        for repetition in range(repetitions):
            rng_gate = np.random.default_rng(seed + 100_000 * repetition + count)
            rng_uniform = np.random.default_rng(
                seed + 100_000 * repetition + count + 17
            )
            rng_stratified = np.random.default_rng(
                seed + 100_000 * repetition + count + 31
            )
            gate_selected, gate_inclusion, lanes = select_from_plan(
                targets,
                plan,
                rng_gate,
            )
            uniform_selected = np.zeros(len(targets), dtype=bool)
            uniform_selected[
                rng_uniform.choice(len(targets), count, replace=False)
            ] = True
            uniform_inclusion = np.full(len(targets), count / len(targets))
            stratified_selected, stratified_inclusion = select_stratified(
                groups,
                quotas,
                len(targets),
                rng_stratified,
            )
            for policy, selected, inclusion in (
                ("three_gate", gate_selected, gate_inclusion),
                ("uniform_random", uniform_selected, uniform_inclusion),
                (
                    "process_stratified_random",
                    stratified_selected,
                    stratified_inclusion,
                ),
            ):
                metric_records.append(
                    {
                        "scenario": scenario,
                        "budget": budget,
                        "policy": policy,
                        "repetition": repetition,
                        **selected_metrics(targets, selected),
                    }
                )
                if policy == "three_gate":
                    inclusion_records.append(
                        {
                            "scenario": scenario,
                            "budget": budget,
                            "repetition": repetition,
                            **inclusion_diagnostics(
                                targets,
                                selected,
                                inclusion,
                            ),
                        }
                    )
            for values in lane_metrics(targets, gate_selected, lanes):
                lane_records.append(
                    {
                        "scenario": scenario,
                        "budget": budget,
                        "repetition": repetition,
                        **values,
                    }
                )
            if repetition == 0 and np.isclose(budget, 0.20):
                example = targets.loc[
                    :,
                    [
                        "logical_task_id",
                        "workflow",
                        "process",
                        "actual_peak_mib",
                        "actual_consumed_bytes",
                        "c_hat_bytes",
                        "ap_active_request_mib",
                        "apc_active_request_mib",
                        "ap_q500_prediction_mib",
                        "apc_q500_prediction_mib",
                        "gate_risk_probability",
                        "gate_expected_shortfall_mib",
                        "gate_information_probability",
                        "gate_risk_score",
                        "gate_information_score",
                        "gate_discovery_score",
                    ],
                ].copy()
                example["scenario"] = scenario
                example["audit_flag"] = gate_selected
                example["inclusion_probability"] = gate_inclusion
                example["selection_lane"] = lanes
                example_records.extend(example.to_dict(orient="records"))
    return (
        pd.DataFrame.from_records(metric_records),
        pd.DataFrame.from_records(lane_records),
        pd.DataFrame.from_records(inclusion_records),
        pd.DataFrame.from_records(example_records),
    )


def sensitivity_analysis(
    crossfit_scores: pd.DataFrame,
    scenario: str,
    repetitions: int,
    seed: int,
) -> pd.DataFrame:
    targets = add_evaluation_targets(crossfit_scores, scenario)
    count = int(round(0.20 * len(targets)))
    records = []
    score_variants = {
        "risk_70_shortfall_30": (0.70, 0.30),
        "risk_100_shortfall_0": (1.0, 0.0),
        "risk_50_shortfall_50": (0.50, 0.50),
    }
    risk_rank = targets.groupby("workflow", observed=True)[
        "gate_risk_probability"
    ].transform(lambda values: values.rank(pct=True))
    shortfall_rank = targets.groupby("workflow", observed=True)[
        "gate_expected_shortfall_mib"
    ].transform(lambda values: values.rank(pct=True))
    original = targets["gate_risk_score"].copy()
    for score_name, (risk_weight, shortfall_weight) in score_variants.items():
        targets["gate_risk_score"] = (
            risk_weight * risk_rank + shortfall_weight * shortfall_rank
        )
        for mix_name, fractions in LANE_MIXES.items():
            plan = build_gate_plan(
                targets,
                count,
                lane_fractions=fractions,
            )
            for repetition in range(repetitions):
                selected, _, _ = select_from_plan(
                    targets,
                    plan,
                    np.random.default_rng(
                        seed + repetition * 100_000 + len(records)
                    ),
                )
                values = selected_metrics(targets, selected)
                objective = (
                    0.45 * values["underallocation_recall"]
                    + 0.25 * values["shortfall_share"]
                    + 0.20 * values["heavy_information_recall"]
                    + 0.10 * values["process_coverage"]
                )
                records.append(
                    {
                        "scenario": scenario,
                        "budget": 0.20,
                        "risk_score_mix": score_name,
                        "lane_mix": mix_name,
                        "repetition": repetition,
                        "predeclared_objective": objective,
                        **values,
                    }
                )
    targets["gate_risk_score"] = original
    return pd.DataFrame.from_records(records)


def workflow_summary(
    example: pd.DataFrame,
) -> pd.DataFrame:
    if example.empty:
        return pd.DataFrame()
    records = []
    for (scenario, workflow), group in example.groupby(
        ["scenario", "workflow"],
        sort=True,
    ):
        selected = group["audit_flag"].to_numpy(bool)
        targets = add_evaluation_targets(group, scenario)
        records.append(
            {
                "scenario": scenario,
                "policy": "three_gate",
                "workflow": workflow,
                "budget": 0.20,
                **selected_metrics(targets, selected),
            }
        )
    return pd.DataFrame.from_records(records)


def make_figures(
    summary: pd.DataFrame,
    workflow: pd.DataFrame,
    output: Path,
) -> None:
    labels = {
        "three_gate": "CAMP three-gate",
        "uniform_random": "Uniform random",
        "process_stratified_random": "Process-stratified random",
    }
    colors = {
        "three_gate": "#0072B2",
        "uniform_random": "#999999",
        "process_stratified_random": "#D55E00",
    }
    metrics = (
        ("underallocation_recall_mean", "At-risk tasks found"),
        ("shortfall_share_mean", "Memory shortfall captured"),
        ("heavy_information_recall_mean", "Informative tasks found"),
        ("peak_top5_recall_mean", "Top-5% peak tasks found"),
    )
    for scenario in SCENARIOS:
        subset = summary.loc[summary["scenario"] == scenario]
        figure, axes = plt.subplots(2, 2, figsize=(10.5, 7.2), sharex=True)
        for axis, (metric, title) in zip(axes.flat, metrics):
            for policy in POLICIES:
                values = subset.loc[subset["policy"] == policy].sort_values(
                    "budget"
                )
                axis.plot(
                    100 * values["budget"],
                    100 * values[metric],
                    marker="o",
                    linewidth=2,
                    color=colors[policy],
                    label=labels[policy],
                )
            axis.set_title(title)
            axis.set_ylabel("Recall / share (%)")
            axis.grid(axis="y", alpha=0.25)
            axis.set_ylim(bottom=0)
        for axis in axes[-1]:
            axis.set_xlabel("Audit budget (% of tasks)")
        handles, legend_labels = axes[0, 0].get_legend_handles_labels()
        figure.legend(
            handles,
            legend_labels,
            loc="upper center",
            bbox_to_anchor=(0.5, 0.955),
            ncol=3,
            frameon=False,
        )
        figure.suptitle(
            "Design 3 selective-audit yield: "
            + ("A+P" if scenario == "a_plus_p" else "A+P+C"),
            y=0.995,
        )
        figure.tight_layout(rect=(0.0, 0.0, 1.0, 0.90))
        stem = f"design3_{scenario}_selective_audit_yield"
        figure.savefig(output / f"{stem}.png", dpi=220, bbox_inches="tight")
        figure.savefig(output / f"{stem}.pdf", bbox_inches="tight")
        plt.close(figure)

    if len(workflow):
        values = workflow.loc[
            workflow["scenario"].eq("a_plus_p")
            & workflow["policy"].eq("three_gate")
        ].sort_values(
            "underallocation_recall",
            ascending=True,
        )
        figure, axis = plt.subplots(figsize=(8.8, 4.8))
        positions = np.arange(len(values))
        axis.barh(
            positions - 0.18,
            100 * values["underallocation_recall"],
            height=0.34,
            label="At-risk tasks found",
            color="#0072B2",
        )
        axis.barh(
            positions + 0.18,
            100 * values["heavy_information_recall"],
            height=0.34,
            label="Informative tasks found",
            color="#E69F00",
        )
        axis.set_yticks(positions, values["workflow"])
        axis.set_xlabel("Recall at 20% audit budget (%)")
        axis.grid(axis="x", alpha=0.25)
        axis.legend(frameon=False)
        figure.tight_layout()
        figure.savefig(
            output / "design3_workflow_yield_at_20_percent.png",
            dpi=220,
            bbox_inches="tight",
        )
        figure.savefig(
            output / "design3_workflow_yield_at_20_percent.pdf",
            bbox_inches="tight",
        )
        plt.close(figure)

    metric_names = (
        ("underallocation_recall_mean", "At-risk\nrecall"),
        ("shortfall_share_mean", "Shortfall\ncaptured"),
        ("heavy_information_recall_mean", "Informative\nrecall"),
        ("peak_top5_recall_mean", "Top-5% peak\nrecall"),
    )
    for scenario in SCENARIOS:
        values = summary.loc[
            summary["scenario"].eq(scenario) & summary["budget"].eq(0.20)
        ].set_index("policy")
        if not set(POLICIES).issubset(values.index):
            continue
        figure, axis = plt.subplots(figsize=(9.0, 4.8))
        positions = np.arange(len(metric_names))
        width = 0.25
        for offset, policy in zip((-1, 0, 1), POLICIES):
            heights = [
                100 * float(values.loc[policy, metric])
                for metric, _ in metric_names
            ]
            bars = axis.bar(
                positions + offset * width,
                heights,
                width=width,
                label=labels[policy],
                color=colors[policy],
            )
            axis.bar_label(
                bars,
                labels=[f"{height:.1f}" for height in heights],
                padding=2,
                fontsize=8,
            )
        axis.set_xticks(
            positions,
            [label for _, label in metric_names],
        )
        axis.set_ylabel("Captured targets (%)")
        axis.set_ylim(
            0,
            max(
                40.0,
                1.16
                * max(
                    100 * float(values.loc[policy, metric])
                    for policy in POLICIES
                    for metric, _ in metric_names
                ),
            ),
        )
        axis.grid(axis="y", alpha=0.25)
        axis.legend(frameon=False, ncol=3, loc="upper left")
        axis.set_title(
            "20% audit budget: "
            + ("A+P mode" if scenario == "a_plus_p" else "A+P+C mode")
        )
        figure.tight_layout()
        stem = f"design3_{scenario}_20_percent_comparison"
        figure.savefig(output / f"{stem}.png", dpi=220, bbox_inches="tight")
        figure.savefig(output / f"{stem}.pdf", bbox_inches="tight")
        plt.close(figure)


def main() -> None:
    args = arguments()
    started = time.time()
    output = ROOT / "results" / f"seed_{args.seed}" / "gating_design3"
    output.mkdir(parents=True, exist_ok=True)
    initial, test = load_analysis_frames(args.seed)
    all_repetitions = []
    all_lanes = []
    all_inclusion = []
    all_examples = []
    all_validation = []
    all_bins = []
    all_sensitivity = []
    scored_test_frames = []

    for scenario_position, scenario in enumerate(SCENARIOS):
        crossfit = crossfit_gate_scores(
            initial,
            scenario,
            seed=args.seed + 1000 * scenario_position,
            n_jobs=args.n_jobs,
        )
        artifact = fit_gate_models(
            initial,
            scenario,
            seed=args.seed + 10_000 * scenario_position,
            n_jobs=args.n_jobs,
        )
        test_scores = score_gate_models(artifact, test)
        test_scores["scenario"] = scenario
        scored_test_frames.append(test_scores)
        validation, bins = validation_metrics(test_scores, scenario)
        all_validation.append(validation)
        all_bins.append(bins)
        all_sensitivity.append(
            sensitivity_analysis(
                crossfit,
                scenario,
                args.sensitivity_repetitions,
                args.seed + 20_000 * scenario_position,
            )
        )
        repetitions, lanes, inclusion, examples = run_policy_repetitions(
            test_scores,
            scenario,
            list(args.budgets),
            args.repetitions,
            args.seed + 30_000 * scenario_position,
        )
        all_repetitions.append(repetitions)
        all_lanes.append(lanes)
        all_inclusion.append(inclusion)
        all_examples.append(examples)

    repetitions = pd.concat(all_repetitions, ignore_index=True)
    lanes = pd.concat(all_lanes, ignore_index=True)
    inclusion = pd.concat(all_inclusion, ignore_index=True)
    examples = pd.concat(all_examples, ignore_index=True)
    validation = pd.concat(all_validation, ignore_index=True)
    bins = pd.concat(all_bins, ignore_index=True)
    sensitivity = pd.concat(all_sensitivity, ignore_index=True)
    task_scores = pd.concat(scored_test_frames, ignore_index=True)
    summary = summarize_repetitions(repetitions)
    comparisons = comparison_statistics(repetitions)
    workflow = workflow_summary(examples)
    sensitivity_summary = (
        sensitivity.groupby(
            ["scenario", "risk_score_mix", "lane_mix"],
            sort=True,
        )
        .agg(
            objective_mean=("predeclared_objective", "mean"),
            objective_std=("predeclared_objective", "std"),
            underallocation_recall=("underallocation_recall", "mean"),
            shortfall_share=("shortfall_share", "mean"),
            heavy_information_recall=("heavy_information_recall", "mean"),
            process_coverage=("process_coverage", "mean"),
        )
        .reset_index()
    )
    lane_summary = (
        lanes.groupby(["scenario", "budget", "lane"], sort=True)
        .mean(numeric_only=True)
        .reset_index()
    )
    inclusion_summary = (
        inclusion.groupby(["scenario", "budget"], sort=True)
        .mean(numeric_only=True)
        .reset_index()
    )
    score_columns = [
        "logical_task_id",
        "workflow",
        "process",
        "scenario",
        "history_scope",
        "history_scope_support",
        "history_scope_confidence",
        "c_hat_bytes",
        "ap_active_request_mib",
        "apc_active_request_mib",
        "ap_q500_prediction_mib",
        "apc_q500_prediction_mib",
        "gate_risk_probability",
        "gate_expected_shortfall_mib",
        "gate_information_probability",
        "gate_risk_score",
        "gate_information_score",
        "gate_discovery_score",
    ]
    task_scores = task_scores.loc[:, score_columns]

    repetitions.to_csv(output / "budget_repetitions.csv", index=False)
    summary.to_csv(output / "budget_summary.csv", index=False)
    comparisons.to_csv(output / "budget_comparison_statistics.csv", index=False)
    workflow.to_csv(output / "workflow_20_percent_summary.csv", index=False)
    lanes.to_csv(output / "lane_repetitions.csv", index=False)
    lane_summary.to_csv(output / "lane_summary.csv", index=False)
    inclusion.to_csv(output / "inclusion_weight_repetitions.csv", index=False)
    inclusion_summary.to_csv(
        output / "inclusion_weight_summary.csv",
        index=False,
    )
    validation.to_csv(output / "gate_model_validation.csv", index=False)
    bins.to_csv(output / "risk_calibration_bins.csv", index=False)
    sensitivity.to_csv(output / "policy_sensitivity_repetitions.csv", index=False)
    sensitivity_summary.to_csv(output / "policy_sensitivity_summary.csv", index=False)
    examples.to_csv(output / "selected_tasks_20_percent.tsv", sep="\t", index=False)
    task_scores.to_csv(output / "task_gate_scores.tsv", sep="\t", index=False)
    make_figures(summary, workflow, output)
    write_json(
        output / "evaluation_manifest.json",
        {
            "design": "CAMP Design 3 three-gate selective auditing",
            "seed": args.seed,
            "initial_rows": len(initial),
            "test_rows": len(test),
            "scenarios": list(SCENARIOS),
            "budgets": list(args.budgets),
            "repetitions": args.repetitions,
            "sensitivity_repetitions": args.sensitivity_repetitions,
            "lane_fractions": DEFAULT_LANE_FRACTIONS,
            "selection_contract": (
                "gate features are predecision-only; current peak RSS and "
                "current consumed bytes are read only after audit sets freeze"
            ),
            "allocation_label_contract": (
                "initial gate labels use grouped-fold-external residual "
                "histories; test labels use frozen Design 3 task allocations"
            ),
            "elapsed_seconds": time.time() - started,
            "python": sys.version,
        },
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "elapsed_seconds": time.time() - started,
                "rows": len(test),
                "repetitions": len(repetitions),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
