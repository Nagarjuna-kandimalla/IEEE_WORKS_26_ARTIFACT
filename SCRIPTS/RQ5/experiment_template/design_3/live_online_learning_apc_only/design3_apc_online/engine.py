"""Versioned Design 3 APC-only engine with incremental causal history."""

from __future__ import annotations

import json
import math
import re
import sys
import warnings
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


ONLINE_ROOT = Path(__file__).resolve().parents[1]
DESIGN3_ROOT = ONLINE_ROOT.parent
SNAPSHOT_SUPPORT = ONLINE_ROOT / "design3_support"
MAIN_SCRIPTS = (
    SNAPSHOT_SUPPORT
    if SNAPSHOT_SUPPORT.is_dir()
    else DESIGN3_ROOT / "scripts"
)
SNAPSHOT_LIVE = ONLINE_ROOT / "design3_live"
LIVE_DEPLOYMENT = (
    ONLINE_ROOT
    if SNAPSHOT_LIVE.is_dir()
    else DESIGN3_ROOT / "live_deployment"
)
sys.path.insert(0, str(MAIN_SCRIPTS))
sys.path.insert(0, str(LIVE_DEPLOYMENT))

import common
from calibration import ResidualIndex
from history import HistoryIndex
from modeling import materialize_model_features
from design3_live.cold_start import ColdStartBundle
from .policy import APCResidualCalibration, allocate_apc_only


QUANTILES = (0.5, 0.9, 0.95, 0.99, 0.995)
TAIL_QUANTILES = QUANTILES[1:]
VARIANT_PATHS = {
    "A": "a",
    "A+P": "a_plus_p",
    "A+P+C": "a_plus_p_plus_c",
}
REQUIRED_TASK_DEFAULTS = {
    "staged_original_input_bytes": np.nan,
    "staged_intermediate_input_bytes": np.nan,
    "staged_external_input_bytes": np.nan,
    "requested_threads": np.nan,
    "requested_java_heap_mb": np.nan,
    "coverage": np.nan,
    "interval_length_bp": np.nan,
    "has_str_region": False,
    "worker_count": 13,
    "worker_vcpu": 32,
    "worker_memory_gib": 244.140625,
    "split_group_id": None,
}


def quantile_label(value: float) -> str:
    return f"q{int(round(value * 1000)):03d}"


def json_value(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if pd.isna(value):
        return None
    return value


class ModelGroup:
    """One preprocessor and median LightGBM quantile ensemble."""

    def __init__(self, path: Path, quantiles: tuple[float, ...]) -> None:
        self.path = path
        self.preprocessor = joblib.load(path / "preprocessor.joblib")
        self.models: dict[float, list[Any]] = {}
        for quantile in quantiles:
            label = quantile_label(quantile)
            members = []
            for model_path in sorted(
                path.glob(f"lightgbm_{label}_member_*.joblib")
            ):
                model = joblib.load(model_path)
                model.set_params(n_jobs=1)
                members.append(model)
            if not members:
                raise ValueError(f"{path}: no models for {label}")
            self.models[quantile] = members

    def predict(self, frame: pd.DataFrame) -> dict[float, np.ndarray]:
        matrix = self.preprocessor.transform(frame)
        result = {}
        for quantile, members in self.models.items():
            predictions = []
            for model in members:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message="X does not have valid feature names",
                        category=UserWarning,
                    )
                    predictions.append(np.expm1(model.predict(matrix)))
            result[quantile] = np.maximum(
                np.median(np.column_stack(predictions), axis=1),
                0.0,
            )
        return result


class OnlineDesign3Engine:
    """Apply one immutable model version plus incrementally completed rows."""

    def __init__(
        self,
        model_root: Path,
        history_features: Path,
        cold_start_root: Path,
        cold_start_policy: Path,
        policy_root: Path,
        config_path: Path,
        apc_policy_path: Path | None = None,
    ) -> None:
        common.CONFIG_PATH = config_path.resolve()
        self.model_root = model_root.resolve()
        manifest_path = self.model_root / "online_manifest.json"
        with manifest_path.open(encoding="utf-8") as handle:
            self.manifest = json.load(handle)
        self.model_version = str(self.manifest["model_version"])
        self.known_workflows = {
            str(value).lower()
            for value in self.manifest["known_workflows"]
        }
        self.scaler = joblib.load(
            self.model_root / "signature_scaler.joblib"
        )
        calibration_path = self.model_root / "version_calibration.tsv"
        if not calibration_path.is_file():
            raise ValueError(
                f"model version lacks version calibration: {calibration_path}"
            )
        self.version_calibration = pd.read_csv(
            calibration_path,
            sep="\t",
            low_memory=False,
        )
        calibration_versions = set(
            self.version_calibration.get(
                "calibration_model_version",
                pd.Series(dtype=str),
            )
            .dropna()
            .astype(str)
        )
        if calibration_versions - {self.model_version}:
            raise ValueError(
                "calibration/model version mismatch: "
                f"model={self.model_version}, "
                f"calibration={sorted(calibration_versions)}"
            )
        self.history_frame = pd.read_csv(
            history_features,
            sep="\t",
            low_memory=False,
        ).sort_values("history_order", kind="mergesort").reset_index(
            drop=True
        )
        if self.history_frame["logical_task_id"].duplicated().any():
            raise ValueError("history feature manifest has duplicate task IDs")
        self.history_ids = set(
            self.history_frame["logical_task_id"].astype(str)
        )

        self.cold_start_bundle = ColdStartBundle(cold_start_root)
        with cold_start_policy.open(encoding="utf-8") as handle:
            self.cold_start_policy = json.load(handle)
        if self.cold_start_policy["feature_view"] != "A+P+C":
            raise ValueError("cold-start policy must select A+P+C")

        config = common.load_config()
        history_config = config["history"]
        self.history = HistoryIndex(
            nearest_k=int(history_config["nearest_k"]),
            scope_window=int(history_config["scope_window"]),
        )
        vectors = self.scaler.transform(self.history_frame)
        for position, (_, row) in enumerate(
            self.history_frame.iterrows()
        ):
            self.history.add(row, vectors[position])

        self.global_models = {
            variant: ModelGroup(
                self.model_root / path,
                QUANTILES,
            )
            for variant, path in VARIANT_PATHS.items()
        }
        self.aux_models = {
            "c_hat": ModelGroup(
                self.model_root / "consumed_c_hat",
                (0.5,),
            ),
            "memory_per_consumed": ModelGroup(
                self.model_root / "memory_per_consumed_ratio",
                (0.5,),
            ),
        }
        self.process_metadata: dict[str, dict[str, Any]] = {}
        self.process_models: dict[tuple[str, str], ModelGroup] = {}
        for variant in ("A", "A+P+C"):
            path = VARIANT_PATHS[variant]
            metadata_path = (
                self.model_root
                / f"{path}_process_tail"
                / "metadata.json"
            )
            if metadata_path.is_file():
                with metadata_path.open(encoding="utf-8") as handle:
                    self.process_metadata[variant] = json.load(handle)[
                        "processes"
                    ]
            else:
                self.process_metadata[variant] = {}
        self.workflow_metadata: dict[str, dict[str, Any]] = {}
        self.workflow_models: dict[str, ModelGroup] = {}
        workflow_metadata_path = (
            self.model_root
            / "a_plus_p_plus_c_workflow_tail"
            / "metadata.json"
        )
        if workflow_metadata_path.is_file():
            with workflow_metadata_path.open(encoding="utf-8") as handle:
                self.workflow_metadata = json.load(handle)["workflows"]

        with (
            policy_root / "a" / "process_tail_selected_policy.json"
        ).open(encoding="utf-8") as handle:
            self.a_policy = json.load(handle)
        resolved_apc_policy = (
            apc_policy_path
            if apc_policy_path is not None
            else (
                policy_root
                / "a_plus_p_plus_c"
                / "process_tail_selected_policy.json"
            )
        )
        with resolved_apc_policy.open(encoding="utf-8") as handle:
            self.apc_policy = json.load(handle)

        self.a_residuals = ResidualIndex(
            nearest_k=int(history_config["nearest_k"]),
            scope_window=int(history_config["scope_window"]),
        )
        calibration_vectors = (
            self.scaler.transform(self.version_calibration)
            if len(self.version_calibration)
            else np.empty((0, len(self.scaler.median)))
        )
        required_calibration_columns = {
            "calibration_model_version",
            "peak_memory_bytes",
            "base_a_mib",
            "base_a_plus_p_plus_c_mib",
        }
        missing_calibration_columns = (
            required_calibration_columns
            - set(self.version_calibration.columns)
        )
        if missing_calibration_columns:
            raise ValueError(
                "version calibration lacks columns: "
                f"{sorted(missing_calibration_columns)}"
            )
        for position, (_, row) in enumerate(
            self.version_calibration.iterrows()
        ):
            actual_mib = float(row["peak_memory_bytes"]) / 2**20
            base_mib = max(float(row["base_a_mib"]), 1e-6)
            self.a_residuals.add(
                row,
                calibration_vectors[position],
                math.log(max(actual_mib, 1e-6) / base_mib),
            )
        self.apc_residuals = APCResidualCalibration(
            scope_window=int(history_config["scope_window"]),
            process_min_support=int(history_config["process_min_support"]),
            workflow_min_support=int(history_config["workflow_min_support"]),
        )
        for _, row in self.version_calibration.iterrows():
            self.apc_residuals.add(
                row.to_dict(),
                base_mib=float(row["base_a_plus_p_plus_c_mib"]),
                actual_mib=float(row["peak_memory_bytes"]) / 2**20,
            )

    @staticmethod
    def _task_row(task: dict[str, Any]) -> dict[str, Any]:
        row = dict(REQUIRED_TASK_DEFAULTS)
        row.update(task)
        row["logical_task_id"] = str(task["task_id"])
        row["static_input_bytes"] = max(
            int(task["static_input_bytes"]),
            1,
        )
        row["input_identity"] = str(task.get("input_identity", ""))
        row["task_instance"] = str(task.get("task_instance", ""))
        split_group = task.get("split_group_id")
        if split_group is None or not str(split_group).strip():
            match = re.search(
                r"(ERR\d+)",
                row["task_instance"],
                flags=re.IGNORECASE,
            )
            split_group = (
                match.group(1).upper()
                if match
                else f"{row['process']}::{row['task_instance']}"
            )
        row["split_group_id"] = str(split_group)
        return row

    def ingest_history_row(self, raw: dict[str, Any]) -> bool:
        """Make one successful prior outcome visible to later decisions."""
        task_id = str(raw["logical_task_id"])
        if task_id in self.history_ids:
            return False
        row = dict(raw)
        frame = pd.DataFrame([row])
        vector = self.scaler.transform(frame)[0]
        self.history.add(row, vector)
        if str(row.get("prediction_model_version", "")) == self.model_version:
            actual_mib = float(row["peak_memory_bytes"]) / 2**20
            base_mib = max(float(row["base_a_mib"]), 1e-6)
            self.a_residuals.add(
                row,
                vector,
                math.log(max(actual_mib, 1e-6) / base_mib),
            )
            self.apc_residuals.add(
                row,
                base_mib=max(
                    float(row["base_a_plus_p_plus_c_mib"]),
                    1e-6,
                ),
                actual_mib=actual_mib,
            )
        self.history_ids.add(task_id)
        return True

    def _process_group(
        self,
        variant: str,
        workflow: str,
        process: str,
    ) -> ModelGroup | None:
        details = self.process_metadata.get(variant, {}).get(
            f"{workflow}::{process}"
        )
        if details is None:
            return None
        cache_key = (variant, f"{workflow}::{process}")
        if cache_key not in self.process_models:
            path = VARIANT_PATHS[variant]
            model_path = (
                self.model_root
                / f"{path}_process_tail"
                / details["model_directory"]
            )
            self.process_models[cache_key] = ModelGroup(
                model_path,
                TAIL_QUANTILES,
            )
        return self.process_models[cache_key]

    def _variant_predictions(
        self,
        variant: str,
        features: pd.DataFrame,
        workflow: str,
        process: str,
    ) -> dict[float, float]:
        values = {
            quantile: float(result[0])
            for quantile, result in self.global_models[
                variant
            ].predict(features).items()
        }
        if variant in {"A", "A+P+C"}:
            process_group = self._process_group(
                variant,
                workflow,
                process,
            )
            if process_group is not None:
                for quantile, result in process_group.predict(
                    features
                ).items():
                    values[quantile] = float(result[0])
        ordered = np.maximum.accumulate(
            [values[quantile] for quantile in QUANTILES]
        )
        return {
            quantile: float(ordered[position])
            for position, quantile in enumerate(QUANTILES)
        }

    def _workflow_group(self, workflow: str) -> ModelGroup | None:
        details = self.workflow_metadata.get(str(workflow).lower())
        if details is None:
            return None
        key = str(workflow).lower()
        if key not in self.workflow_models:
            model_path = (
                self.model_root
                / "a_plus_p_plus_c_workflow_tail"
                / details["model_directory"]
            )
            self.workflow_models[key] = ModelGroup(
                model_path,
                TAIL_QUANTILES,
            )
        return self.workflow_models[key]

    def _apc_predictions(
        self,
        features: pd.DataFrame,
        workflow: str,
        process: str,
    ) -> tuple[dict[float, float], str]:
        values = {
            quantile: float(result[0])
            for quantile, result in self.global_models["A+P+C"]
            .predict(features)
            .items()
        }
        model_scope = "global"
        workflow_group = self._workflow_group(workflow)
        if workflow_group is not None:
            for quantile, result in workflow_group.predict(features).items():
                values[quantile] = float(result[0])
            model_scope = "workflow"
        process_group = self._process_group(
            "A+P+C",
            workflow,
            process,
        )
        if process_group is not None:
            for quantile, result in process_group.predict(features).items():
                values[quantile] = float(result[0])
            model_scope = "process"
        ordered = np.maximum.accumulate(
            [values[quantile] for quantile in QUANTILES]
        )
        return (
            {
                quantile: float(ordered[position])
                for position, quantile in enumerate(QUANTILES)
            },
            model_scope,
        )

    def _a_allocation(
        self,
        row: dict[str, Any],
        vector: np.ndarray,
        values: dict[float, float],
    ) -> dict[str, Any]:
        base_policy = str(self.a_policy["base_policy"])
        if base_policy == "point_q50":
            base = values[0.5]
        else:
            quantile = {
                "model_q90": 0.9,
                "model_q95": 0.95,
                "model_q99": 0.99,
                "model_q995": 0.995,
            }[base_policy]
            base = max(values[0.5], values[quantile])
        residual_probability = float(
            self.a_policy["residual_quantile"]
        )
        corrections, scope = self.a_residuals.corrections(
            row,
            vector,
            [residual_probability],
            mode=str(self.a_policy["mode"]),
        )
        correction = float(corrections[residual_probability])
        request = int(math.ceil(max(base * math.exp(correction), 1.0)))
        return {
            "base_policy": base_policy,
            "base_mib": float(base),
            "residual_quantile": residual_probability,
            "calibration_log_correction": correction,
            "calibration_factor": float(math.exp(correction)),
            "calibration_scope": scope["calibration_scope"],
            "calibration_support": scope["calibration_support"],
            "calibration_confidence": scope["calibration_confidence"],
            "request_mb": request,
        }

    def _cold_allocation(
        self,
        values: dict[float, float],
        row: dict[str, Any],
    ) -> dict[str, Any]:
        quantile = float(self.cold_start_policy["model_quantile"])
        base = float(values[quantile])
        multiplier = float(self.cold_start_policy["multiplier"])
        global_peak_p95_mib = (
            float(row["history_i5_peak_p95_bytes"]) / 2**20
        )
        ood_floor = math.sqrt(
            max(values[0.99], 1.0)
            * max(global_peak_p95_mib, 1.0)
        )
        request = int(math.ceil(max(base * multiplier, ood_floor, 1.0)))
        return {
            "base_policy": (
                f"workflow_cold_model_{quantile_label(quantile)}"
            ),
            "base_mib": base,
            "calibration_factor": multiplier,
            "calibration_scope": "workflow_leave_one_out_global",
            "calibration_support": int(len(self.history_ids)),
            "calibration_confidence": float(
                self.cold_start_policy["coverage"]
            ),
            "global_peak_p95_mib": global_peak_p95_mib,
            "ood_safety_floor_mib": ood_floor,
            "request_mb": request,
        }

    def predict(self, task: dict[str, Any]) -> dict[str, Any]:
        row = self._task_row(task)
        frame = pd.DataFrame([row])
        vector = self.scaler.transform(frame)[0]
        history = self.history.snapshot(row, vector)
        row.update(history)
        features = materialize_model_features(pd.DataFrame([row]))
        workflow = str(row["workflow"])
        process = str(row["process"])

        a_values = self._variant_predictions(
            "A", features, workflow, process
        )
        ap_values = self._variant_predictions(
            "A+P", features, workflow, process
        )
        c_hat = float(
            self.aux_models["c_hat"].predict(features)[0.5][0]
        )
        ratio_hat = float(
            self.aux_models["memory_per_consumed"].predict(
                features
            )[0.5][0]
        )
        row["c_hat_bytes"] = c_hat
        features = materialize_model_features(pd.DataFrame([row]))
        raw_apc, apc_model_scope = self._apc_predictions(
            features,
            workflow,
            process,
        )
        consumed_rss_mib = c_hat * ratio_hat / 1_000_000.0 / 2**20
        ordered = np.maximum.accumulate(
            [raw_apc[value] for value in QUANTILES]
        )
        raw_apc = {
            quantile: float(ordered[position])
            for position, quantile in enumerate(QUANTILES)
        }

        workflow_cold = workflow.lower() not in self.known_workflows
        if workflow_cold:
            cold = self.cold_start_bundle.predict(pd.DataFrame([row]))
            c_hat = float(cold["c_hat_bytes"][0])
            row["c_hat_bytes"] = c_hat
            apc_values = {
                quantile: float(cold["rss"][quantile][0])
                for quantile in QUANTILES
            }
            consumed_rss_mib = (
                c_hat * ratio_hat / 1_000_000.0 / 2**20
            )
            apc_model_scope = "workflow_cold_leave_one_out"
        else:
            apc_values = raw_apc

        a_allocation = self._a_allocation(row, vector, a_values)
        ap_allocation = {
            "base_policy": "shadow_model_q995",
            "base_mib": float(ap_values[0.995]),
            "request_mb": int(math.ceil(max(ap_values[0.995], 1.0))),
        }
        if workflow_cold:
            dynamic_allocation = self._cold_allocation(apc_values, row)
            selected_request = int(dynamic_allocation["request_mb"])
            selected_source = "workflow_cold_a_plus_p_plus_c"
        else:
            dynamic_allocation = allocate_apc_only(
                row=row,
                values=apc_values,
                policy=self.apc_policy,
                calibration=self.apc_residuals,
                model_scope=apc_model_scope,
            )
            selected_request = int(dynamic_allocation["request_mb"])
            selected_source = "a_plus_p_plus_c_only"
        applied_apc_allocation = {
            **dynamic_allocation,
            "apc_request_mb": int(dynamic_allocation["request_mb"]),
            "a_shadow_request_mb": int(a_allocation["request_mb"]),
            "selection_source": selected_source,
            "request_mb": selected_request,
            "uses_a_prediction": False,
        }

        predictions = {
            "A": (a_values, a_allocation),
            "A+P": (ap_values, ap_allocation),
            "A+P+C": (apc_values, applied_apc_allocation),
        }
        serial_history = {
            key: json_value(value)
            for key, value in history.items()
        }
        serial_features = {
            key: json_value(value)
            for key, value in row.items()
            if key not in {
                "peak_memory_bytes",
                "ebpf_total_consumed_bytes",
            }
        }
        prediction_records = {}
        for variant, (values, allocation) in predictions.items():
            prediction_records[variant] = {
                "point_q50_mb": values[0.5],
                "q90_mb": values[0.9],
                "q95_mb": values[0.95],
                "q99_mb": values[0.99],
                "q995_mb": values[0.995],
                "allocation": allocation,
            }
        identity = {
            key: str(row.get(key, ""))
            for key in (
                "workflow",
                "process",
                "version",
                "system_config_id",
                "task_instance",
                "input_identity",
            )
        }
        return {
            "task_id": str(task["task_id"]),
            "decision_time": str(task["decision_time"]),
            "workflow": workflow,
            "process": process,
            "version": str(row["version"]),
            "static_input_bytes": int(row["static_input_bytes"]),
            "static_q50_prediction_mb": a_values[0.5],
            "prior_peak_q50_prediction_mb": ap_values[0.5],
            "history_q50_prediction_mb": apc_values[0.5],
            "consumed_q50_prediction_mb": apc_values[0.5],
            "c_hat_bytes": c_hat,
            "consumed_derived_q50_mb": consumed_rss_mib,
            "stack_weights": None,
            "apc_point_contract": "direct_a_plus_p_plus_c_model",
            "variant_predictions": prediction_records,
            "allocation_variant": "A+P+C",
            "recommended_allocation_mb": selected_request,
            "allocation_granularity_mb": 1,
            "allocation_source": (
                "design3_apc_only_workflow_cold"
                if workflow_cold
                else "design3_apc_only_warm_calibrated"
            ),
            "selection_source": selected_source,
            "workflow_cold": workflow_cold,
            "workflow_cold_policy": (
                self.cold_start_policy if workflow_cold else None
            ),
            "raw_apc_counterfactual": (
                {
                    quantile_label(quantile): value
                    for quantile, value in raw_apc.items()
                }
                if workflow_cold
                else None
            ),
            "history_context": {
                "identity": identity,
                "selected_scope": history["history_scope"],
                "support_count": history["history_scope_support"],
                "confidence": history["history_scope_confidence"],
                "features": serial_history,
            },
            "design3_feature_row": serial_features,
            "model_version": self.model_version,
            "calibration_model_version": self.model_version,
            "calibration_rows_at_load": int(len(self.version_calibration)),
            "record_after_completion": {
                "static_prediction_mb": a_values[0.5],
                "history_prediction_mb": apc_values[0.5],
                "allocated_memory_mb": selected_request,
                "model_version": self.model_version,
            },
        }
