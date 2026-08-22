"""Causal I0-I5 task-instance and nearest-neighbour history."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from common import load_config, replay_sort, training_sort


SIGNATURE_COLUMNS = (
    "static_input_bytes",
    "staged_original_input_bytes",
    "staged_intermediate_input_bytes",
    "staged_external_input_bytes",
    "requested_threads",
    "requested_java_heap_mb",
    "coverage",
    "interval_length_bp",
    "worker_count",
    "worker_vcpu",
    "worker_memory_gib",
    "has_str_region",
)

SCOPES = ("i0", "i1", "i2", "i3", "i4", "i5")
STAT_NAMES = (
    "support",
    "peak_p50_bytes",
    "peak_p95_bytes",
    "peak_p99_bytes",
    "consumed_p50_bytes",
    "consumed_p95_bytes",
    "consumed_p99_bytes",
    "consumed_to_input_p50",
    "peak_log_spread",
    "confidence",
)
HISTORY_NUMERIC_COLUMNS = tuple(
    f"history_{scope}_{stat}"
    for scope in SCOPES
    for stat in STAT_NAMES
) + (
    "history_i2_distance_p50",
    "history_i2_distance_min",
    "history_scope_support",
    "history_scope_confidence",
)


def _clean(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def hierarchy_keys(row: pd.Series | dict[str, Any]) -> dict[str, tuple[str, ...] | None]:
    workflow = _clean(row.get("workflow")) or "unknown"
    process = _clean(row.get("process")) or "unknown"
    version = _clean(row.get("version")) or "unknown"
    system = _clean(row.get("system_config_id")) or "unknown"
    context = (workflow, process, version, system)
    task_instance = _clean(row.get("task_instance"))
    split_group = _clean(row.get("split_group_id"))
    input_identity = _clean(row.get("input_identity"))

    exact = None
    if task_instance is not None and split_group is not None:
        exact = context + (task_instance, split_group)
    input_key = None
    if input_identity is not None:
        input_key = context + (input_identity,)
    return {
        "i0": exact,
        "i1": input_key,
        "i2": context,
        "i3": context,
        "i4": (workflow, version, system),
        "i5": (system,),
    }


@dataclass
class SignatureScaler:
    median: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, frame: pd.DataFrame) -> "SignatureScaler":
        matrix = cls._raw(frame)
        median = np.nanmedian(matrix, axis=0)
        median = np.where(np.isfinite(median), median, 0.0)
        filled = np.where(np.isfinite(matrix), matrix, median)
        q25 = np.quantile(filled, 0.25, axis=0)
        q75 = np.quantile(filled, 0.75, axis=0)
        scale = q75 - q25
        scale = np.where(scale > 1e-9, scale, 1.0)
        return cls(median=median, scale=scale)

    @staticmethod
    def _raw(frame: pd.DataFrame) -> np.ndarray:
        columns = []
        for column in SIGNATURE_COLUMNS:
            if column == "has_str_region":
                values = frame[column].eq(True).astype(float)
            else:
                values = pd.to_numeric(frame[column], errors="coerce")
                values = np.log1p(values.clip(lower=0.0))
            columns.append(values.to_numpy(dtype=float))
        return np.column_stack(columns)

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        matrix = self._raw(frame)
        matrix = np.where(np.isfinite(matrix), matrix, self.median)
        return np.clip((matrix - self.median) / self.scale, -12.0, 12.0)


class HistoryIndex:
    """Outcome index queried before the current task is inserted."""

    def __init__(self, nearest_k: int, scope_window: int = 512) -> None:
        self.nearest_k = nearest_k
        self.scope_window = scope_window
        self.vectors: list[np.ndarray] = []
        self.task_ids: list[str] = []
        self.peak: list[float] = []
        self.consumed: list[float] = []
        self.static_input: list[float] = []
        self.by_exact: dict[tuple[str, ...], list[int]] = defaultdict(list)
        self.by_input: dict[tuple[str, ...], list[int]] = defaultdict(list)
        self.by_context: dict[tuple[str, ...], list[int]] = defaultdict(list)
        self.by_workflow: dict[tuple[str, ...], list[int]] = defaultdict(list)
        self.by_global: dict[tuple[str, ...], list[int]] = defaultdict(list)

    def add(self, row: pd.Series | dict[str, Any], vector: np.ndarray) -> None:
        keys = hierarchy_keys(row)
        index = len(self.peak)
        self.vectors.append(np.asarray(vector, dtype=float))
        self.task_ids.append(str(row["logical_task_id"]))
        self.peak.append(float(row["peak_memory_bytes"]))
        self.consumed.append(float(row["ebpf_total_consumed_bytes"]))
        self.static_input.append(max(float(row["static_input_bytes"]), 1.0))
        if keys["i0"] is not None:
            self.by_exact[keys["i0"]].append(index)
        if keys["i1"] is not None:
            self.by_input[keys["i1"]].append(index)
        self.by_context[keys["i3"]].append(index)
        self.by_workflow[keys["i4"]].append(index)
        self.by_global[keys["i5"]].append(index)

    def _nearest(
        self,
        context: tuple[str, ...],
        vector: np.ndarray,
        *,
        exclude_task_id: str | None = None,
    ) -> tuple[list[int], np.ndarray]:
        candidates = self.by_context.get(context, [])
        if exclude_task_id is not None:
            candidates = [
                index
                for index in candidates
                if self.task_ids[index] != exclude_task_id
            ]
        if not candidates:
            return [], np.asarray([], dtype=float)
        matrix = np.asarray([self.vectors[index] for index in candidates])
        distances = np.sqrt(np.mean((matrix - vector) ** 2, axis=1))
        count = min(self.nearest_k, len(candidates))
        if count < len(candidates):
            positions = np.argpartition(distances, count - 1)[:count]
            positions = positions[np.argsort(distances[positions])]
        else:
            positions = np.argsort(distances)
        indices = [candidates[int(position)] for position in positions]
        return indices, distances[positions]

    def _stats(
        self,
        indices: Iterable[int],
        *,
        distances: np.ndarray | None = None,
    ) -> dict[str, float]:
        all_indices = list(indices)
        total_support = len(all_indices)
        selected = np.asarray(all_indices[-self.scope_window :], dtype=int)
        if not total_support:
            return {name: np.nan for name in STAT_NAMES} | {
                "support": 0.0,
                "confidence": 0.0,
            }
        peak = np.asarray([self.peak[index] for index in selected], dtype=float)
        consumed = np.asarray(
            [self.consumed[index] for index in selected], dtype=float
        )
        static_input = np.asarray(
            [self.static_input[index] for index in selected], dtype=float
        )
        p50, p95, p99 = np.quantile(peak, [0.5, 0.95, 0.99])
        c50, c95, c99 = np.quantile(consumed, [0.5, 0.95, 0.99])
        ratio50 = float(np.quantile(consumed / static_input, 0.5))
        spread = float(max(np.log1p(p95) - np.log1p(p50), 0.0))
        support_confidence = total_support / (total_support + 16.0)
        stability = 1.0 / (1.0 + spread)
        distance_confidence = 1.0
        if distances is not None and len(distances):
            distance_confidence = float(
                np.exp(-max(float(np.median(distances)), 0.0))
            )
        return {
            "support": float(total_support),
            "peak_p50_bytes": float(p50),
            "peak_p95_bytes": float(p95),
            "peak_p99_bytes": float(p99),
            "consumed_p50_bytes": float(c50),
            "consumed_p95_bytes": float(c95),
            "consumed_p99_bytes": float(c99),
            "consumed_to_input_p50": ratio50,
            "peak_log_spread": spread,
            "confidence": float(
                support_confidence * stability * distance_confidence
            ),
        }

    def snapshot(
        self,
        row: pd.Series | dict[str, Any],
        vector: np.ndarray,
        *,
        exclude_task_id: str | None = None,
    ) -> dict[str, float | str]:
        keys = hierarchy_keys(row)
        nearest_indices, nearest_distances = self._nearest(
            keys["i2"],
            vector,
            exclude_task_id=exclude_task_id,
        )
        def without_current(indices: Iterable[int]) -> list[int]:
            if exclude_task_id is None:
                return list(indices)
            return [
                index
                for index in indices
                if self.task_ids[index] != exclude_task_id
            ]

        index_sets = {
            "i0": (
                without_current(self.by_exact.get(keys["i0"], []))
                if keys["i0"] is not None
                else []
            ),
            "i1": (
                without_current(self.by_input.get(keys["i1"], []))
                if keys["i1"] is not None
                else []
            ),
            "i2": nearest_indices,
            "i3": without_current(self.by_context.get(keys["i3"], [])),
            "i4": without_current(self.by_workflow.get(keys["i4"], [])),
            "i5": without_current(self.by_global.get(keys["i5"], [])),
        }
        scope_stats = {}
        result: dict[str, float | str] = {}
        for scope in SCOPES:
            distances = nearest_distances if scope == "i2" else None
            stats = self._stats(index_sets[scope], distances=distances)
            scope_stats[scope] = stats
            for name, value in stats.items():
                result[f"history_{scope}_{name}"] = value
        result["history_i2_distance_p50"] = (
            float(np.median(nearest_distances))
            if len(nearest_distances)
            else np.nan
        )
        result["history_i2_distance_min"] = (
            float(np.min(nearest_distances))
            if len(nearest_distances)
            else np.nan
        )

        config = load_config()["history"]
        rules = (
            ("i0", config["exact_min_support"]),
            ("i1", config["input_min_support"]),
            ("i2", config["nearest_min_support"]),
            ("i3", config["process_min_support"]),
            ("i4", config["workflow_min_support"]),
            ("i5", 1),
        )
        selected_scope = "cold_start"
        selected_support = 0.0
        selected_confidence = 0.0
        for scope, minimum in rules:
            stats = scope_stats[scope]
            if stats["support"] >= minimum:
                selected_scope = scope.upper()
                selected_support = stats["support"]
                selected_confidence = stats["confidence"]
                break
        result["history_scope"] = selected_scope
        result["history_scope_support"] = selected_support
        result["history_scope_confidence"] = selected_confidence
        return result


def _vector_map(
    frame: pd.DataFrame,
    vectors: np.ndarray,
) -> dict[str, np.ndarray]:
    return dict(
        zip(frame["logical_task_id"].astype(str).to_numpy(), vectors)
    )


def materialize_causal_history(
    initial: pd.DataFrame,
    test: pd.DataFrame,
    scaler: SignatureScaler,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Materialize initial and replay features with current-row exclusion."""

    config = load_config()["history"]
    initial_vectors = scaler.transform(initial)
    test_vectors = scaler.transform(test)
    vectors = _vector_map(initial, initial_vectors) | _vector_map(
        test, test_vectors
    )

    state = HistoryIndex(
        nearest_k=int(config["nearest_k"]),
        scope_window=int(config["scope_window"]),
    )
    for _, row in training_sort(initial).iterrows():
        task_id = str(row["logical_task_id"])
        state.add(row, vectors[task_id])

    initial_records = []
    for _, row in initial.iterrows():
        task_id = str(row["logical_task_id"])
        snapshot = state.snapshot(
            row,
            vectors[task_id],
            exclude_task_id=task_id,
        )
        snapshot["logical_task_id"] = task_id
        initial_records.append(snapshot)

    test_records = []
    for _, row in replay_sort(test).iterrows():
        task_id = str(row["logical_task_id"])
        snapshot = state.snapshot(row, vectors[task_id])
        snapshot["logical_task_id"] = task_id
        test_records.append(snapshot)
        state.add(row, vectors[task_id])

    initial_history = pd.DataFrame.from_records(initial_records)
    test_history = pd.DataFrame.from_records(test_records)
    initial_result = initial.merge(
        initial_history,
        on="logical_task_id",
        how="left",
        validate="one_to_one",
    )
    test_result = test.merge(
        test_history,
        on="logical_task_id",
        how="left",
        validate="one_to_one",
    )
    return initial_result, test_result
