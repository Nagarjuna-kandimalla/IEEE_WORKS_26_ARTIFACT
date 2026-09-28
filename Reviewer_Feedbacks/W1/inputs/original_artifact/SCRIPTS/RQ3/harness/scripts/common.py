"""Shared utilities for the isolated Sizey-versus-CAMP experiment."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "experiment.json"
MIB = 1024.0**2
GIB = 1024.0**3


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def read_tsv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep="\t", low_memory=False)


def log1p_numeric(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").fillna(0.0).clip(lower=0.0)
    return np.log1p(numeric)


def nearest_rank(values: Iterable[float], probability: float) -> float:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return math.nan
    rank = max(0, math.ceil(probability * len(array)) - 1)
    return float(np.sort(array)[rank])


def ceil_mib(values: np.ndarray) -> np.ndarray:
    return np.ceil(np.maximum(values, 1.0))


def common_task_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    """Add common first-allocation metrics to normalized task predictions."""

    result = frame.copy()
    actual = pd.to_numeric(result["actual_peak_mib"], errors="coerce")
    raw = pd.to_numeric(result["raw_prediction_mib"], errors="coerce")
    allocation = pd.to_numeric(result["first_allocation_mib"], errors="coerce")
    runtime = pd.to_numeric(result["runtime_seconds"], errors="coerce")

    result["absolute_error_mib"] = (raw - actual).abs()
    result["absolute_percentage_error"] = (
        result["absolute_error_mib"] / actual.replace(0.0, np.nan)
    )
    result["initial_oom"] = allocation < actual
    result["underallocation_mib"] = (actual - allocation).clip(lower=0.0)
    result["overallocation_mib"] = (allocation - actual).clip(lower=0.0)
    result["requested_gib"] = allocation / 1024.0
    result["overallocation_gib"] = result["overallocation_mib"] / 1024.0
    result["underallocation_gib"] = result["underallocation_mib"] / 1024.0
    result["usage_gib_hours"] = actual * runtime / 1024.0 / 3600.0
    first_waste_mib = np.where(
        allocation >= actual,
        allocation - actual,
        allocation,
    )
    result["first_attempt_wastage_gib_hours"] = (
        first_waste_mib * runtime / 1024.0 / 3600.0
    )
    return result

