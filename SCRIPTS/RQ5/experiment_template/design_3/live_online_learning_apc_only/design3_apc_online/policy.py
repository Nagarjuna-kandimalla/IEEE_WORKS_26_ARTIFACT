"""A-independent allocation policy for CAMP S+P+C predictions."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any

import numpy as np


MODEL_QUANTILES = {
    "model_q90": 0.9,
    "model_q95": 0.95,
    "model_q99": 0.99,
    "model_q995": 0.995,
}


class APCResidualCalibration:
    """Strictly prior APC residuals at process, workflow, and global scope."""

    def __init__(
        self,
        *,
        scope_window: int,
        process_min_support: int,
        workflow_min_support: int,
    ) -> None:
        self.scope_window = int(scope_window)
        self.process_min_support = int(process_min_support)
        self.workflow_min_support = int(workflow_min_support)
        self.global_residuals: deque[float] = deque(maxlen=self.scope_window)
        self.workflow_residuals: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.scope_window)
        )
        self.process_residuals: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.scope_window)
        )

    @staticmethod
    def _workflow_key(row: dict[str, Any]) -> str:
        return str(row["workflow"]).strip().lower()

    @classmethod
    def _process_key(cls, row: dict[str, Any]) -> str:
        return f"{cls._workflow_key(row)}::{str(row['process']).strip()}"

    def add(
        self,
        row: dict[str, Any],
        *,
        base_mib: float,
        actual_mib: float,
    ) -> None:
        residual = math.log(
            max(float(actual_mib), 1e-6) / max(float(base_mib), 1e-6)
        )
        self.global_residuals.append(residual)
        self.workflow_residuals[self._workflow_key(row)].append(residual)
        self.process_residuals[self._process_key(row)].append(residual)

    @staticmethod
    def _higher_quantile(values: deque[float], probability: float) -> float:
        ordered = np.sort(np.asarray(values, dtype=float))
        position = int(math.ceil(float(probability) * len(ordered))) - 1
        return float(ordered[min(max(position, 0), len(ordered) - 1)])

    def correction(
        self,
        row: dict[str, Any],
        probability: float,
        *,
        scope_rule: str = "most_specific",
    ) -> tuple[float, dict[str, Any]]:
        process_values = self.process_residuals[self._process_key(row)]
        workflow_values = self.workflow_residuals[self._workflow_key(row)]
        candidates = []
        if len(process_values) >= self.process_min_support:
            candidates.append(
                (
                    "process",
                    process_values,
                    self.process_min_support,
                )
            )
        if len(workflow_values) >= self.workflow_min_support:
            candidates.append(
                (
                    "workflow",
                    workflow_values,
                    self.workflow_min_support,
                )
            )
        if self.global_residuals:
            candidates.append(
                (
                    "global",
                    self.global_residuals,
                    self.workflow_min_support,
                )
            )
        if not candidates:
            return 0.0, {
                "scope": "none",
                "support_count": 0,
                "confidence": 0.0,
                "candidate_corrections": {},
            }
        if scope_rule == "most_specific":
            selected = candidates[0]
        elif scope_rule == "max_available":
            selected = max(
                candidates,
                key=lambda candidate: self._higher_quantile(
                    candidate[1],
                    probability,
                ),
            )
        else:
            raise ValueError(f"unknown APC calibration scope rule: {scope_rule}")
        scope, values, minimum = selected
        correction = self._higher_quantile(values, probability)
        return correction, {
            "scope": scope,
            "support_count": len(values),
            "confidence": min(1.0, len(values) / max(minimum, 1)),
            "candidate_corrections": {
                candidate_scope: self._higher_quantile(
                    candidate_values,
                    probability,
                )
                for candidate_scope, candidate_values, _ in candidates
            },
        }


def allocate_apc_only(
    *,
    row: dict[str, Any],
    values: dict[float, float],
    policy: dict[str, Any],
    calibration: APCResidualCalibration,
    model_scope: str,
) -> dict[str, Any]:
    """Allocate from S+P+C predictions without accepting an A prediction."""

    base_policy = str(policy["base_policy"])
    if base_policy == "point_q50":
        base = float(values[0.5])
    else:
        quantile = MODEL_QUANTILES[base_policy]
        base = max(float(values[0.5]), float(values[quantile]))
    residual_probability = float(policy["residual_quantile"])
    raw_correction, calibration_scope = calibration.correction(
        row,
        residual_probability,
        scope_rule=str(policy.get("scope_rule", "most_specific")),
    )
    # Calibration may increase APC safety, but it cannot lower the learned
    # APC tail request.
    applied_correction = max(float(raw_correction), 0.0)
    request = int(
        math.ceil(max(base * math.exp(applied_correction), 1.0))
    )
    return {
        "base_policy": base_policy,
        "base_mib": base,
        "raw_request_mb": int(math.ceil(max(base, 1.0))),
        "residual_quantile": residual_probability,
        "calibration_log_correction": applied_correction,
        "raw_calibration_log_correction": float(raw_correction),
        "calibration_factor": float(math.exp(applied_correction)),
        "calibration_scope": calibration_scope["scope"],
        "calibration_support": calibration_scope["support_count"],
        "calibration_confidence": calibration_scope["confidence"],
        "calibration_candidate_corrections": calibration_scope[
            "candidate_corrections"
        ],
        "model_scope": model_scope,
        "request_mb": request,
        "uses_a_prediction": False,
    }
