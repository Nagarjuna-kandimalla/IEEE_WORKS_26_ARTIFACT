"""Leakage-safe three-gate selective auditing for CAMP."""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from modeling import (
    CATEGORICAL_STATIC,
    NUMERIC_STATIC,
    materialize_model_features,
)


SCENARIOS = ("a_plus_p", "a_plus_p_plus_c")
DEFAULT_LANE_FRACTIONS = {
    "risk": 0.80,
    "information": 0.10,
    "discovery": 0.10,
}
DISCOVERY_RANDOM_FRACTION = 0.50

PEAK_HISTORY_COLUMNS = tuple(
    f"history_i{scope}_{suffix}"
    for scope in range(6)
    for suffix in (
        "support",
        "peak_p50_bytes",
        "peak_p95_bytes",
        "peak_p99_bytes",
        "peak_log_spread",
        "confidence",
    )
)
CONSUMED_HISTORY_COLUMNS = tuple(
    f"history_i{scope}_{suffix}"
    for scope in range(6)
    for suffix in (
        "consumed_p50_bytes",
        "consumed_p95_bytes",
        "consumed_p99_bytes",
        "consumed_to_input_p50",
    )
)
HISTORY_CONTEXT_COLUMNS = (
    "history_i2_distance_p50",
    "history_i2_distance_min",
    "history_scope_support",
    "history_scope_confidence",
)
POST_OUTCOME_COLUMNS = frozenset(
    {
        "peak_memory_bytes",
        "ebpf_total_consumed_bytes",
        "ebpf_read_return_bytes",
        "ebpf_mmap_page_fault_bytes",
        "runtime_seconds",
        "observed_oom_flag",
        "actual_peak_mib",
        "actual_consumed_bytes",
        "initial_oom",
        "native_unresolved_oom",
        "active_shortfall_mib",
        "active_underallocation",
        "active_absolute_log_residual",
        "heavy_information_target",
        "process_peak_top10",
        "consumed_surprise_top10",
        "history_error_gain",
    }
)


def _rank(values: pd.Series) -> pd.Series:
    if len(values) <= 1:
        return pd.Series(1.0, index=values.index)
    return values.rank(method="average", pct=True)


def grouped_rank(frame: pd.DataFrame, column: str) -> pd.Series:
    """Rank a signal within workflow/process to preserve local tails."""

    return frame.groupby(
        ["workflow", "process"],
        sort=False,
        observed=True,
    )[column].transform(_rank)


def _active_prefix(scenario: str) -> str:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown gate scenario: {scenario}")
    return "ap" if scenario == "a_plus_p" else "apc"


def add_gate_targets(
    frame: pd.DataFrame,
    scenario: str,
) -> pd.DataFrame:
    """Build post-run labels; callers must not pass them to gate features."""

    prefix = _active_prefix(scenario)
    result = frame.copy()
    actual = np.maximum(
        pd.to_numeric(result["actual_peak_mib"], errors="raise").to_numpy(),
        1e-9,
    )
    request = np.maximum(
        pd.to_numeric(
            result[f"{prefix}_active_request_mib"],
            errors="raise",
        ).to_numpy(),
        1e-9,
    )
    active_point = np.maximum(
        pd.to_numeric(
            result[f"{prefix}_q500_prediction_mib"],
            errors="raise",
        ).to_numpy(),
        1e-9,
    )
    ap_point = np.maximum(
        pd.to_numeric(
            result["ap_q500_prediction_mib"],
            errors="raise",
        ).to_numpy(),
        1e-9,
    )
    apc_point = np.maximum(
        pd.to_numeric(
            result["apc_q500_prediction_mib"],
            errors="raise",
        ).to_numpy(),
        1e-9,
    )
    actual_consumed = np.maximum(
        pd.to_numeric(
            result["actual_consumed_bytes"],
            errors="raise",
        ).to_numpy(),
        1.0,
    )
    c_hat = np.maximum(
        pd.to_numeric(result["c_hat_bytes"], errors="raise").to_numpy(),
        1.0,
    )

    result["active_shortfall_mib"] = np.maximum(actual - request, 0.0)
    result["active_underallocation"] = result[
        "active_shortfall_mib"
    ].gt(0)
    result["active_absolute_log_residual"] = np.abs(
        np.log(actual) - np.log(active_point)
    )
    result["ap_absolute_log_residual"] = np.abs(
        np.log(actual) - np.log(ap_point)
    )
    result["apc_absolute_log_residual"] = np.abs(
        np.log(actual) - np.log(apc_point)
    )
    result["history_error_gain"] = (
        result["ap_absolute_log_residual"]
        - result["apc_absolute_log_residual"]
    )
    result["consumed_prediction_log_error"] = np.abs(
        np.log(actual_consumed) - np.log(c_hat)
    )
    result["process_peak_rank"] = grouped_rank(result, "actual_peak_mib")
    result["active_residual_rank"] = grouped_rank(
        result,
        "active_absolute_log_residual",
    )
    result["consumed_surprise_rank"] = grouped_rank(
        result,
        "consumed_prediction_log_error",
    )
    result["process_peak_top10"] = result["process_peak_rank"].ge(0.90)
    result["consumed_surprise_top10"] = result[
        "consumed_surprise_rank"
    ].ge(0.90)

    active_tail = (
        result["process_peak_top10"]
        | result["active_residual_rank"].ge(0.90)
        | result["active_underallocation"]
    )
    consumed_value = (
        result["consumed_surprise_top10"]
        | result["history_error_gain"].gt(0)
    )
    result["heavy_information_target"] = active_tail & consumed_value
    return result


def build_gate_features(
    frame: pd.DataFrame,
    scenario: str,
) -> tuple[pd.DataFrame, tuple[str, ...], tuple[str, ...]]:
    """Build legal pre-audit features for one active CAMP allocator."""

    prefix = _active_prefix(scenario)
    materialized = materialize_model_features(frame)
    numeric = list(NUMERIC_STATIC)
    categorical = list(CATEGORICAL_STATIC) + ["history_scope"]

    history_sources = list(PEAK_HISTORY_COLUMNS) + list(
        HISTORY_CONTEXT_COLUMNS
    )
    if scenario == "a_plus_p_plus_c":
        history_sources.extend(CONSUMED_HISTORY_COLUMNS)
    for source in history_sources:
        if source.endswith(("_confidence", "_log_spread")) or "distance_" in source:
            target = source
        else:
            target = f"log1p_{source}"
        numeric.append(target)

    if scenario == "a_plus_p_plus_c":
        numeric.append("log1p_c_hat_bytes")

    prediction_sources = {
        "active_q500_mib": f"{prefix}_q500_prediction_mib",
        "active_q900_mib": f"{prefix}_q900_prediction_mib",
        "active_q950_mib": f"{prefix}_q950_prediction_mib",
        "active_q990_mib": f"{prefix}_q990_prediction_mib",
        "active_q995_mib": f"{prefix}_q995_prediction_mib",
        "active_request_mib": f"{prefix}_active_request_mib",
    }
    for target, source in prediction_sources.items():
        values = pd.to_numeric(materialized[source], errors="coerce").clip(
            lower=0.0
        )
        column = f"log1p_{target}"
        materialized[column] = np.log1p(values)
        numeric.append(column)

    q900 = np.maximum(
        pd.to_numeric(
            materialized[prediction_sources["active_q900_mib"]],
            errors="coerce",
        ).to_numpy(),
        1e-9,
    )
    q995 = np.maximum(
        pd.to_numeric(
            materialized[prediction_sources["active_q995_mib"]],
            errors="coerce",
        ).to_numpy(),
        1e-9,
    )
    materialized["active_tail_interval_log"] = np.maximum(
        np.log(q995) - np.log(q900),
        0.0,
    )
    numeric.append("active_tail_interval_log")

    global_source = f"{prefix}_global_q995_prediction_mib"
    if global_source in materialized:
        global_q995 = np.maximum(
            pd.to_numeric(
                materialized[global_source],
                errors="coerce",
            ).to_numpy(),
            1e-9,
        )
        materialized["active_local_global_spread_log"] = np.abs(
            np.log(q995) - np.log(global_q995)
        )
    else:
        materialized["active_local_global_spread_log"] = 0.0
    numeric.append("active_local_global_spread_log")

    features = materialized.loc[:, numeric + categorical].copy()
    leaked = POST_OUTCOME_COLUMNS.intersection(features.columns)
    if leaked:
        raise AssertionError(
            "gate features contain post-run outcomes: "
            + ", ".join(sorted(leaked))
        )
    return features, tuple(numeric), tuple(categorical)


def make_preprocessor(
    numeric: tuple[str, ...],
    categorical: tuple[str, ...],
) -> ColumnTransformer:
    numeric_pipe = Pipeline(
        [
            (
                "imputer",
                SimpleImputer(
                    strategy="median",
                    add_indicator=True,
                    keep_empty_features=True,
                ),
            )
        ]
    )
    categorical_pipe = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            (
                "one_hot",
                OneHotEncoder(handle_unknown="ignore", sparse_output=True),
            ),
        ]
    )
    return ColumnTransformer(
        [
            ("numeric", numeric_pipe, list(numeric)),
            ("categorical", categorical_pipe, list(categorical)),
        ],
        sparse_threshold=1.0,
    )


def _classifier(seed: int, n_jobs: int) -> LGBMClassifier:
    return LGBMClassifier(
        objective="binary",
        n_estimators=350,
        learning_rate=0.03,
        num_leaves=15,
        min_child_samples=20,
        reg_lambda=5.0,
        class_weight="balanced",
        random_state=seed,
        verbosity=-1,
        n_jobs=n_jobs,
    )


def _regressor(seed: int, n_jobs: int) -> LGBMRegressor:
    return LGBMRegressor(
        objective="regression_l1",
        n_estimators=250,
        learning_rate=0.04,
        num_leaves=7,
        min_child_samples=5,
        reg_lambda=5.0,
        random_state=seed,
        verbosity=-1,
        n_jobs=n_jobs,
    )


def fit_gate_models(
    calibration: pd.DataFrame,
    scenario: str,
    *,
    seed: int,
    n_jobs: int,
) -> dict[str, Any]:
    """Fit active-risk, shortfall, and heavy-information models."""

    targets = add_gate_targets(calibration.reset_index(drop=True), scenario)
    features, numeric, categorical = build_gate_features(targets, scenario)
    transformer = make_preprocessor(numeric, categorical)
    transformed = transformer.fit_transform(features)
    labels = {
        "active_underallocation": targets[
            "active_underallocation"
        ].astype(int),
        "heavy_information": targets[
            "heavy_information_target"
        ].astype(int),
    }
    fitted: dict[str, Any] = {}
    for name, values in labels.items():
        if values.nunique() != 2:
            raise ValueError(f"gate label {name} is constant")
        model = _classifier(seed, n_jobs)
        model.fit(transformed, values)
        fitted[name] = model

    positive = targets["active_underallocation"].to_numpy(bool)
    if int(positive.sum()) < 5:
        raise ValueError("too few shortfalls to fit severity model")
    severity = _regressor(seed, n_jobs)
    severity.fit(
        transformed[positive],
        np.log1p(
            targets.loc[positive, "active_shortfall_mib"].to_numpy(float)
        ),
    )
    fitted["conditional_shortfall"] = severity
    return {
        "scenario": scenario,
        "preprocessor": transformer,
        "models": fitted,
        "feature_columns": tuple(features.columns),
        "numeric_columns": numeric,
        "categorical_columns": categorical,
        "training_rows": int(len(targets)),
        "label_counts": {
            name: int(values.sum()) for name, values in labels.items()
        },
    }


def _workflow_rank(frame: pd.DataFrame, values: np.ndarray) -> np.ndarray:
    temporary = pd.DataFrame(
        {
            "workflow": frame["workflow"].astype(str).to_numpy(),
            "value": np.asarray(values, dtype=float),
        }
    )
    return temporary.groupby(
        "workflow",
        sort=False,
        observed=True,
    )["value"].transform(_rank).to_numpy(float)


def score_gate_models(
    artifact: dict[str, Any],
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """Score all gates without reading current task outcomes."""

    scenario = str(artifact["scenario"])
    prefix = _active_prefix(scenario)
    features, _, _ = build_gate_features(
        frame.reset_index(drop=True),
        scenario,
    )
    if tuple(features.columns) != artifact["feature_columns"]:
        raise ValueError("gate feature schema changed")
    transformed = artifact["preprocessor"].transform(features)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="X does not have valid feature names",
        )
        risk_probability = artifact["models"][
            "active_underallocation"
        ].predict_proba(transformed)[:, 1]
        information_probability = artifact["models"][
            "heavy_information"
        ].predict_proba(transformed)[:, 1]
        conditional_shortfall = np.expm1(
            artifact["models"]["conditional_shortfall"].predict(transformed)
        ).clip(min=0.0)
    expected_shortfall = risk_probability * conditional_shortfall

    result = frame.reset_index(drop=True).copy()
    result["gate_risk_probability"] = risk_probability
    result["gate_expected_shortfall_mib"] = expected_shortfall
    result["gate_information_probability"] = information_probability

    q900 = np.maximum(
        pd.to_numeric(
            result[f"{prefix}_q900_prediction_mib"],
            errors="coerce",
        ).to_numpy(),
        1e-9,
    )
    q995 = np.maximum(
        pd.to_numeric(
            result[f"{prefix}_q995_prediction_mib"],
            errors="coerce",
        ).to_numpy(),
        1e-9,
    )
    interval = np.maximum(np.log(q995) - np.log(q900), 0.0)
    global_column = f"{prefix}_global_q995_prediction_mib"
    if global_column in result:
        global_q995 = np.maximum(
            pd.to_numeric(result[global_column], errors="coerce").to_numpy(),
            1e-9,
        )
        local_global = np.abs(np.log(q995) - np.log(global_q995))
    else:
        local_global = np.zeros(len(result), dtype=float)
    distance = (
        pd.to_numeric(
            result["history_i2_distance_p50"],
            errors="coerce",
        )
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
        .to_numpy(float)
    )
    confidence = (
        pd.to_numeric(
            result["history_scope_confidence"],
            errors="coerce",
        )
        .fillna(0.0)
        .clip(0.0, 1.0)
        .to_numpy(float)
    )

    result["gate_risk_score"] = (
        0.70 * _workflow_rank(result, risk_probability)
        + 0.30 * _workflow_rank(result, expected_shortfall)
    )
    result["gate_information_score"] = _workflow_rank(
        result,
        information_probability,
    )
    result["gate_discovery_score"] = (
        0.35 * _workflow_rank(result, interval)
        + 0.25 * _workflow_rank(result, local_global)
        + 0.20 * _workflow_rank(result, distance)
        + 0.20 * (1.0 - confidence)
    )
    return result


def proportional_quotas(
    sizes: pd.Series,
    count: int,
    *,
    guarantee_one: bool,
) -> dict[Any, int]:
    """Allocate an exact integer count proportionally without overfilling."""

    clean = sizes.astype(int)
    if count < 0 or count > int(clean.sum()):
        raise ValueError("quota count is outside available rows")
    quotas = pd.Series(0, index=clean.index, dtype=int)
    if count == 0 or not len(clean):
        return quotas.to_dict()
    remaining = count
    if guarantee_one and count >= len(clean):
        eligible = clean.gt(0)
        quotas.loc[eligible] = 1
        remaining -= int(eligible.sum())
    capacity = clean - quotas
    if remaining <= 0:
        return quotas.to_dict()
    raw = remaining * capacity / max(int(capacity.sum()), 1)
    base = np.floor(raw).astype(int)
    base = np.minimum(base, capacity)
    quotas += base
    remaining = count - int(quotas.sum())
    fractions = (raw - base).sort_values(ascending=False, kind="stable")
    while remaining:
        progressed = False
        for key in fractions.index:
            if quotas[key] < clean[key]:
                quotas[key] += 1
                remaining -= 1
                progressed = True
                if not remaining:
                    break
        if not progressed:
            raise AssertionError("unable to satisfy proportional quota")
    return {key: int(value) for key, value in quotas.items()}


@dataclass(frozen=True)
class GatePlan:
    count: int
    risk_positions: np.ndarray
    information_positions: np.ndarray
    discovery_positions: np.ndarray
    exploration_positions: dict[tuple[str, str], np.ndarray]
    exploration_quotas: dict[tuple[str, str], int]


def build_gate_plan(
    frame: pd.DataFrame,
    count: int,
    *,
    lane_fractions: dict[str, float] | None = None,
) -> GatePlan:
    """Freeze deterministic lane rankings and random exploration strata."""

    fractions = lane_fractions or DEFAULT_LANE_FRACTIONS
    if not math.isclose(sum(fractions.values()), 1.0, abs_tol=1e-9):
        raise ValueError("gate lane fractions must sum to one")
    workflow_sizes = frame.groupby("workflow", sort=True).size()
    workflow_quotas = proportional_quotas(
        workflow_sizes,
        count,
        guarantee_one=True,
    )
    risk: list[int] = []
    information: list[int] = []
    discovery: list[int] = []
    exploration_positions: dict[tuple[str, str], np.ndarray] = {}
    exploration_quotas: dict[tuple[str, str], int] = {}

    for workflow, rows in frame.groupby("workflow", sort=True):
        positions = rows.index.to_numpy(dtype=int)
        quota = workflow_quotas[str(workflow)]
        risk_count = int(math.floor(fractions["risk"] * quota))
        information_count = int(
            math.floor(fractions["information"] * quota)
        )
        discovery_total = quota - risk_count - information_count
        discovery_count = int(
            math.floor(
                (1.0 - DISCOVERY_RANDOM_FRACTION) * discovery_total
            )
        )
        random_count = discovery_total - discovery_count

        risk_order = positions[
            np.argsort(
                -frame.loc[positions, "gate_risk_score"].to_numpy(float),
                kind="stable",
            )
        ]
        chosen_risk = risk_order[:risk_count]
        risk.extend(chosen_risk.tolist())
        remaining = np.setdiff1d(positions, chosen_risk)

        info_order = remaining[
            np.argsort(
                -frame.loc[
                    remaining,
                    "gate_information_score",
                ].to_numpy(float),
                kind="stable",
            )
        ]
        chosen_info = info_order[:information_count]
        information.extend(chosen_info.tolist())
        remaining = np.setdiff1d(remaining, chosen_info)

        discovery_order = remaining[
            np.argsort(
                -frame.loc[
                    remaining,
                    "gate_discovery_score",
                ].to_numpy(float),
                kind="stable",
            )
        ]
        chosen_discovery = discovery_order[:discovery_count]
        discovery.extend(chosen_discovery.tolist())
        remaining = np.setdiff1d(remaining, chosen_discovery)

        groups = {
            (str(workflow), str(process)): group.index.to_numpy(dtype=int)
            for process, group in frame.loc[remaining].groupby(
                "process",
                sort=True,
                observed=True,
            )
        }
        if 0 < random_count < len(groups):
            # A small pending wave cannot give every process a random slot.
            # Pool that workflow's remainder so every task keeps nonzero pi.
            groups = {
                (str(workflow), "__pooled_small_wave__"): remaining
            }
        group_sizes = pd.Series(
            {key: len(value) for key, value in groups.items()},
            dtype=int,
        )
        local_quotas = proportional_quotas(
            group_sizes,
            random_count,
            guarantee_one=True,
        )
        exploration_positions.update(groups)
        exploration_quotas.update(local_quotas)

    if (
        len(risk)
        + len(information)
        + len(discovery)
        + sum(exploration_quotas.values())
        != count
    ):
        raise AssertionError("gate plan does not match requested count")
    return GatePlan(
        count=count,
        risk_positions=np.asarray(risk, dtype=int),
        information_positions=np.asarray(information, dtype=int),
        discovery_positions=np.asarray(discovery, dtype=int),
        exploration_positions=exploration_positions,
        exploration_quotas=exploration_quotas,
    )


def select_from_plan(
    frame: pd.DataFrame,
    plan: GatePlan,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Select tasks and return audit flags, probabilities, and lane names."""

    selected = np.zeros(len(frame), dtype=bool)
    inclusion = np.zeros(len(frame), dtype=float)
    lanes = np.full(len(frame), "not_selected", dtype=object)
    for positions, name in (
        (plan.risk_positions, "risk"),
        (plan.information_positions, "heavy_information"),
        (plan.discovery_positions, "discovery"),
    ):
        selected[positions] = True
        inclusion[positions] = 1.0
        lanes[positions] = name
    for key, positions in plan.exploration_positions.items():
        quota = int(plan.exploration_quotas[key])
        if not len(positions):
            continue
        inclusion[positions] = quota / len(positions)
        if quota:
            chosen = rng.choice(positions, quota, replace=False)
            selected[chosen] = True
            lanes[chosen] = "process_exploration"
    if int(selected.sum()) != plan.count:
        raise AssertionError("gate selector returned an incorrect count")
    return selected, inclusion, lanes
