"""Leakage-free LightGBM ensembles for C-hat and peak RSS."""

from __future__ import annotations

import os
import time
import warnings
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from common import load_config, stable_hash, write_json
from history import HISTORY_NUMERIC_COLUMNS


def configured_model_seeds() -> list[int]:
    config = load_config()
    baseline_seed = int(config["seed"])
    replicate_seed = int(os.environ.get("CAMP_REPLICATE_SEED", baseline_seed))
    offset = 1000 * (replicate_seed - baseline_seed)
    return [int(value) + offset for value in config["models"]["model_seeds"]]


CATEGORICAL_A = (
    "workflow",
    "process",
    "version",
    "system_config_id",
)
NUMERIC_A = (
    "log1p_static_input_bytes",
    "log1p_staged_original_input_bytes",
    "log1p_staged_intermediate_input_bytes",
    "log1p_staged_external_input_bytes",
    "worker_count",
    "worker_vcpu",
    "worker_memory_gib",
    "requested_threads",
    "requested_java_heap_mb",
    "coverage",
    "interval_length_bp",
    "has_str_region_numeric",
)
HISTORY_CATEGORICAL = ("history_scope",)


def _history_model_column(source: str) -> str:
    if source.endswith(("_confidence", "_log_spread")) or "distance_" in source:
        return source
    return f"log1p_{source}"


PEAK_HISTORY_COLUMNS = tuple(
    column
    for column in HISTORY_NUMERIC_COLUMNS
    if "consumed" not in column
)
CONSUMED_HISTORY_COLUMNS = tuple(
    column
    for column in HISTORY_NUMERIC_COLUMNS
    if "consumed" in column
)
NUMERIC_P = tuple(
    _history_model_column(column) for column in PEAK_HISTORY_COLUMNS
)
NUMERIC_C = tuple(
    _history_model_column(column) for column in CONSUMED_HISTORY_COLUMNS
) + ("log1p_c_hat_bytes",)
FORBIDDEN_CURRENT_COLUMNS = {
    "peak_memory_bytes",
    "ebpf_total_consumed_bytes",
    "ebpf_read_return_bytes",
    "ebpf_mmap_page_fault_bytes",
    "runtime_seconds",
    "observed_oom_flag",
}


def materialize_model_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for source in (
        "static_input_bytes",
        "staged_original_input_bytes",
        "staged_intermediate_input_bytes",
        "staged_external_input_bytes",
    ):
        values = pd.to_numeric(result[source], errors="coerce").clip(lower=0.0)
        result[f"log1p_{source}"] = np.log1p(values)
    result["has_str_region_numeric"] = (
        result["has_str_region"].eq(True).astype(float)
    )
    for source in HISTORY_NUMERIC_COLUMNS:
        values = pd.to_numeric(result[source], errors="coerce")
        model_column = _history_model_column(source)
        if model_column.startswith("log1p_"):
            result[model_column] = np.log1p(values.clip(lower=0.0))
        else:
            result[model_column] = values
    if "c_hat_bytes" in result:
        result["log1p_c_hat_bytes"] = np.log1p(
            pd.to_numeric(result["c_hat_bytes"], errors="coerce").clip(
                lower=0.0
            )
        )
    for column in CATEGORICAL_A:
        result[column] = result[column].fillna("unknown").astype(str)
    result["history_scope"] = (
        result["history_scope"].fillna("cold_start").astype(str)
    )
    return result


def feature_columns(view: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    numeric = list(NUMERIC_A)
    categorical = list(CATEGORICAL_A)
    if view in {"A+P", "A+P+C"}:
        numeric.extend(NUMERIC_P)
        categorical.extend(HISTORY_CATEGORICAL)
    if view == "A+P+C":
        numeric.extend(NUMERIC_C)
    if view not in {"A", "A+P", "A+P+C"}:
        raise ValueError(f"unknown feature view: {view}")
    selected = set(numeric) | set(categorical)
    if selected & FORBIDDEN_CURRENT_COLUMNS:
        raise ValueError("feature contract includes a current-row label")
    return tuple(numeric), tuple(categorical)


def preprocessor(view: str) -> ColumnTransformer:
    numeric, categorical = feature_columns(view)
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


def lgbm(alpha: float, seed: int, n_jobs: int) -> LGBMRegressor:
    model_config = load_config()["models"]
    return LGBMRegressor(
        objective="quantile",
        alpha=alpha,
        n_estimators=int(model_config["n_estimators"]),
        learning_rate=float(model_config["learning_rate"]),
        num_leaves=31,
        min_child_samples=20,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        random_state=seed,
        verbosity=-1,
        n_jobs=n_jobs,
    )


def grouped_fold_assignments(
    frame: pd.DataFrame,
    folds: int,
) -> np.ndarray:
    assignments: dict[tuple[str, str], int] = {}
    groups = frame[["workflow", "split_group_id"]].drop_duplicates()
    for workflow, workflow_groups in groups.groupby("workflow", sort=True):
        ordered = workflow_groups.assign(
            group_hash=workflow_groups["split_group_id"]
            .astype(str)
            .map(stable_hash)
        ).sort_values(["group_hash", "split_group_id"])
        for position, row in enumerate(ordered.itertuples(index=False)):
            assignments[(str(workflow), str(row.split_group_id))] = (
                position % folds
            )
    return np.asarray(
        [
            assignments[(str(workflow), str(group))]
            for workflow, group in zip(
                frame["workflow"], frame["split_group_id"]
            )
        ],
        dtype=int,
    )


def _ensemble_fit_predict(
    x_fit,
    target_log: np.ndarray,
    x_predict,
    *,
    quantiles: list[float],
    model_seeds: list[int],
    n_jobs: int,
) -> tuple[dict[float, np.ndarray], dict[float, list[LGBMRegressor]]]:
    predictions: dict[float, np.ndarray] = {}
    fitted: dict[float, list[LGBMRegressor]] = {}
    for quantile in quantiles:
        members = []
        member_predictions = []
        for model_seed in model_seeds:
            model = lgbm(quantile, model_seed, n_jobs)
            model.fit(x_fit, target_log)
            members.append(model)
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="X does not have valid feature names",
                    category=UserWarning,
                )
                member_predictions.append(
                    np.expm1(model.predict(x_predict))
                )
        predictions[quantile] = np.maximum(
            np.median(np.column_stack(member_predictions), axis=1),
            0.0,
        )
        fitted[quantile] = members
    ordered = sorted(quantiles)
    monotonic = np.maximum.accumulate(
        np.column_stack([predictions[value] for value in ordered]), axis=1
    )
    for position, quantile in enumerate(ordered):
        predictions[quantile] = monotonic[:, position]
    return predictions, fitted


def crossfit_predictions(
    frame: pd.DataFrame,
    target: np.ndarray,
    *,
    view: str,
    quantiles: list[float],
    n_jobs: int,
) -> tuple[dict[float, np.ndarray], dict[str, Any]]:
    config = load_config()["models"]
    folds = int(config["folds"])
    model_seeds = configured_model_seeds()
    assignments = grouped_fold_assignments(frame, folds)
    target_log = np.log1p(np.maximum(np.asarray(target, dtype=float), 0.0))
    predictions = {
        quantile: np.full(len(frame), np.nan, dtype=float)
        for quantile in quantiles
    }
    started = time.time()
    for fold in range(folds):
        fit_mask = assignments != fold
        validation_mask = assignments == fold
        if not validation_mask.any():
            continue
        if not fit_mask.any():
            raise ValueError(
                f"{view}: grouped cross-fit has no training rows"
            )
        transformer = preprocessor(view)
        x_fit = transformer.fit_transform(frame.loc[fit_mask])
        x_validation = transformer.transform(frame.loc[validation_mask])
        fold_predictions, _ = _ensemble_fit_predict(
            x_fit,
            target_log[fit_mask],
            x_validation,
            quantiles=quantiles,
            model_seeds=model_seeds,
            n_jobs=n_jobs,
        )
        for quantile in quantiles:
            predictions[quantile][validation_mask] = fold_predictions[quantile]
    if any(not np.isfinite(values).all() for values in predictions.values()):
        raise ValueError(f"{view}: incomplete cross-fitted predictions")
    return predictions, {
        "view": view,
        "folds": folds,
        "model_seeds": model_seeds,
        "quantiles": quantiles,
        "rows": int(len(frame)),
        "elapsed_seconds": time.time() - started,
    }


def fit_final_predictions(
    initial: pd.DataFrame,
    test: pd.DataFrame,
    target: np.ndarray,
    *,
    view: str,
    quantiles: list[float],
    n_jobs: int,
    model_root: Path,
) -> tuple[dict[float, np.ndarray], dict[str, Any]]:
    config = load_config()["models"]
    model_seeds = configured_model_seeds()
    target_log = np.log1p(np.maximum(np.asarray(target, dtype=float), 0.0))
    started = time.time()
    transformer = preprocessor(view)
    x_initial = transformer.fit_transform(initial)
    x_test = transformer.transform(test)
    predictions, models = _ensemble_fit_predict(
        x_initial,
        target_log,
        x_test,
        quantiles=quantiles,
        model_seeds=model_seeds,
        n_jobs=n_jobs,
    )
    model_root.mkdir(parents=True, exist_ok=True)
    joblib.dump(transformer, model_root / "preprocessor.joblib")
    for quantile, members in models.items():
        label = int(round(quantile * 1000))
        for member_index, model in enumerate(members):
            joblib.dump(
                model,
                model_root
                / f"lightgbm_q{label:03d}_member_{member_index}.joblib",
            )
    metadata = {
        "view": view,
        "fit_rows": int(len(initial)),
        "test_rows": int(len(test)),
        "quantiles": quantiles,
        "model_seeds": model_seeds,
        "ensemble_reducer": "median",
        "quantile_crossing_policy": "row-wise cumulative maximum",
        "numeric_features": list(feature_columns(view)[0]),
        "categorical_features": list(feature_columns(view)[1]),
        "target_transform": "log1p",
        "elapsed_seconds": time.time() - started,
    }
    write_json(model_root / "metadata.json", metadata)
    return predictions, metadata


def crossfit_per_process_predictions(
    frame: pd.DataFrame,
    target: np.ndarray,
    *,
    view: str,
    quantiles: list[float],
    n_jobs: int,
    fallback_predictions: dict[float, np.ndarray] | None = None,
) -> tuple[dict[float, np.ndarray], dict[str, Any]]:
    predictions = {
        quantile: np.full(len(frame), np.nan, dtype=float)
        for quantile in quantiles
    }
    process_metadata = {}
    for keys, group in frame.groupby(["workflow", "process"], sort=True):
        positions = group.index.to_numpy(dtype=int)
        local_frame = group.reset_index(drop=True)
        local_target = np.asarray(target, dtype=float)[positions]
        unique_groups = local_frame["split_group_id"].nunique()
        if unique_groups < 2:
            if fallback_predictions is None:
                raise ValueError(
                    f"{keys}: one split group and no OOF fallback"
                )
            local_predictions = {
                quantile: np.asarray(
                    fallback_predictions[quantile],
                    dtype=float,
                )[positions]
                for quantile in quantiles
            }
            metadata = {
                "view": view,
                "rows": int(len(local_frame)),
                "unique_split_groups": int(unique_groups),
                "oof_scope": "global_fallback_for_single_parent_group",
            }
        else:
            local_predictions, metadata = crossfit_predictions(
                local_frame,
                local_target,
                view=view,
                quantiles=quantiles,
                n_jobs=n_jobs,
            )
            metadata["unique_split_groups"] = int(unique_groups)
            metadata["oof_scope"] = "per_process"
        for quantile in quantiles:
            predictions[quantile][positions] = local_predictions[quantile]
        process_metadata[f"{keys[0]}::{keys[1]}"] = metadata
    if any(not np.isfinite(values).all() for values in predictions.values()):
        raise ValueError("per-process OOF predictions are incomplete")
    return predictions, {
        "scope": "per_process",
        "view": view,
        "quantiles": quantiles,
        "processes": process_metadata,
    }


def fit_final_per_process_predictions(
    initial: pd.DataFrame,
    test: pd.DataFrame,
    target: np.ndarray,
    *,
    view: str,
    quantiles: list[float],
    n_jobs: int,
    model_root: Path,
    fallback_predictions: dict[float, np.ndarray] | None = None,
) -> tuple[dict[float, np.ndarray], dict[str, Any]]:
    predictions = {
        quantile: np.full(len(test), np.nan, dtype=float)
        for quantile in quantiles
    }
    process_metadata = {}
    for keys, initial_group in initial.groupby(
        ["workflow", "process"], sort=True
    ):
        initial_positions = initial_group.index.to_numpy(dtype=int)
        test_mask = (
            test["workflow"].astype(str).eq(str(keys[0]))
            & test["process"].astype(str).eq(str(keys[1]))
        )
        test_positions = np.flatnonzero(test_mask.to_numpy())
        if not len(test_positions):
            continue
        local_initial = initial_group.reset_index(drop=True)
        local_test = test.iloc[test_positions].reset_index(drop=True)
        local_target = np.asarray(target, dtype=float)[initial_positions]
        process_label = stable_hash(f"{keys[0]}::{keys[1]}")[:16]
        local_predictions, metadata = fit_final_predictions(
            local_initial,
            local_test,
            local_target,
            view=view,
            quantiles=quantiles,
            n_jobs=n_jobs,
            model_root=model_root / process_label,
        )
        for quantile in quantiles:
            predictions[quantile][test_positions] = local_predictions[
                quantile
            ]
        process_metadata[f"{keys[0]}::{keys[1]}"] = {
            **metadata,
            "model_directory": process_label,
        }
    fallback_rows = np.zeros(len(test), dtype=bool)
    for quantile, values in predictions.items():
        missing = ~np.isfinite(values)
        fallback_rows |= missing
        if missing.any():
            if fallback_predictions is None:
                raise ValueError(
                    "per-process final predictions are incomplete"
                )
            fallback = np.asarray(
                fallback_predictions[quantile],
                dtype=float,
            )
            if fallback.shape != values.shape:
                raise ValueError("global tail fallback shape mismatch")
            values[missing] = fallback[missing]
    if any(not np.isfinite(values).all() for values in predictions.values()):
        raise ValueError("global tail fallback contains non-finite values")
    fallback_processes = (
        test.loc[fallback_rows, ["workflow", "process"]]
        .drop_duplicates()
        .astype(str)
        .agg("::".join, axis=1)
        .sort_values()
        .tolist()
    )
    metadata = {
        "scope": "per_process",
        "view": view,
        "quantiles": quantiles,
        "processes": process_metadata,
        "unseen_process_fallback": "global_tail_ensemble",
        "unseen_process_fallback_rows": int(fallback_rows.sum()),
        "unseen_processes": fallback_processes,
    }
    write_json(model_root / "metadata.json", metadata)
    return predictions, metadata
