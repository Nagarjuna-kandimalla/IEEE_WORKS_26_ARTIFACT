#!/usr/bin/env python3
"""Merge Nextflow task metric key/value TSVs into a reproducible table."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


LEGACY_FIELD_RENAMES = {
    "instrumentation": "instrumentation_mode",
    "c_bytes": "observed_c_bytes",
    "audit_flag": "ebpf_audit_plan_flag",
    "audit_requested": "ebpf_audit_requested",
    "audit_applied": "ebpf_audit_applied",
    "ebpf_read_bytes": "ebpf_file_read_bytes",
    "ebpf_mmap_bytes": "ebpf_file_mmap_bytes",
    "ebpf_total_bytes": "ebpf_file_total_bytes",
    "ebpf_status": "ebpf_audit_status",
    "ebpf_tsv_path": "ebpf_attribution_tsv_path",
    "ebpf_error": "ebpf_audit_error",
    "strace_mmap_calls": "strace_mmap_syscall_count",
    "strace_read_calls": "strace_read_syscall_count",
    "strace_read_bytes": "strace_read_syscall_bytes",
    "strace_write_calls": "strace_write_syscall_count",
}

PREFERRED_COLUMN_ORDER = [
    "workflow",
    "dataset_id",
    "process",
    "bucket",
    "task_type",
    "task_instance",
    "task_hash",
    "sample_id",
    "coverage",
    "seed",
    "scatter_id",
    "chrom",
    "start",
    "end",
    "interval_length_bp",
    "contig_length_bp",
    "has_str_region",
    "instrumentation_mode",
    "observed_c_source",
    "observed_c_bytes",
    "a_bytes",
    "input_bytes_total",
    "M_peak_rss_bytes",
    "M_cgroup_peak_bytes",
    "peak_rss_kb",
    "runtime_seconds",
    "exit_code",
    "oom_suspected",
    "strace_read_syscall_bytes",
    "strace_read_syscall_count",
    "strace_mmap_syscall_count",
    "strace_write_syscall_count",
    "ebpf_audit_plan_flag",
    "ebpf_audit_requested",
    "ebpf_audit_applied",
    "ebpf_audit_status",
    "ebpf_audit_error",
    "ebpf_file_read_bytes",
    "ebpf_file_mmap_bytes",
    "ebpf_file_total_bytes",
    "ebpf_attribution_tsv_path",
]


def truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "audit"}


def positive_int(value: str) -> bool:
    try:
        return int(value.strip() or "0") > 0
    except ValueError:
        return False


def normalize_record(record: dict[str, str]) -> dict[str, str]:
    """Convert legacy metric names to purpose-named output columns."""
    normalized = dict(record)
    for old, new in LEGACY_FIELD_RENAMES.items():
        if new not in normalized and old in normalized:
            normalized[new] = normalized[old]
        normalized.pop(old, None)

    instrumentation = normalized.get("instrumentation_mode", "")
    if "observed_c_source" not in normalized:
        if instrumentation == "strace" and positive_int(normalized.get("strace_read_syscall_bytes", "")):
            normalized["observed_c_source"] = "strace_read_syscalls"
        elif truthy(normalized.get("ebpf_audit_applied", "")):
            normalized["observed_c_source"] = "ebpf_file_access"
        else:
            normalized["observed_c_source"] = "not_observed"

    normalized.setdefault("observed_c_bytes", "")
    normalized.setdefault("ebpf_audit_plan_flag", "NoAudit")
    normalized.setdefault("ebpf_audit_requested", "false")
    normalized.setdefault("ebpf_audit_applied", "false")
    normalized.setdefault("ebpf_audit_status", "not_requested")
    normalized.setdefault("ebpf_audit_error", "")
    normalized.setdefault("ebpf_file_read_bytes", "0")
    normalized.setdefault("ebpf_file_mmap_bytes", "0")
    normalized.setdefault("ebpf_file_total_bytes", "0")
    normalized.setdefault("ebpf_attribution_tsv_path", "")
    normalized.setdefault("strace_read_syscall_bytes", "0")
    normalized.setdefault("strace_read_syscall_count", "0")
    normalized.setdefault("strace_mmap_syscall_count", "0")
    normalized.setdefault("strace_write_syscall_count", "0")
    return normalized


def ordered_columns(records: list[dict[str, str]]) -> list[str]:
    discovered = sorted({column for record in records for column in record})
    preferred = [column for column in PREFERRED_COLUMN_ORDER if column in discovered]
    return preferred + [column for column in discovered if column not in preferred]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--out-tsv", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-training", required=True)
    args = parser.parse_args()

    task_root = Path(args.task_root)
    # Nextflow stages collected task directories as symlinks. Path.rglob does
    # not descend into directory symlinks, so inspect each staged task directly.
    metric_files = sorted(
        child / "task_metrics.tsv"
        for child in task_root.iterdir()
        if child.is_dir() and (child / "task_metrics.tsv").is_file()
    )

    records: list[dict[str, str]] = []
    for path in metric_files:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        records.append(normalize_record({row["field"]: row["value"] for row in rows}))

    columns = ordered_columns(records)
    for output, delimiter in ((Path(args.out_tsv), "\t"), (Path(args.out_csv), ","), (Path(args.out_training), "\t")):
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, delimiter=delimiter, lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)


if __name__ == "__main__":
    main()
