"""Workflow-cold A+P+C model shared by training and live inference."""

from __future__ import annotations

import re
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


COLD_QUANTILES = (0.5, 0.9, 0.95, 0.99, 0.995)
COLD_SEEDS = (20260728, 20260729, 20260730)
COLD_CATEGORICAL = (
    "process",
    "process_family",
    "input_class",
    "system_config_id",
)
COLD_NUMERIC_A = (
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
COLD_NUMERIC_P = (
    "log1p_history_i5_support",
    "log1p_history_i5_peak_p50_bytes",
    "log1p_history_i5_peak_p95_bytes",
    "log1p_history_i5_peak_p99_bytes",
    "history_i5_peak_log_spread",
    "history_i5_confidence",
)
COLD_NUMERIC_C = (
    "log1p_history_i5_consumed_p50_bytes",
    "log1p_history_i5_consumed_p95_bytes",
    "log1p_history_i5_consumed_p99_bytes",
    "log1p_history_i5_consumed_to_input_p50",
    "log1p_c_hat_bytes",
)


def process_family(process: str) -> str:
    value = re.sub(r"[^A-Z0-9]+", "_", str(process).upper()).strip("_")
    if any(token in value for token in ("DOWNLOAD", "FETCH")):
        return "download"
    if "BUILD_INDEX" in value or value.endswith("_INDEX"):
        return "index_build"
    if any(token in value for token in ("ALIGN", "MAP_WINDOW", "BWAMEM")):
        return "alignment"
    if any(token in value for token in ("SPLIT", "SCATTER", "INTERVAL")):
        return "partition"
    if "INDEX_BAM" in value or "INDEX_CRAM" in value:
        return "alignment_index"
    if any(
        token in value
        for token in ("FLAGSTAT", "STATS", "FASTQC", "MOSDEPTH", "MULTIQC")
    ):
        return "quality_control"
    if any(token in value for token in ("KRAKEN", "KAIJU", "TAXON")):
        return "classification"
    if any(token in value for token in ("UNTAR", "UNPACK", "EXTRACT")):
        return "archive_extract"
    if any(token in value for token in ("HAPLOTYPE", "VARIANT", "GATK")):
        return "variant_processing"
    if any(token in value for token in ("GENERATE", "CREATE")):
        return "generation"
    return "other"


def _input_class(row: pd.Series) -> str:
    components = {
        "original": row.get("staged_original_input_bytes"),
        "intermediate": row.get("staged_intermediate_input_bytes"),
        "external": row.get("staged_external_input_bytes"),
    }
    present = []
    for name, value in components.items():
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.notna(numeric) and float(numeric) > 0:
            present.append(name)
    if len(present) == 1:
        return present[0]
    if len(present) > 1:
        return "mixed"
    return "unknown"


def materialize_cold_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for source in (
        "static_input_bytes",
        "staged_original_input_bytes",
        "staged_intermediate_input_bytes",
        "staged_external_input_bytes",
        "history_i5_support",
        "history_i5_peak_p50_bytes",
        "history_i5_peak_p95_bytes",
        "history_i5_peak_p99_bytes",
        "history_i5_consumed_p50_bytes",
        "history_i5_consumed_p95_bytes",
        "history_i5_consumed_p99_bytes",
        "history_i5_consumed_to_input_p50",
    ):
        values = pd.to_numeric(result[source], errors="coerce")
        result[f"log1p_{source}"] = np.log1p(values.clip(lower=0.0))
    if "c_hat_bytes" in result:
        result["log1p_c_hat_bytes"] = np.log1p(
            pd.to_numeric(result["c_hat_bytes"], errors="coerce").clip(
                lower=0.0
            )
        )
    result["has_str_region_numeric"] = (
        result["has_str_region"].eq(True).astype(float)
    )
    result["process"] = result["process"].fillna("unknown").astype(str)
    result["process_family"] = result["process"].map(process_family)
    result["input_class"] = result.apply(_input_class, axis=1)
    result["system_config_id"] = (
        result["system_config_id"].fillna("unknown").astype(str)
    )
    return result


def feature_columns(view: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    numeric = list(COLD_NUMERIC_A)
    if view == "A+P+C":
        numeric.extend(COLD_NUMERIC_P)
        numeric.extend(COLD_NUMERIC_C)
    elif view != "A":
        raise ValueError(f"unknown cold-start view: {view}")
    return tuple(numeric), COLD_CATEGORICAL


def preprocessor(view: str) -> ColumnTransformer:
    numeric, categorical = feature_columns(view)
    return ColumnTransformer(
        [
            (
                "numeric",
                Pipeline(
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
                ),
                list(numeric),
            ),
            (
                "categorical",
                Pipeline(
                    [
                        (
                            "imputer",
                            SimpleImputer(strategy="most_frequent"),
                        ),
                        (
                            "one_hot",
                            OneHotEncoder(
                                handle_unknown="ignore",
                                sparse_output=True,
                            ),
                        ),
                    ]
                ),
                list(categorical),
            ),
        ],
        sparse_threshold=1.0,
    )


def lgbm(quantile: float, seed: int, n_jobs: int) -> LGBMRegressor:
    return LGBMRegressor(
        objective="quantile",
        alpha=quantile,
        n_estimators=350,
        learning_rate=0.03,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        random_state=seed,
        verbosity=-1,
        n_jobs=n_jobs,
    )


def fit_predict(
    fit_frame: pd.DataFrame,
    predict_frame: pd.DataFrame,
    target: np.ndarray,
    *,
    view: str,
    quantiles: tuple[float, ...],
    n_jobs: int,
    model_root: Path | None = None,
) -> dict[float, np.ndarray]:
    transformer = preprocessor(view)
    fit_matrix = transformer.fit_transform(fit_frame)
    predict_matrix = transformer.transform(predict_frame)
    result = {}
    fitted = {}
    for quantile in quantiles:
        members = []
        predictions = []
        for seed in COLD_SEEDS:
            model = lgbm(quantile, seed, n_jobs)
            model.fit(fit_matrix, np.log1p(np.maximum(target, 0.0)))
            members.append(model)
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="X does not have valid feature names",
                    category=UserWarning,
                )
                predictions.append(
                    np.expm1(model.predict(predict_matrix))
                )
        result[quantile] = np.maximum(
            np.median(np.column_stack(predictions), axis=1),
            0.0,
        )
        fitted[quantile] = members
    ordered = np.maximum.accumulate(
        np.column_stack([result[value] for value in quantiles]),
        axis=1,
    )
    for position, quantile in enumerate(quantiles):
        result[quantile] = ordered[:, position]
    if model_root is not None:
        model_root.mkdir(parents=True, exist_ok=True)
        joblib.dump(transformer, model_root / "preprocessor.joblib")
        for quantile, members in fitted.items():
            label = f"q{int(round(quantile * 1000)):03d}"
            for position, model in enumerate(members):
                joblib.dump(
                    model,
                    model_root / f"lightgbm_{label}_member_{position}.joblib",
                )
    return result


class ColdStartBundle:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.c_preprocessor = joblib.load(
            path / "consumed_c_hat" / "preprocessor.joblib"
        )
        self.rss_preprocessor = joblib.load(
            path / "a_plus_p_plus_c" / "preprocessor.joblib"
        )
        self.c_models = self._load_models(
            path / "consumed_c_hat",
            (0.5,),
        )
        self.rss_models = self._load_models(
            path / "a_plus_p_plus_c",
            COLD_QUANTILES,
        )

    @staticmethod
    def _load_models(
        path: Path,
        quantiles: tuple[float, ...],
    ) -> dict[float, list[Any]]:
        result = {}
        for quantile in quantiles:
            label = f"q{int(round(quantile * 1000)):03d}"
            members = [
                joblib.load(item)
                for item in sorted(
                    path.glob(f"lightgbm_{label}_member_*.joblib")
                )
            ]
            if not members:
                raise ValueError(f"{path}: missing {label} models")
            for model in members:
                model.set_params(n_jobs=1)
            result[quantile] = members
        return result

    @staticmethod
    def _predict_members(models: list[Any], matrix) -> np.ndarray:
        predictions = []
        for model in models:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="X does not have valid feature names",
                    category=UserWarning,
                )
                predictions.append(np.expm1(model.predict(matrix)))
        return np.maximum(
            np.median(np.column_stack(predictions), axis=1),
            0.0,
        )

    def predict(self, frame: pd.DataFrame) -> dict[str, Any]:
        features = materialize_cold_features(frame)
        c_matrix = self.c_preprocessor.transform(features)
        c_hat = self._predict_members(self.c_models[0.5], c_matrix)
        features["c_hat_bytes"] = c_hat
        features = materialize_cold_features(features)
        rss_matrix = self.rss_preprocessor.transform(features)
        predictions = {
            quantile: self._predict_members(models, rss_matrix)
            for quantile, models in self.rss_models.items()
        }
        ordered = np.maximum.accumulate(
            np.column_stack(
                [predictions[value] for value in COLD_QUANTILES]
            ),
            axis=1,
        )
        predictions = {
            quantile: ordered[:, position]
            for position, quantile in enumerate(COLD_QUANTILES)
        }
        return {"c_hat_bytes": c_hat, "rss": predictions}
