"""CAMP allocation policy for direct A+P+C predictions."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


MODEL_QUANTILES = {
    "model_q90": 0.9,
    "model_q95": 0.95,
    "model_q99": 0.99,
    "model_q995": 0.995,
}


def allocate_memory(
    *,
    row: dict[str, Any],
    vector: np.ndarray,
    values: dict[float, float],
    policy: dict[str, Any],
    calibration: Any,
    model_scope: str,
) -> dict[str, Any]:
    """Apply the RQ2 hierarchical residual policy to A+P+C predictions."""

    base_policy = str(policy["base_policy"])
    if base_policy == "point_q50":
        base = float(values[0.5])
    else:
        quantile = MODEL_QUANTILES[base_policy]
        base = max(float(values[0.5]), float(values[quantile]))
    residual_probability = float(policy["residual_quantile"])
    corrections, calibration_scope = calibration.corrections(
        row,
        vector,
        [residual_probability],
        mode=str(policy["mode"]),
    )
    applied_correction = float(corrections[residual_probability])
    request = int(
        math.ceil(max(base * math.exp(applied_correction), 1.0))
    )
    return {
        "base_policy": base_policy,
        "base_mib": base,
        "raw_request_mb": int(math.ceil(max(base, 1.0))),
        "residual_quantile": residual_probability,
        "calibration_log_correction": applied_correction,
        "raw_calibration_log_correction": applied_correction,
        "calibration_factor": float(math.exp(applied_correction)),
        "calibration_scope": calibration_scope["calibration_scope"],
        "calibration_support": calibration_scope["calibration_support"],
        "calibration_confidence": calibration_scope[
            "calibration_confidence"
        ],
        "model_scope": model_scope,
        "request_mb": request,
        "uses_a_prediction": False,
    }
