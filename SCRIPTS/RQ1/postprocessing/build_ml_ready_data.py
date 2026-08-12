#!/usr/bin/env python3
"""Build and validate the final CAMP ML data artifacts."""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import heapq
import math
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


BASE_WORKFLOWS = (
    "minimap2",
    "sarek",
    "seqinspector",
    "taxprofiler",
    "viralmetagenome",
)
ALL_WORKFLOWS = (*BASE_WORKFLOWS, "gatk")
GATK_VERSION = "gatk-4.5.0.0+custom-nextflow"
SYSTEM_CONFIG_ID = "sc13"
WORKER_COUNT = 13
WORKER_VCPU = 32
WORKER_MEMORY_GIB = 256
MIN_HISTORY_COUNT = 10
PRIMARY_FRACTIONS = (0.70, 0.15, 0.15)
SPLIT_LINK_FIELDS = ("input_identity", "task_instance", "sample_id")
MAX_SPLIT_LINK_ROWS = 100

OOD_CALIBRATION_WORKFLOW = {
    "minimap2": "sarek",
    "sarek": "seqinspector",
    "seqinspector": "taxprofiler",
    "taxprofiler": "viralmetagenome",
    "viralmetagenome": "minimap2",
    "gatk": "taxprofiler",
}

PAIR_KEY_FIELDS = (
    "workflow",
    "dataset_id",
    "process",
    "task_instance",
    "unit_id",
    "input_identity",
)

MASTER_COLUMNS = (
    "logical_task_id",
    "workflow_cohort_id",
    "workflow",
    "process",
    "bucket",
    "task_type",
    "version",
    "workflow_source_fingerprint",
    "dataset_id",
    "task_instance",
    "sample_id",
    "unit_id",
    "input_identity",
    "split_group_id",
    "system_config_id",
    "source_system_config_id",
    "worker_count",
    "worker_vcpu",
    "worker_memory_gib",
    "requested_threads",
    "requested_java_heap_mb",
    "coverage",
    "interval_length_bp",
    "has_str_region",
    "static_input_bytes",
    "static_input_derivation",
    "staged_original_input_bytes",
    "staged_intermediate_input_bytes",
    "staged_external_input_bytes",
    "staged_total_input_bytes",
    "static_manifest_input_bytes",
    "decision_time",
    "completion_time",
    "baseline_event_time",
    "baseline_completion_time",
    "strace_event_time",
    "strace_completion_time",
    "ebpf_event_time",
    "ebpf_completion_time",
    "baseline_parent_run_id",
    "strace_parent_run_id",
    "ebpf_parent_run_id",
    "baseline_task_id",
    "strace_task_id",
    "ebpf_task_id",
    "baseline_task_hash",
    "strace_task_hash",
    "ebpf_task_hash",
    "peak_memory_bytes",
    "runtime_seconds",
    "runtime_available",
    "baseline_exit_code",
    "baseline_trace_status",
    "exp0_consumed_proxy_bytes",
    "exp0_consumed_signal",
    "strace_read_return_bytes",
    "strace_read_calls",
    "strace_mmap_calls",
    "strace_signal_available",
    "ebpf_read_return_bytes",
    "ebpf_mmap_page_fault_bytes",
    "ebpf_total_consumed_bytes",
    "ebpf_signal_available",
    "observed_oom_flag",
)

PRELAUNCH_COLUMNS = (
    "logical_task_id",
    "decision_time",
    "workflow_cohort_id",
    "workflow",
    "process",
    "bucket",
    "task_type",
    "version",
    "workflow_source_fingerprint",
    "dataset_id",
    "task_instance",
    "sample_id",
    "unit_id",
    "input_identity",
    "split_group_id",
    "system_config_id",
    "source_system_config_id",
    "worker_count",
    "worker_vcpu",
    "worker_memory_gib",
    "requested_threads",
    "requested_java_heap_mb",
    "coverage",
    "interval_length_bp",
    "has_str_region",
    "static_input_bytes",
    "static_input_derivation",
    "staged_original_input_bytes",
    "staged_intermediate_input_bytes",
    "staged_external_input_bytes",
    "staged_total_input_bytes",
    "static_manifest_input_bytes",
)

OUTCOME_COLUMNS = (
    "logical_task_id",
    "completion_time",
    "workflow",
    "process",
    "version",
    "baseline_parent_run_id",
    "strace_parent_run_id",
    "ebpf_parent_run_id",
    "baseline_task_id",
    "strace_task_id",
    "ebpf_task_id",
    "peak_memory_bytes",
    "runtime_seconds",
    "runtime_available",
    "baseline_exit_code",
    "baseline_trace_status",
    "exp0_consumed_proxy_bytes",
    "exp0_consumed_signal",
    "strace_read_return_bytes",
    "strace_read_calls",
    "strace_mmap_calls",
    "strace_signal_available",
    "ebpf_read_return_bytes",
    "ebpf_mmap_page_fault_bytes",
    "ebpf_total_consumed_bytes",
    "ebpf_signal_available",
    "observed_oom_flag",
)

PRIMARY_SPLIT_COLUMNS = (
    "logical_task_id",
    "workflow",
    "workflow_cohort_id",
    "split_group_id",
    "decision_time",
    "split",
    "split_strategy",
    "parent_overlap_policy",
)

OOD_SPLIT_COLUMNS = (
    "fold_id",
    "held_out_workflow",
    "calibration_workflow",
    "logical_task_id",
    "workflow",
    "workflow_cohort_id",
    "role",
    "split_strategy",
)

HISTORY_COLUMNS = (
    "logical_task_id",
    "workflow",
    "process",
    "version",
    "decision_time",
    "split_or_role",
    "history_g0_count",
    "history_g1_count",
    "history_g2_count",
    "history_g3_count",
    "history_g4_count",
    "history_level_used",
    "prior_group_count",
    "prior_peak_median_bytes",
    "prior_peak_q95_bytes",
    "prior_consumed_median_bytes",
    "prior_consumed_q95_bytes",
    "prior_consumed_to_input_median",
    "history_rule",
)

OOD_HISTORY_COLUMNS = ("fold_id", *HISTORY_COLUMNS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workflows-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "workflows_run_results",
    )
    return parser.parse_args()


def read_table(path: Path, delimiter: str = "\t") -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def write_table(
    path: Path,
    rows: Iterable[dict[str, object]],
    columns: Iterable[str],
    delimiter: str = "\t",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(columns),
            delimiter=delimiter,
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def parse_number(value: object) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def positive_int(value: object) -> int | None:
    parsed = parse_number(value)
    return int(round(parsed)) if parsed is not None and parsed > 0 else None


def int_string(value: object) -> str:
    parsed = parse_number(value)
    return "" if parsed is None else str(int(round(parsed)))


def bool_string(value: bool) -> str:
    return "true" if value else "false"


def stable_id(*values: object, length: int = 20) -> str:
    payload = "\x1f".join(str(value) for value in values)
    return hashlib.sha256(payload.encode()).hexdigest()[:length]


def parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


_DURATION_TOKEN = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(ms|d|h|m|s)")
_DURATION_SCALE = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
}


def duration_seconds(value: object) -> float | None:
    tokens = _DURATION_TOKEN.findall(str(value or "").strip())
    if not tokens:
        return None
    return sum(float(amount) * _DURATION_SCALE[unit] for amount, unit in tokens)


def completion_time(trace: dict[str, str]) -> str:
    submit = parse_datetime(trace.get("submit"))
    duration = duration_seconds(trace.get("duration"))
    if submit is None or duration is None:
        return ""
    return (submit + timedelta(seconds=duration)).isoformat(sep=" ")


def source_fingerprint(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    matched = False
    for path in paths:
        if not path.is_file():
            continue
        matched = True
        digest.update(str(path.name).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16] if matched else "unavailable"


def pair_key(row: dict[str, object]) -> tuple[str, ...]:
    return tuple(str(row.get(field, "")) for field in PAIR_KEY_FIELDS)


def choose_unique(
    grouped: dict[tuple[str, ...], list[dict[str, str]]],
    key: tuple[str, ...],
) -> dict[str, str] | None:
    candidates = grouped.get(key, [])
    return candidates[0] if len(candidates) == 1 else None


def build_base_rows(
    results_root: Path,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    source_path = results_root / "task_level" / "all_task_observations.tsv"
    rows = read_table(source_path)
    grouped: dict[int, dict[tuple[str, ...], list[dict[str, str]]]] = {
        0: defaultdict(list),
        1: defaultdict(list),
        2: defaultdict(list),
    }
    for row in rows:
        experiment_id = int(row["experiment_id"])
        grouped[experiment_id][pair_key(row)].append(row)

    output: list[dict[str, object]] = []
    stats: Counter[str] = Counter()
    for key in sorted(set(grouped[0]) & set(grouped[2])):
        baseline = choose_unique(grouped[0], key)
        ebpf = choose_unique(grouped[2], key)
        if baseline is None or ebpf is None:
            stats["nonunique_baseline_or_ebpf"] += 1
            continue
        if baseline["ml_eligible"] != "true" or ebpf["audit_signal_eligible"] != "true":
            stats["ineligible_baseline_or_ebpf"] += 1
            continue
        strace = choose_unique(grouped[1], key)
        logical_id = f"{baseline['workflow']}:{stable_id(*key)}"
        cohort_id = f"{baseline['workflow']}:{baseline['dataset_id']}:matched_exp012"
        output.append(
            {
                "logical_task_id": logical_id,
                "workflow_cohort_id": cohort_id,
                "workflow": baseline["workflow"],
                "process": baseline["process"],
                "bucket": baseline["bucket"],
                "task_type": baseline["task_type"],
                "version": baseline["version"],
                "workflow_source_fingerprint": baseline[
                    "workflow_source_fingerprint"
                ],
                "dataset_id": baseline["dataset_id"],
                "task_instance": baseline["task_instance"],
                "sample_id": "",
                "unit_id": baseline["unit_id"],
                "input_identity": baseline["input_identity"],
                "split_group_id": "",
                "system_config_id": SYSTEM_CONFIG_ID,
                "source_system_config_id": baseline["system_config_id"],
                "worker_count": baseline["worker_count"],
                "worker_vcpu": baseline["worker_vcpu"],
                "worker_memory_gib": baseline["worker_memory_gib"],
                "requested_threads": "",
                "requested_java_heap_mb": "",
                "coverage": "",
                "interval_length_bp": "",
                "has_str_region": "",
                "static_input_bytes": baseline["static_input_bytes"],
                "static_input_derivation": baseline["static_input_derivation"],
                "staged_original_input_bytes": baseline[
                    "staged_original_input_bytes"
                ],
                "staged_intermediate_input_bytes": baseline[
                    "staged_intermediate_input_bytes"
                ],
                "staged_external_input_bytes": baseline[
                    "staged_external_input_bytes"
                ],
                "staged_total_input_bytes": baseline["staged_total_input_bytes"],
                "static_manifest_input_bytes": baseline[
                    "static_manifest_input_bytes"
                ],
                "decision_time": ebpf["event_time"],
                "completion_time": ebpf["completion_time"],
                "baseline_event_time": baseline["event_time"],
                "baseline_completion_time": baseline["completion_time"],
                "strace_event_time": strace["event_time"] if strace else "",
                "strace_completion_time": strace["completion_time"] if strace else "",
                "ebpf_event_time": ebpf["event_time"],
                "ebpf_completion_time": ebpf["completion_time"],
                "baseline_parent_run_id": baseline["parent_run_id"],
                "strace_parent_run_id": strace["parent_run_id"] if strace else "",
                "ebpf_parent_run_id": ebpf["parent_run_id"],
                "baseline_task_id": baseline["task_id"],
                "strace_task_id": strace["task_id"] if strace else "",
                "ebpf_task_id": ebpf["task_id"],
                "baseline_task_hash": baseline["task_hash"],
                "strace_task_hash": strace["task_hash"] if strace else "",
                "ebpf_task_hash": ebpf["task_hash"],
                "peak_memory_bytes": baseline["peak_memory_bytes"],
                "runtime_seconds": baseline["runtime_seconds"],
                "runtime_available": bool_string(bool(baseline["runtime_seconds"])),
                "baseline_exit_code": baseline["exit_code"],
                "baseline_trace_status": baseline["trace_status"],
                "exp0_consumed_proxy_bytes": baseline["observed_consumed_bytes"],
                "exp0_consumed_signal": baseline["observed_consumed_signal"],
                "strace_read_return_bytes": (
                    strace["observed_explicit_read_bytes"] if strace else ""
                ),
                "strace_read_calls": (
                    strace["observed_read_calls"] if strace else ""
                ),
                "strace_mmap_calls": (
                    strace["observed_mmap_calls"] if strace else ""
                ),
                "strace_signal_available": bool_string(
                    bool(strace)
                    and strace["audit_signal_eligible"] == "true"
                ),
                "ebpf_read_return_bytes": ebpf["observed_explicit_read_bytes"],
                "ebpf_mmap_page_fault_bytes": ebpf["observed_mmap_bytes"],
                "ebpf_total_consumed_bytes": ebpf["observed_consumed_bytes"],
                "ebpf_signal_available": bool_string(
                    ebpf["audit_signal_eligible"] == "true"
                ),
                "observed_oom_flag": "",
            }
        )
        stats["matched_rows"] += 1
        stats["strace_missing_or_nonunique"] += strace is None
        stats["strace_signal_missing"] += (
            strace is None or strace["audit_signal_eligible"] != "true"
        )
    return output, dict(stats)


def gatk_paths(workflows_root: Path, experiment: str) -> tuple[Path, Path]:
    run_dir = workflows_root / "GATK" / "runs" / f"gatk_500_sc13_local_exp{experiment}"
    metrics = (
        run_dir
        / "results"
        / "metrics"
        / "model_training_haplotypecaller_task_metrics.tsv"
    )
    return metrics, run_dir / "nextflow_trace.tsv"


def gatk_trace_by_instance(path: Path) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    for row in read_table(path):
        match = re.fullmatch(r"HAPLOTYPECALLER \((.+)\)", row.get("name", ""))
        if match:
            output[match.group(1)] = row
    return output


def build_gatk_rows(workflows_root: Path) -> list[dict[str, object]]:
    metric_rows: dict[str, dict[str, dict[str, str]]] = {}
    trace_rows: dict[str, dict[str, dict[str, str]]] = {}
    for experiment in ("0_baseline", "1_strace", "2_ebpf"):
        metrics_path, trace_path = gatk_paths(workflows_root, experiment)
        metrics = read_table(metrics_path)
        metric_rows[experiment] = {row["task_instance"]: row for row in metrics}
        trace_rows[experiment] = gatk_trace_by_instance(trace_path)

    instances = set(metric_rows["0_baseline"])
    if any(set(metric_rows[experiment]) != instances for experiment in metric_rows):
        raise ValueError("GATK Exp 0/1/2 task instances do not match")
    if any(set(trace_rows[experiment]) != instances for experiment in trace_rows):
        raise ValueError("GATK HaplotypeCaller traces do not match metric instances")

    fingerprint = source_fingerprint(
        (
            workflows_root / "GATK" / "main.nf",
            workflows_root / "GATK" / "nextflow.config",
            workflows_root / "GATK" / "bin" / "run_haplotypecaller_task.py",
        )
    )
    output: list[dict[str, object]] = []
    for task_instance in sorted(instances):
        baseline = metric_rows["0_baseline"][task_instance]
        strace = metric_rows["1_strace"][task_instance]
        ebpf = metric_rows["2_ebpf"][task_instance]
        baseline_trace = trace_rows["0_baseline"][task_instance]
        strace_trace = trace_rows["1_strace"][task_instance]
        ebpf_trace = trace_rows["2_ebpf"][task_instance]
        input_identity = stable_id(
            "gatk",
            baseline["dataset_id"],
            baseline["sample_id"],
            baseline["scatter_id"],
            baseline["chrom"],
            baseline["start"],
            baseline["end"],
            length=16,
        )
        logical_id = f"gatk:{input_identity}"
        intermediate = (
            int(baseline["bam_size_bytes"]) + int(baseline["bai_size_bytes"])
        )
        external = (
            int(baseline["reference_size_bytes"])
            + int(baseline["interval_file_size_bytes"])
        )
        static_input = int(baseline["input_bytes_total"])
        output.append(
            {
                "logical_task_id": logical_id,
                "workflow_cohort_id": "gatk:gatk_synthetic_20samples_25scatters:matched_exp012",
                "workflow": "gatk",
                "process": baseline["process"],
                "bucket": baseline["bucket"],
                "task_type": baseline["task_type"],
                "version": GATK_VERSION,
                "workflow_source_fingerprint": fingerprint,
                "dataset_id": baseline["dataset_id"],
                "task_instance": task_instance,
                "sample_id": baseline["sample_id"],
                "unit_id": task_instance,
                "input_identity": input_identity,
                "split_group_id": "",
                "system_config_id": SYSTEM_CONFIG_ID,
                "source_system_config_id": SYSTEM_CONFIG_ID,
                "worker_count": WORKER_COUNT,
                "worker_vcpu": WORKER_VCPU,
                "worker_memory_gib": WORKER_MEMORY_GIB,
                "requested_threads": baseline["requested_threads"],
                "requested_java_heap_mb": baseline["requested_java_heap_mb"],
                "coverage": baseline["coverage"],
                "interval_length_bp": baseline["interval_length_bp"],
                "has_str_region": baseline["has_str_region"],
                "static_input_bytes": str(static_input),
                "static_input_derivation": (
                    "gatk_bam_plus_bai_plus_reference_plus_interval"
                ),
                "staged_original_input_bytes": "0",
                "staged_intermediate_input_bytes": str(intermediate),
                "staged_external_input_bytes": str(external),
                "staged_total_input_bytes": str(static_input),
                "static_manifest_input_bytes": str(static_input),
                "decision_time": ebpf_trace["submit"],
                "completion_time": completion_time(ebpf_trace),
                "baseline_event_time": baseline_trace["submit"],
                "baseline_completion_time": completion_time(baseline_trace),
                "strace_event_time": strace_trace["submit"],
                "strace_completion_time": completion_time(strace_trace),
                "ebpf_event_time": ebpf_trace["submit"],
                "ebpf_completion_time": completion_time(ebpf_trace),
                "baseline_parent_run_id": "gatk_500_sc13_local_exp0_baseline",
                "strace_parent_run_id": "gatk_500_sc13_local_exp1_strace",
                "ebpf_parent_run_id": "gatk_500_sc13_local_exp2_ebpf",
                "baseline_task_id": (
                    f"gatk_500_sc13_local_exp0_baseline:{baseline['task_hash']}"
                ),
                "strace_task_id": (
                    f"gatk_500_sc13_local_exp1_strace:{strace['task_hash']}"
                ),
                "ebpf_task_id": (
                    f"gatk_500_sc13_local_exp2_ebpf:{ebpf['task_hash']}"
                ),
                "baseline_task_hash": baseline_trace["hash"],
                "strace_task_hash": strace_trace["hash"],
                "ebpf_task_hash": ebpf_trace["hash"],
                "peak_memory_bytes": baseline["M_peak_rss_bytes"],
                "runtime_seconds": baseline["runtime_seconds"],
                "runtime_available": "true",
                "baseline_exit_code": baseline["exit_code"],
                "baseline_trace_status": baseline_trace["status"],
                "exp0_consumed_proxy_bytes": str(static_input),
                "exp0_consumed_signal": "static_input_file_size_proxy",
                "strace_read_return_bytes": strace[
                    "strace_read_syscall_bytes"
                ],
                "strace_read_calls": strace["strace_read_syscall_count"],
                "strace_mmap_calls": strace["strace_mmap_syscall_count"],
                "strace_signal_available": bool_string(
                    positive_int(strace["strace_read_syscall_bytes"]) is not None
                ),
                "ebpf_read_return_bytes": ebpf["ebpf_file_read_bytes"],
                "ebpf_mmap_page_fault_bytes": ebpf["ebpf_file_mmap_bytes"],
                "ebpf_total_consumed_bytes": ebpf["ebpf_file_total_bytes"],
                "ebpf_signal_available": ebpf["ebpf_audit_applied"],
                "observed_oom_flag": baseline["oom_suspected"],
            }
        )
    return output


class UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}
        self.rank = {value: 0 for value in values}

    def find(self, value: str) -> str:
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[value] != value:
            parent = self.parent[value]
            self.parent[value] = root
            value = parent
        return root

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def split_components(
    rows: list[dict[str, object]],
) -> dict[str, list[list[dict[str, object]]]]:
    output: dict[str, list[list[dict[str, object]]]] = {}
    by_workflow: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_workflow[str(row["workflow"])].append(row)

    for workflow, workflow_rows in sorted(by_workflow.items()):
        row_ids = [str(row["logical_task_id"]) for row in workflow_rows]
        union_find = UnionFind(row_ids)
        token_owner: dict[tuple[str, str], str] = {}
        token_counts: Counter[tuple[str, str]] = Counter()
        for row in workflow_rows:
            for field in SPLIT_LINK_FIELDS:
                value = str(row.get(field, "")).strip()
                if value:
                    token_counts[(field, value)] += 1
        for row in workflow_rows:
            row_id = str(row["logical_task_id"])
            for field in SPLIT_LINK_FIELDS:
                value = str(row.get(field, "")).strip()
                if not value:
                    continue
                token = (field, value)
                # Broad sample labels and shared references are features, but not
                # unique task families suitable for split isolation.
                if token_counts[token] > MAX_SPLIT_LINK_ROWS:
                    continue
                previous = token_owner.get(token)
                if previous is None:
                    token_owner[token] = row_id
                else:
                    union_find.union(previous, row_id)

        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in workflow_rows:
            grouped[union_find.find(str(row["logical_task_id"]))].append(row)
        components = list(grouped.values())
        components.sort(
            key=lambda component: (
                min(
                    parse_datetime(row["decision_time"]) or datetime.max
                    for row in component
                ),
                min(str(row["logical_task_id"]) for row in component),
            )
        )
        for component in components:
            group_id = f"{workflow}:{stable_id(*(sorted(str(row['logical_task_id']) for row in component)), length=16)}"
            for row in component:
                row["split_group_id"] = group_id
        output[workflow] = components
    return output


def assign_primary_splits(
    components: dict[str, list[list[dict[str, object]]]],
) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for workflow, workflow_components in sorted(components.items()):
        total = sum(len(component) for component in workflow_components)
        consumed = 0
        for component in workflow_components:
            midpoint = (consumed + len(component) / 2.0) / total
            if midpoint <= PRIMARY_FRACTIONS[0]:
                split = "train"
            elif midpoint <= PRIMARY_FRACTIONS[0] + PRIMARY_FRACTIONS[1]:
                split = "calibration"
            else:
                split = "test"
            for row in component:
                assignments[str(row["logical_task_id"])] = split
            consumed += len(component)
    return assignments


def build_primary_manifest(
    rows: list[dict[str, object]], assignments: dict[str, str]
) -> list[dict[str, object]]:
    return [
        {
            "logical_task_id": row["logical_task_id"],
            "workflow": row["workflow"],
            "workflow_cohort_id": row["workflow_cohort_id"],
            "split_group_id": row["split_group_id"],
            "decision_time": row["decision_time"],
            "split": assignments[str(row["logical_task_id"])],
            "split_strategy": (
                "workflow_stratified_chronological_task_family_70_15_15"
            ),
            "parent_overlap_policy": (
                "allowed_and_reported_for_primary_seen_workflow_evaluation"
            ),
        }
        for row in rows
    ]


def build_ood_manifest(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for held_out in ALL_WORKFLOWS:
        calibration = OOD_CALIBRATION_WORKFLOW[held_out]
        fold_id = f"leave_out_{held_out}"
        for row in rows:
            workflow = str(row["workflow"])
            if workflow == held_out:
                role = "test_ood"
            elif workflow == calibration:
                role = "calibration_known"
            else:
                role = "train_known"
            output.append(
                {
                    "fold_id": fold_id,
                    "held_out_workflow": held_out,
                    "calibration_workflow": calibration,
                    "logical_task_id": row["logical_task_id"],
                    "workflow": workflow,
                    "workflow_cohort_id": row["workflow_cohort_id"],
                    "role": role,
                    "split_strategy": (
                        "whole_workflow_train_calibration_test_no_parent_overlap"
                    ),
                }
            )
    return output


def group_keys(row: dict[str, object]) -> tuple[tuple[str, ...], ...]:
    workflow = str(row["workflow"])
    process = str(row["process"])
    version = str(row["version"])
    return (
        (workflow, process, version),
        (workflow, process),
        (process,),
        (workflow,),
        ("global",),
    )


@dataclass
class HistoryValues:
    peaks: list[int]
    consumed: list[int]
    ratios: list[float]


class HistoryState:
    def __init__(self) -> None:
        self.levels: list[dict[tuple[str, ...], HistoryValues]] = [
            defaultdict(lambda: HistoryValues([], [], [])) for _ in range(5)
        ]

    def add(self, row: dict[str, object]) -> None:
        peak = positive_int(row["peak_memory_bytes"])
        consumed = positive_int(row["ebpf_total_consumed_bytes"])
        static_input = positive_int(row["static_input_bytes"])
        if peak is None or consumed is None or static_input is None:
            return
        ratio = consumed / static_input
        for level, key in enumerate(group_keys(row)):
            values = self.levels[level][key]
            bisect.insort(values.peaks, peak)
            bisect.insort(values.consumed, consumed)
            bisect.insort(values.ratios, ratio)

    def values_for(
        self, row: dict[str, object]
    ) -> list[HistoryValues]:
        return [
            self.levels[level][key]
            for level, key in enumerate(group_keys(row))
        ]


def quantile(values: list[int] | list[float], probability: float) -> object:
    if not values:
        return ""
    index = max(0, math.ceil(probability * len(values)) - 1)
    return values[index]


def history_row(
    row: dict[str, object],
    split_or_role: str,
    state: HistoryState,
) -> dict[str, object]:
    histories = state.values_for(row)
    counts = [len(values.peaks) for values in histories]
    selected_level: int | None = next(
        (index for index, count in enumerate(counts) if count >= MIN_HISTORY_COUNT),
        None,
    )
    if selected_level is None and counts[4] > 0:
        selected_level = 4
        level_label = "G4_low_support"
    elif selected_level is None:
        level_label = "cold_start_static"
    else:
        level_label = f"G{selected_level}"
    selected = histories[selected_level] if selected_level is not None else None
    return {
        "logical_task_id": row["logical_task_id"],
        "workflow": row["workflow"],
        "process": row["process"],
        "version": row["version"],
        "decision_time": row["decision_time"],
        "split_or_role": split_or_role,
        "history_g0_count": counts[0],
        "history_g1_count": counts[1],
        "history_g2_count": counts[2],
        "history_g3_count": counts[3],
        "history_g4_count": counts[4],
        "history_level_used": level_label,
        "prior_group_count": 0 if selected is None else len(selected.peaks),
        "prior_peak_median_bytes": (
            "" if selected is None else quantile(selected.peaks, 0.50)
        ),
        "prior_peak_q95_bytes": (
            "" if selected is None else quantile(selected.peaks, 0.95)
        ),
        "prior_consumed_median_bytes": (
            "" if selected is None else quantile(selected.consumed, 0.50)
        ),
        "prior_consumed_q95_bytes": (
            "" if selected is None else quantile(selected.consumed, 0.95)
        ),
        "prior_consumed_to_input_median": (
            "" if selected is None else quantile(selected.ratios, 0.50)
        ),
        "history_rule": (
            "prior_phase_outcomes_plus_strictly_earlier_completed_current_phase"
        ),
    }


def build_history_for_roles(
    rows: list[dict[str, object]],
    role_by_id: dict[str, str],
    role_order: tuple[str, ...],
) -> tuple[list[dict[str, object]], int]:
    by_role: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_role[role_by_id[str(row["logical_task_id"])]].append(row)
    state = HistoryState()
    output: list[dict[str, object]] = []
    temporal_violations = 0
    sequence = 0
    for role in role_order:
        role_rows = sorted(
            by_role[role],
            key=lambda row: (
                parse_datetime(row["decision_time"]) or datetime.max,
                str(row["logical_task_id"]),
            ),
        )
        pending: list[tuple[datetime, int, dict[str, object]]] = []
        for row in role_rows:
            decision = parse_datetime(row["decision_time"])
            if decision is None:
                raise ValueError(f"missing decision_time for {row['logical_task_id']}")
            while pending and pending[0][0] < decision:
                completed_at, _, completed_row = heapq.heappop(pending)
                if completed_at >= decision:
                    temporal_violations += 1
                state.add(completed_row)
            output.append(history_row(row, role, state))
            completed_at = parse_datetime(row["completion_time"])
            if completed_at is None:
                raise ValueError(
                    f"missing completion_time for {row['logical_task_id']}"
                )
            if completed_at < decision:
                temporal_violations += 1
            sequence += 1
            heapq.heappush(pending, (completed_at, sequence, row))
        while pending:
            _, _, completed_row = heapq.heappop(pending)
            state.add(completed_row)
    return output, temporal_violations


def build_primary_history(
    rows: list[dict[str, object]], assignments: dict[str, str]
) -> tuple[list[dict[str, object]], int]:
    return build_history_for_roles(
        rows,
        assignments,
        ("train", "calibration", "test"),
    )


def build_ood_history(
    rows: list[dict[str, object]],
    ood_manifest: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int]:
    by_fold: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in ood_manifest:
        by_fold[str(item["fold_id"])].append(item)
    output: list[dict[str, object]] = []
    violations = 0
    for fold_id, items in sorted(by_fold.items()):
        roles = {
            str(item["logical_task_id"]): str(item["role"]) for item in items
        }
        fold_history, fold_violations = build_history_for_roles(
            rows,
            roles,
            ("train_known", "calibration_known", "test_ood"),
        )
        for item in fold_history:
            output.append({"fold_id": fold_id, **item})
        violations += fold_violations
    return output, violations


def density_label(count: int) -> str:
    if count >= 300:
        return "dense"
    if count >= 100:
        return "medium"
    return "sparse"


def build_group_inventory(
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[int, tuple[str, ...]], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        for level, key in enumerate(group_keys(row)):
            grouped[(level, key)].append(row)
    output: list[dict[str, object]] = []
    for (level, key), group_rows in sorted(
        grouped.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        count = len(group_rows)
        output.append(
            {
                "hierarchy_level": f"G{level}",
                "group_key": " | ".join(key),
                "workflow": (
                    key[0] if level in {0, 1, 3} else ""
                ),
                "process": (
                    key[1] if level in {0, 1} else key[0] if level == 2 else ""
                ),
                "version": key[2] if level == 0 else "",
                "row_count": count,
                "density": density_label(count),
                "workflow_count": len(
                    {str(row["workflow"]) for row in group_rows}
                ),
            }
        )
    return output


def roles_by_workflow(
    manifest: list[dict[str, object]],
    role_field: str,
) -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for row in manifest:
        output[str(row["workflow"])].add(str(row[role_field]))
    return output


def primary_identity_overlap(
    rows: list[dict[str, object]], assignments: dict[str, str]
) -> int:
    tokens: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    token_counts: Counter[tuple[str, str, str]] = Counter()
    for row in rows:
        for field in SPLIT_LINK_FIELDS:
            value = str(row.get(field, "")).strip()
            if value:
                token_counts[(str(row["workflow"]), field, value)] += 1
    for row in rows:
        role = assignments[str(row["logical_task_id"])]
        for field in SPLIT_LINK_FIELDS:
            value = str(row.get(field, "")).strip()
            token = (str(row["workflow"]), field, value)
            if value and token_counts[token] <= MAX_SPLIT_LINK_ROWS:
                tokens[token].add(role)
    return sum(len(roles) > 1 for roles in tokens.values())


def ood_parent_overlap(
    manifest: list[dict[str, object]],
) -> int:
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in manifest:
        grouped[
            (str(row["fold_id"]), str(row["workflow_cohort_id"]))
        ].add(str(row["role"]))
    return sum(len(roles) > 1 for roles in grouped.values())


def validate_and_report(
    rows: list[dict[str, object]],
    base_stats: dict[str, int],
    primary_manifest: list[dict[str, object]],
    ood_manifest: list[dict[str, object]],
    primary_history: list[dict[str, object]],
    ood_history: list[dict[str, object]],
    primary_temporal_violations: int,
    ood_temporal_violations: int,
    report_path: Path,
    metrics_path: Path,
) -> None:
    checks: list[dict[str, object]] = []

    def add_check(name: str, passed: bool, value: object, expected: object) -> None:
        checks.append(
            {
                "check": name,
                "status": "PASS" if passed else "FAIL",
                "value": value,
                "expected": expected,
            }
        )

    workflow_counts = Counter(str(row["workflow"]) for row in rows)
    logical_ids = [str(row["logical_task_id"]) for row in rows]
    static_missing = sum(positive_int(row["static_input_bytes"]) is None for row in rows)
    peak_missing = sum(positive_int(row["peak_memory_bytes"]) is None for row in rows)
    decision_missing = sum(parse_datetime(row["decision_time"]) is None for row in rows)
    completion_missing = sum(
        parse_datetime(row["completion_time"]) is None for row in rows
    )
    exp0_errors = sum(
        int(row["exp0_consumed_proxy_bytes"]) != int(row["static_input_bytes"])
        or row["exp0_consumed_signal"] != "static_input_file_size_proxy"
        for row in rows
    )
    ebpf_errors = sum(
        int(row["ebpf_total_consumed_bytes"])
        != int(row["ebpf_read_return_bytes"])
        + int(row["ebpf_mmap_page_fault_bytes"])
        for row in rows
    )
    strace_formula_errors = sum(
        row["strace_signal_available"] == "true"
        and positive_int(row["strace_read_return_bytes"]) is None
        for row in rows
    )
    prelaunch_forbidden = set(PRELAUNCH_COLUMNS) & {
        "completion_time",
        "peak_memory_bytes",
        "runtime_seconds",
        "strace_read_return_bytes",
        "strace_read_calls",
        "strace_mmap_calls",
        "ebpf_read_return_bytes",
        "ebpf_mmap_page_fault_bytes",
        "ebpf_total_consumed_bytes",
        "observed_oom_flag",
    }
    primary_overlap = primary_identity_overlap(
        rows,
        {
            str(item["logical_task_id"]): str(item["split"])
            for item in primary_manifest
        },
    )
    ood_overlap = ood_parent_overlap(ood_manifest)
    primary_by_workflow: dict[str, Counter[str]] = defaultdict(Counter)
    for item in primary_manifest:
        primary_by_workflow[str(item["workflow"])][str(item["split"])] += 1
    split_fraction_errors = [
        abs(counts[split] / sum(counts.values()) - expected)
        for counts in primary_by_workflow.values()
        for split, expected in zip(
            ("train", "calibration", "test"), PRIMARY_FRACTIONS
        )
    ]
    max_split_fraction_error = max(split_fraction_errors, default=1.0)
    missing_primary_roles = sum(
        set(counts) != {"train", "calibration", "test"}
        for counts in primary_by_workflow.values()
    )
    system_configs = {str(row["system_config_id"]) for row in rows}
    hardware_profiles = {
        (
            str(row["worker_count"]),
            str(row["worker_vcpu"]),
            str(row["worker_memory_gib"]),
        )
        for row in rows
    }
    ebpf_missing = sum(row["ebpf_signal_available"] != "true" for row in rows)
    invalid_baseline_targets = sum(
        str(row["baseline_exit_code"]) != "0"
        or str(row["baseline_trace_status"]) not in {"COMPLETED", "CACHED"}
        for row in rows
    )

    add_check("canonical_row_count", len(rows) == 33516, len(rows), 33516)
    add_check("workflow_count", len(workflow_counts) == 6, len(workflow_counts), 6)
    add_check("gatk_row_count", workflow_counts["gatk"] == 500, workflow_counts["gatk"], 500)
    add_check(
        "logical_task_id_unique",
        len(set(logical_ids)) == len(logical_ids),
        len(set(logical_ids)),
        len(logical_ids),
    )
    add_check("static_input_complete", static_missing == 0, static_missing, 0)
    add_check("peak_memory_complete", peak_missing == 0, peak_missing, 0)
    add_check("decision_time_complete", decision_missing == 0, decision_missing, 0)
    add_check(
        "completion_time_complete", completion_missing == 0, completion_missing, 0
    )
    add_check("exp0_formula", exp0_errors == 0, exp0_errors, 0)
    add_check("ebpf_formula", ebpf_errors == 0, ebpf_errors, 0)
    add_check(
        "strace_signal_formula",
        strace_formula_errors == 0,
        strace_formula_errors,
        0,
    )
    add_check(
        "prelaunch_forbidden_columns",
        not prelaunch_forbidden,
        ",".join(sorted(prelaunch_forbidden)),
        "none",
    )
    add_check(
        "primary_manifest_rows",
        len(primary_manifest) == len(rows),
        len(primary_manifest),
        len(rows),
    )
    add_check(
        "primary_identity_overlap",
        primary_overlap == 0,
        primary_overlap,
        0,
    )
    add_check(
        "primary_all_roles_per_workflow",
        missing_primary_roles == 0,
        missing_primary_roles,
        0,
    )
    add_check(
        "primary_max_split_fraction_error",
        max_split_fraction_error <= 0.01,
        f"{max_split_fraction_error:.6f}",
        "<=0.01",
    )
    add_check(
        "ood_manifest_rows",
        len(ood_manifest) == len(rows) * len(ALL_WORKFLOWS),
        len(ood_manifest),
        len(rows) * len(ALL_WORKFLOWS),
    )
    add_check("ood_parent_overlap", ood_overlap == 0, ood_overlap, 0)
    add_check(
        "primary_history_rows",
        len(primary_history) == len(rows),
        len(primary_history),
        len(rows),
    )
    add_check(
        "ood_history_rows",
        len(ood_history) == len(ood_manifest),
        len(ood_history),
        len(ood_manifest),
    )
    add_check(
        "primary_history_temporal_violations",
        primary_temporal_violations == 0,
        primary_temporal_violations,
        0,
    )
    add_check(
        "ood_history_temporal_violations",
        ood_temporal_violations == 0,
        ood_temporal_violations,
        0,
    )
    add_check(
        "base_pair_count",
        base_stats.get("matched_rows") == 33016,
        base_stats.get("matched_rows"),
        33016,
    )
    add_check(
        "system_config_consistent",
        system_configs == {SYSTEM_CONFIG_ID},
        ",".join(sorted(system_configs)),
        SYSTEM_CONFIG_ID,
    )
    add_check(
        "worker_hardware_consistent",
        hardware_profiles
        == {(str(WORKER_COUNT), str(WORKER_VCPU), str(WORKER_MEMORY_GIB))},
        ";".join("x".join(profile) for profile in sorted(hardware_profiles)),
        f"{WORKER_COUNT}x{WORKER_VCPU}x{WORKER_MEMORY_GIB}",
    )
    add_check("ebpf_signal_complete", ebpf_missing == 0, ebpf_missing, 0)
    add_check(
        "successful_baseline_targets",
        invalid_baseline_targets == 0,
        invalid_baseline_targets,
        0,
    )

    write_table(metrics_path, checks, ("check", "status", "value", "expected"), ",")
    failures = [row for row in checks if row["status"] == "FAIL"]
    primary_counts = Counter(str(row["split"]) for row in primary_manifest)
    ood_fold_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in ood_manifest:
        ood_fold_counts[str(row["fold_id"])][str(row["role"])] += 1
    runtime_missing = sum(row["runtime_available"] != "true" for row in rows)
    strace_missing = sum(row["strace_signal_available"] != "true" for row in rows)
    cached_baselines = sum(
        row["baseline_trace_status"] == "CACHED" for row in rows
    )

    lines = [
        "# Final ML Data Validation Report",
        "",
        "## Result",
        "",
        f"**{'PASS' if not failures else 'FAIL'}**: {len(checks) - len(failures)}/{len(checks)} validation checks passed.",
        "",
        "## Canonical Dataset",
        "",
        f"- Rows: {len(rows):,}",
        f"- Workflows: {len(workflow_counts)}",
        f"- GATK HaplotypeCaller rows: {workflow_counts['gatk']:,}",
        f"- Runtime missing: {runtime_missing:,} rows",
        f"- Strace signal missing: {strace_missing:,} rows",
        f"- Baseline targets sourced from cached successful tasks: {cached_baselines:,} rows",
        "- Static inputs, peak memory, decision time, completion time, and eBPF totals are complete.",
        "",
        "### Rows by workflow",
        "",
        "| Workflow | Rows |",
        "|---|---:|",
    ]
    for workflow, count in sorted(workflow_counts.items()):
        lines.append(f"| {workflow} | {count:,} |")
    lines.extend(
        [
            "",
            "## Primary Split",
            "",
            "| Split | Rows | Percentage |",
            "|---|---:|---:|",
        ]
    )
    for split in ("train", "calibration", "test"):
        count = primary_counts[split]
        lines.append(f"| {split} | {count:,} | {100 * count / len(rows):.2f}% |")
    lines.extend(
        [
            "",
            "The primary split is chronological and keeps bounded task-family identities together. Identifiers occurring in more than 100 rows are treated as broad sample labels or shared assets, not unique task families. Parent-run overlap is intentional because only one matched cohort exists per workflow; this split evaluates seen-workflow online prediction.",
            "",
            "## OOD Folds",
            "",
            "| Fold | Train | Calibration | Held-out test |",
            "|---|---:|---:|---:|",
        ]
    )
    for fold_id, counts in sorted(ood_fold_counts.items()):
        lines.append(
            f"| {fold_id} | {counts['train_known']:,} | {counts['calibration_known']:,} | {counts['test_ood']:,} |"
        )
    lines.extend(
        [
            "",
            "Each OOD fold assigns complete workflows to training, calibration, and testing. Parent-run overlap is zero.",
            "",
            "## Historical Features",
            "",
            "- Previous phases are fully observed before the next phase.",
            "- Within a phase, only tasks with `completion_time < decision_time` are revealed.",
            "- Held-out workflow history starts empty in each OOD fold and may grow only from earlier completed held-out tasks.",
            "- The fallback order is G0, G1, G2, G3, then G4/global.",
            "",
            "## Check Results",
            "",
            "| Check | Status | Value | Expected |",
            "|---|---|---:|---:|",
        ]
    )
    for row in checks:
        lines.append(
            f"| {row['check']} | {row['status']} | {row['value']} | {row['expected']} |"
        )
    lines.extend(
        [
            "",
            "## Known Nonblocking Limitations",
            "",
            "- Taxprofiler's historical Strace raw-log resumed-call backfill is incomplete; its current read-return value is retained with the documented qualification.",
            "- Runtime is missing for the existing Viralmetagenome baseline subset and those rows must be excluded from runtime-weighted wastage metrics.",
            "- The primary split cannot be parent-run isolated because there is one selected matched cohort per workflow. Use the OOD folds for parent-isolated claims.",
            "- No naturally observed failed/OOM attempts are present in this successful-task dataset; OOM evaluation is simulated until attempt-level failure data is added.",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    if failures:
        failed_names = ", ".join(str(row["check"]) for row in failures)
        raise ValueError(f"ML data validation failed: {failed_names}")


def build_ml_ready_data(workflows_root: Path, results_root: Path) -> None:
    workflows_root = workflows_root.resolve()
    results_root = results_root.resolve()
    output_root = results_root / "ml_ready"
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    base_rows, base_stats = build_base_rows(results_root)
    gatk_rows = build_gatk_rows(workflows_root)
    rows = [*base_rows, *gatk_rows]
    rows.sort(
        key=lambda row: (
            str(row["workflow"]),
            parse_datetime(row["decision_time"]) or datetime.max,
            str(row["logical_task_id"]),
        )
    )

    components = split_components(rows)
    assignments = assign_primary_splits(components)
    primary_manifest = build_primary_manifest(rows, assignments)
    ood_manifest = build_ood_manifest(rows)
    primary_history, primary_violations = build_primary_history(rows, assignments)
    ood_history, ood_violations = build_ood_history(rows, ood_manifest)
    group_inventory = build_group_inventory(rows)

    write_table(output_root / "canonical_matched_tasks.tsv", rows, MASTER_COLUMNS)
    write_table(
        output_root / "prelaunch_features.tsv",
        ({column: row.get(column, "") for column in PRELAUNCH_COLUMNS} for row in rows),
        PRELAUNCH_COLUMNS,
    )
    write_table(
        output_root / "postrun_outcomes.tsv",
        ({column: row.get(column, "") for column in OUTCOME_COLUMNS} for row in rows),
        OUTCOME_COLUMNS,
    )
    write_table(
        output_root / "split_manifest_primary.tsv",
        primary_manifest,
        PRIMARY_SPLIT_COLUMNS,
    )
    write_table(
        output_root / "split_manifest_ood.tsv",
        ood_manifest,
        OOD_SPLIT_COLUMNS,
    )
    write_table(
        output_root / "historical_features_primary.tsv",
        primary_history,
        HISTORY_COLUMNS,
    )
    write_table(
        output_root / "historical_features_ood.tsv",
        ood_history,
        OOD_HISTORY_COLUMNS,
    )
    group_columns = (
        "hierarchy_level",
        "group_key",
        "workflow",
        "process",
        "version",
        "row_count",
        "density",
        "workflow_count",
    )
    write_table(
        output_root / "group_inventory.tsv",
        group_inventory,
        group_columns,
    )
    validate_and_report(
        rows,
        base_stats,
        primary_manifest,
        ood_manifest,
        primary_history,
        ood_history,
        primary_violations,
        ood_violations,
        output_root / "data_validation_report.md",
        output_root / "data_validation_checks.csv",
    )
    print(f"ml_ready_root={output_root}")
    print(f"canonical_rows={len(rows)}")
    print(f"gatk_rows={len(gatk_rows)}")
    print(f"primary_history_rows={len(primary_history)}")
    print(f"ood_history_rows={len(ood_history)}")


def main() -> None:
    args = parse_args()
    build_ml_ready_data(args.workflows_root, args.results_root)


if __name__ == "__main__":
    main()
