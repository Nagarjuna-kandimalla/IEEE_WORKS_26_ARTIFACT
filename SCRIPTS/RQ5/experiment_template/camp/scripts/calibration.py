"""Support-weighted hierarchical log-residual allocation calibration."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

import numpy as np
import pandas as pd

from common import (
    MIB,
    ceil_mib,
    finite_higher_quantile,
    load_config,
    stable_fraction,
)
from history import SignatureScaler, hierarchy_keys


class ResidualIndex:
    def __init__(self, nearest_k: int, scope_window: int) -> None:
        self.nearest_k = nearest_k
        self.scope_window = scope_window
        self.vectors: list[np.ndarray] = []
        self.residuals: list[float] = []
        self.by_exact: dict[tuple[str, ...], list[int]] = defaultdict(list)
        self.by_input: dict[tuple[str, ...], list[int]] = defaultdict(list)
        self.by_context: dict[tuple[str, ...], list[int]] = defaultdict(list)
        self.by_workflow: dict[tuple[str, ...], list[int]] = defaultdict(list)
        self.by_global: dict[tuple[str, ...], list[int]] = defaultdict(list)

    def add(
        self,
        row: pd.Series | dict[str, Any],
        vector: np.ndarray,
        residual_log: float,
    ) -> None:
        if not np.isfinite(residual_log):
            return
        keys = hierarchy_keys(row)
        index = len(self.residuals)
        self.vectors.append(np.asarray(vector, dtype=float))
        self.residuals.append(float(residual_log))
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
    ) -> tuple[list[int], np.ndarray]:
        candidates = self.by_context.get(context, [])
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
        return (
            [candidates[int(position)] for position in positions],
            distances[positions],
        )

    def _values(self, indices: Iterable[int]) -> np.ndarray:
        selected = list(indices)[-self.scope_window :]
        return np.asarray(
            [self.residuals[index] for index in selected], dtype=float
        )

    def _quantile(self, indices: Iterable[int], probability: float) -> float:
        return finite_higher_quantile(self._values(indices), probability)

    def _scope_confidence(
        self,
        indices: list[int],
        distances: np.ndarray | None,
    ) -> float:
        if not indices:
            return 0.0
        values = self._values(indices)
        spread = (
            float(np.quantile(values, 0.95) - np.quantile(values, 0.5))
            if len(values) > 1
            else 1.0
        )
        support = len(indices) / (len(indices) + 16.0)
        stability = 1.0 / (1.0 + max(spread, 0.0))
        distance = 1.0
        if distances is not None and len(distances):
            distance = float(np.exp(-max(float(np.median(distances)), 0.0)))
        return float(support * stability * distance)

    def corrections(
        self,
        row: pd.Series | dict[str, Any],
        vector: np.ndarray,
        probabilities: list[float],
        *,
        mode: str,
    ) -> tuple[dict[float, float], dict[str, float | str]]:
        keys = hierarchy_keys(row)
        global_indices = self.by_global.get(keys["i5"], [])
        global_values = {
            probability: self._quantile(global_indices, probability)
            for probability in probabilities
        }
        if mode == "global":
            corrections = {
                probability: max(value, 0.0)
                if np.isfinite(value)
                else 0.0
                for probability, value in global_values.items()
            }
            return corrections, {
                "calibration_scope": "I5",
                "calibration_support": float(len(global_indices)),
                "calibration_confidence": self._scope_confidence(
                    global_indices, None
                ),
            }

        nearest_indices, nearest_distances = self._nearest(
            keys["i2"], vector
        )
        scopes = {
            "I0": (
                self.by_exact.get(keys["i0"], [])
                if keys["i0"] is not None
                else []
            ),
            "I1": (
                self.by_input.get(keys["i1"], [])
                if keys["i1"] is not None
                else []
            ),
            "I2": nearest_indices,
            "I3": self.by_context.get(keys["i3"], []),
            "I4": self.by_workflow.get(keys["i4"], []),
            "I5": global_indices,
        }
        config = load_config()["history"]
        rules = (
            ("I0", int(config["exact_min_support"])),
            ("I1", int(config["input_min_support"])),
            ("I2", int(config["nearest_min_support"])),
            ("I3", int(config["process_min_support"])),
            ("I4", int(config["workflow_min_support"])),
            ("I5", 1),
        )
        selected_scope = "I5"
        for scope, minimum in rules:
            if len(scopes[scope]) >= minimum:
                selected_scope = scope
                break
        if selected_scope in {"I0", "I1", "I2"}:
            parent_scope = "I3"
        elif selected_scope == "I3":
            parent_scope = "I4"
        else:
            parent_scope = "I5"
        selected_distances = (
            nearest_distances if selected_scope == "I2" else None
        )
        confidence = self._scope_confidence(
            scopes[selected_scope], selected_distances
        )
        corrections = {}
        for probability in probabilities:
            local_value = self._quantile(
                scopes[selected_scope], probability
            )
            parent_value = self._quantile(
                scopes[parent_scope], probability
            )
            global_value = global_values[probability]
            if not np.isfinite(parent_value):
                parent_value = global_value
            if not np.isfinite(local_value):
                local_value = parent_value
            if not np.isfinite(parent_value):
                parent_value = 0.0
            if not np.isfinite(local_value):
                local_value = 0.0
            blended = confidence * local_value + (1.0 - confidence) * parent_value
            corrections[probability] = max(float(blended), 0.0)
        return corrections, {
            "calibration_scope": selected_scope,
            "calibration_support": float(len(scopes[selected_scope])),
            "calibration_confidence": confidence,
        }


def _vector_map(
    frame: pd.DataFrame,
    scaler: SignatureScaler,
) -> dict[str, np.ndarray]:
    matrix = scaler.transform(frame)
    return dict(zip(frame["logical_task_id"].astype(str), matrix))


def build_residual_index(
    frame: pd.DataFrame,
    point_prediction_mib: np.ndarray,
    scaler: SignatureScaler,
    *,
    reference_mask: np.ndarray | None = None,
) -> ResidualIndex:
    config = load_config()["history"]
    index = ResidualIndex(
        nearest_k=int(config["nearest_k"]),
        scope_window=int(config["scope_window"]),
    )
    vectors = _vector_map(frame, scaler)
    if reference_mask is None:
        reference_mask = np.ones(len(frame), dtype=bool)
    actual_mib = (
        pd.to_numeric(frame["peak_memory_bytes"], errors="raise").to_numpy()
        / (1024.0**2)
    )
    for position, (_, row) in enumerate(frame.iterrows()):
        if not reference_mask[position]:
            continue
        point = max(float(point_prediction_mib[position]), 1e-6)
        residual = np.log(max(actual_mib[position], 1e-6) / point)
        index.add(
            row,
            vectors[str(row["logical_task_id"])],
            residual,
        )
    return index


def allocation_base(
    frame: pd.DataFrame,
    predictions: dict[float, np.ndarray],
    policy_name: str,
) -> np.ndarray:
    point = np.asarray(predictions[0.5], dtype=float)
    if policy_name == "point_q50":
        return point
    if policy_name in {"neighbour_p95", "neighbour_p99"}:
        suffix = "p95" if policy_name.endswith("p95") else "p99"
        neighbour = (
            pd.to_numeric(
                frame[f"history_i2_peak_{suffix}_bytes"],
                errors="coerce",
            ).to_numpy(dtype=float)
            / MIB
        )
        neighbour = np.where(np.isfinite(neighbour), neighbour, point)
        return np.maximum(point, neighbour)
    quantile_by_name = {
        "model_q90": 0.9,
        "model_q95": 0.95,
        "model_q99": 0.99,
        "model_q995": 0.995,
    }
    if policy_name not in quantile_by_name:
        raise ValueError(f"unknown allocation base policy: {policy_name}")
    return np.maximum(
        point,
        np.asarray(predictions[quantile_by_name[policy_name]], dtype=float),
    )


def select_allocation_policy(
    frame: pd.DataFrame,
    predictions: dict[float, np.ndarray],
    scaler: SignatureScaler,
    *,
    mode: str,
) -> tuple[dict[str, Any], pd.DataFrame]:
    allocation_config = load_config()["allocation"]
    reference_fraction = float(
        allocation_config["calibration_reference_fraction"]
    )
    fractions = frame["logical_task_id"].astype(str).map(stable_fraction)
    reference_mask = fractions.to_numpy() < reference_fraction
    selection_mask = ~reference_mask
    if not reference_mask.any() or not selection_mask.any():
        raise ValueError("calibration reference/selection partition is empty")

    vectors = _vector_map(frame, scaler)
    residual_quantiles = [
        float(value) for value in allocation_config["residual_quantiles"]
    ]
    actual = (
        pd.to_numeric(frame["peak_memory_bytes"], errors="raise").to_numpy()
        / (1024.0**2)
    )
    workflows = frame["workflow"].astype(str).to_numpy()
    candidate_rows = []
    candidate_allocations: dict[tuple[str, float], np.ndarray] = {}
    base_policies = list(allocation_config["base_policies"])
    if mode == "global":
        base_policies = [
            value for value in base_policies if value.startswith("model_")
        ] + ["point_q50"]
    for base_policy in base_policies:
        base = allocation_base(frame, predictions, base_policy)
        residual_index = build_residual_index(
            frame,
            base,
            scaler,
            reference_mask=reference_mask,
        )
        correction = {
            quantile: np.full(len(frame), np.nan, dtype=float)
            for quantile in residual_quantiles
        }
        for position, (_, row) in enumerate(frame.iterrows()):
            if not selection_mask[position]:
                continue
            values, _ = residual_index.corrections(
                row,
                vectors[str(row["logical_task_id"])],
                residual_quantiles,
                mode=mode,
            )
            for quantile, value in values.items():
                correction[quantile][position] = value
        for residual_quantile in residual_quantiles:
            allocation = ceil_mib(
                base * np.exp(correction[residual_quantile])
            )
            candidate_allocations[
                (base_policy, residual_quantile)
            ] = allocation
            selected_allocation = allocation[selection_mask]
            selected_actual = actual[selection_mask]
            coverage = float(
                np.mean(selected_allocation >= selected_actual)
            )
            workflow_coverages = {}
            for workflow in sorted(set(workflows[selection_mask])):
                mask = selection_mask & (workflows == workflow)
                workflow_coverages[workflow] = float(
                    np.mean(allocation[mask] >= actual[mask])
                )
            candidate_rows.append(
                {
                    "mode": mode,
                    "base_policy": base_policy,
                    "residual_quantile": residual_quantile,
                    "selection_rows": int(selection_mask.sum()),
                    "global_coverage": coverage,
                    "minimum_workflow_coverage": min(
                        workflow_coverages.values()
                    ),
                    "request_to_peak_ratio": float(
                        selected_allocation.sum() / selected_actual.sum()
                    ),
                    "selection_oom_tasks": int(
                        np.sum(selected_allocation < selected_actual)
                    ),
                    **{
                        f"coverage_{workflow}": value
                        for workflow, value in workflow_coverages.items()
                    },
                }
            )
    candidates = pd.DataFrame.from_records(candidate_rows)
    if (
        mode == "hierarchical"
        and allocation_config.get("enhanced_policy_scope") == "per_process"
    ):
        process_policies = {}
        combined_allocation = np.full(len(frame), np.nan, dtype=float)
        all_constraints_met = True
        target_process_coverage = float(
            allocation_config["target_process_coverage"]
        )
        process_selection_details = {}
        selection_frame = frame.loc[selection_mask]
        for keys, group in selection_frame.groupby(
            ["workflow", "process"], sort=True
        ):
            positions = group.index.to_numpy(dtype=int)
            process_actual = actual[positions]
            choices = []
            for (base_policy, residual_quantile), allocation in (
                candidate_allocations.items()
            ):
                process_allocation = allocation[positions]
                choices.append(
                    {
                        "base_policy": base_policy,
                        "residual_quantile": residual_quantile,
                        "coverage": float(
                            np.mean(process_allocation >= process_actual)
                        ),
                        "request_to_peak_ratio": float(
                            process_allocation.sum() / process_actual.sum()
                        ),
                        "oom_tasks": int(
                            np.sum(process_allocation < process_actual)
                        ),
                    }
                )
            choice_frame = pd.DataFrame.from_records(choices)
            eligible_choices = choice_frame.loc[
                choice_frame["coverage"] >= target_process_coverage
            ]
            if len(eligible_choices):
                chosen_process = eligible_choices.sort_values(
                    [
                        "request_to_peak_ratio",
                        "oom_tasks",
                        "residual_quantile",
                        "base_policy",
                    ]
                ).iloc[0]
                process_status = "met"
            else:
                all_constraints_met = False
                chosen_process = choice_frame.sort_values(
                    ["coverage", "request_to_peak_ratio"],
                    ascending=[False, True],
                ).iloc[0]
                process_status = "unmet_best_available"
            process_key = f"{keys[0]}::{keys[1]}"
            process_policies[process_key] = {
                "base_policy": str(chosen_process["base_policy"]),
                "residual_quantile": float(
                    chosen_process["residual_quantile"]
                ),
            }
            process_selection_details[process_key] = {
                "status": process_status,
                "selection_rows": int(len(positions)),
                "coverage": float(chosen_process["coverage"]),
                "request_to_peak_ratio": float(
                    chosen_process["request_to_peak_ratio"]
                ),
                "oom_tasks": int(chosen_process["oom_tasks"]),
            }
            chosen_allocation = candidate_allocations[
                (
                    str(chosen_process["base_policy"]),
                    float(chosen_process["residual_quantile"]),
                )
            ]
            process_mask = (
                frame["workflow"].astype(str).eq(str(keys[0]))
                & frame["process"].astype(str).eq(str(keys[1]))
            ).to_numpy()
            combined_allocation[process_mask] = chosen_allocation[
                process_mask
            ]
        selected_allocation = combined_allocation[selection_mask]
        selected_actual = actual[selection_mask]
        workflow_coverages = {}
        for workflow in sorted(set(workflows[selection_mask])):
            mask = selection_mask & (workflows == workflow)
            workflow_coverages[workflow] = float(
                np.mean(
                    combined_allocation[mask] >= actual[mask]
                )
            )
        policy = {
            "mode": mode,
            "policy_scope": "per_process",
            "constraint_status": (
                "met" if all_constraints_met else "partially_met"
            ),
            "target_process_coverage": target_process_coverage,
            "selection_global_coverage": float(
                np.mean(selected_allocation >= selected_actual)
            ),
            "selection_minimum_workflow_coverage": min(
                workflow_coverages.values()
            ),
            "selection_request_to_peak_ratio": float(
                selected_allocation.sum() / selected_actual.sum()
            ),
            "selection_oom_tasks": int(
                np.sum(selected_allocation < selected_actual)
            ),
            "reference_rows": int(reference_mask.sum()),
            "selection_rows": int(selection_mask.sum()),
            "process_policies": process_policies,
            "process_selection_details": process_selection_details,
        }
        return policy, candidates

    eligible = candidates.loc[
        (
            candidates["global_coverage"]
            >= float(allocation_config["target_global_coverage"])
        )
        & (
            candidates["minimum_workflow_coverage"]
            >= float(allocation_config["minimum_workflow_coverage"])
        )
    ]
    if len(eligible):
        chosen = eligible.sort_values(
            [
                "request_to_peak_ratio",
                "selection_oom_tasks",
                "residual_quantile",
                "base_policy",
            ]
        ).iloc[0]
        constraint_status = "met"
    else:
        chosen = candidates.sort_values(
            [
                "minimum_workflow_coverage",
                "global_coverage",
                "request_to_peak_ratio",
            ],
            ascending=[False, False, True],
        ).iloc[0]
        constraint_status = "unmet_best_available"
    policy = {
        "mode": mode,
        "base_policy": str(chosen["base_policy"]),
        "residual_quantile": float(chosen["residual_quantile"]),
        "constraint_status": constraint_status,
        "selection_global_coverage": float(chosen["global_coverage"]),
        "selection_minimum_workflow_coverage": float(
            chosen["minimum_workflow_coverage"]
        ),
        "selection_request_to_peak_ratio": float(
            chosen["request_to_peak_ratio"]
        ),
        "selection_oom_tasks": int(chosen["selection_oom_tasks"]),
        "reference_rows": int(reference_mask.sum()),
        "selection_rows": int(selection_mask.sum()),
    }
    return policy, candidates


def apply_policy_sequentially(
    initial: pd.DataFrame,
    test: pd.DataFrame,
    initial_predictions: dict[float, np.ndarray],
    test_predictions: dict[float, np.ndarray],
    scaler: SignatureScaler,
    policy: dict[str, Any],
    *,
    update_during_test: bool,
) -> pd.DataFrame:
    process_policies = policy.get("process_policies")
    if process_policies:
        base_names = sorted(
            {
                str(value["base_policy"])
                for value in process_policies.values()
            }
        )
    else:
        base_names = [str(policy["base_policy"])]
    initial_bases = {
        name: allocation_base(initial, initial_predictions, name)
        for name in base_names
    }
    test_bases = {
        name: allocation_base(test, test_predictions, name)
        for name in base_names
    }
    residual_indices = {
        name: build_residual_index(
            initial, initial_bases[name], scaler
        )
        for name in base_names
    }
    vectors = _vector_map(test, scaler)
    actual = (
        pd.to_numeric(test["peak_memory_bytes"], errors="raise").to_numpy()
        / (1024.0**2)
    )
    records = []
    for position, (_, row) in enumerate(test.iterrows()):
        task_id = str(row["logical_task_id"])
        if process_policies:
            process_key = f"{row['workflow']}::{row['process']}"
            task_policy = process_policies.get(process_key)
            if task_policy is None:
                raise ValueError(
                    f"missing process allocation policy: {process_key}"
                )
        else:
            task_policy = policy
        base_name = str(task_policy["base_policy"])
        residual_quantile = float(task_policy["residual_quantile"])
        residual_index = residual_indices[base_name]
        corrections, scope = residual_index.corrections(
            row,
            vectors[task_id],
            [residual_quantile],
            mode=str(policy["mode"]),
        )
        correction = corrections[residual_quantile]
        base_bound = test_bases[base_name][position]
        local_bound = base_bound * np.exp(correction)
        allocation = float(
            ceil_mib([local_bound])[0]
        )
        records.append(
            {
                "calibration_log_correction": correction,
                "calibration_factor": float(np.exp(correction)),
                "calibration_scope": scope["calibration_scope"],
                "calibration_support": scope["calibration_support"],
                "calibration_confidence": scope[
                    "calibration_confidence"
                ],
                "allocation_base_policy": base_name,
                "allocation_residual_quantile": residual_quantile,
                "local_residual_bound_mib": local_bound,
                "allocation_base_mib": base_bound,
                "first_allocation_mib": allocation,
            }
        )
        if update_during_test:
            for name, index in residual_indices.items():
                residual = np.log(
                    max(actual[position], 1e-6)
                    / max(test_bases[name][position], 1e-6)
                )
                index.add(row, vectors[task_id], residual)
    return pd.DataFrame.from_records(records)
