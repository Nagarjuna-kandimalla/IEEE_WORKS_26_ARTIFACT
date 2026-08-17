"""Shared utilities for the isolated CAMP Design 3 experiment."""

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


def resolve_config_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def read_tsv(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path, sep="\t", low_memory=False)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_fraction(value: str) -> float:
    numerator = int(stable_hash(value)[:15], 16)
    return numerator / float(16**15 - 1)


def finite_higher_quantile(
    values: Iterable[float],
    probability: float,
) -> float:
    clean = np.asarray(list(values), dtype=float)
    clean = clean[np.isfinite(clean)]
    if not len(clean):
        return math.nan
    rank_level = min(
        1.0,
        math.ceil((len(clean) + 1) * probability) / len(clean),
    )
    return float(np.quantile(clean, rank_level, method="higher"))


def ceil_mib(values: np.ndarray | pd.Series | float) -> np.ndarray:
    return np.ceil(np.maximum(np.asarray(values, dtype=float), 1.0))


def load_experiment_rows(seed: int) -> pd.DataFrame:
    config = load_config()
    source = read_tsv(resolve_config_path(config["source_data"]))
    manifests = []
    manifest_root = resolve_config_path(config["split_manifest_root"])
    for workflow in sorted(source["workflow"].astype(str).unique()):
        manifest = read_tsv(manifest_root / f"{workflow}_{seed}.tsv")
        manifests.append(
            manifest[["logical_task_id", "role", "replay_position"]]
        )
    split = pd.concat(manifests, ignore_index=True)
    merged = source.merge(
        split,
        on="logical_task_id",
        how="inner",
        validate="one_to_one",
    )
    if len(merged) != len(source):
        raise ValueError("split manifests do not cover the source population")
    return merged


def replay_sort(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values(
        ["replay_position", "workflow", "process", "logical_task_id"],
        kind="mergesort",
    )


def training_sort(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["_decision_time"] = pd.to_datetime(
        result["decision_time"], errors="coerce", utc=True
    )
    return result.sort_values(
        ["_decision_time", "workflow", "process", "logical_task_id"],
        kind="mergesort",
    ).drop(columns="_decision_time")
