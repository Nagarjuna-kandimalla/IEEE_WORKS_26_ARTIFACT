#!/usr/bin/env python3
"""Normalize a Nextflow trace into baseline rows for overhead comparisons."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
STATIC_FIELDS = [
    "source_manifest", "unit_id", "window_type", "expected_peak_gb", "input_bytes",
    "planned_chunks", "feature_availability",
]
STRACE_FIELDS = [
    "strace_log_prefix", "strace_file_count", "strace_mmap_calls", "strace_read_calls",
    "strace_read_bytes", "strace_write_calls", "strace_write_bytes",
]


def parse_size(value: str) -> int | str:
    match = re.fullmatch(r"\s*([0-9.]+)\s*([KMGT]?B)\s*", value or "", flags=re.I)
    if not match:
        return ""
    return round(float(match.group(1)) * UNITS[match.group(2).upper()])


def parse_duration(value: str) -> float | str:
    total = 0.0
    matches = re.findall(r"([0-9.]+)\s*(ms|s|m|h|d)", value or "", flags=re.I)
    if not matches:
        return ""
    factors = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
    for amount, unit in matches:
        total += float(amount) * factors[unit.lower()]
    return round(total, 6)


def split_name(value: str) -> tuple[str, str]:
    if not value:
        return "", ""
    match = re.fullmatch(r"(.+?)\s+\((.*)\)", value)
    if match:
        return match.group(1), match.group(2)
    return value, ""


def index_by_task_hash(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        full_hash = row["task_hash"].replace("/", "")
        for key in (full_hash, full_hash[:8]):
            previous = index.get(key)
            if previous and previous["task_hash"] != row["task_hash"]:
                raise SystemExit(f"Ambiguous task hash prefix in instrumentation summary: {key}")
            index[key] = row
    return index


def process_alias(value: str) -> str:
    return value.rsplit(":", 1)[-1]


def task_id_key(row: dict[str, str]) -> tuple[int, str]:
    raw = row.get("task_id", "")
    try:
        return int(raw), raw
    except ValueError:
        return 2**63 - 1, raw


def match_static_rows(
    workflow: str,
    trace_rows: list[dict[str, str]],
    static_rows: list[dict[str, str]],
) -> dict[int, dict[str, str]]:
    candidates_by_process: dict[str, list[dict[str, str]]] = {}
    for static in static_rows:
        if static.get("workflow") != workflow:
            continue
        candidates_by_process.setdefault(process_alias(static["process"]), []).append(static)

    assignments: dict[int, dict[str, str]] = {}
    used_static: set[int] = set()
    unresolved_by_process: dict[str, list[dict[str, str]]] = {}

    for trace in trace_rows:
        if trace.get("status") not in {"COMPLETED", "CACHED"}:
            continue
        process = trace.get("process") or split_name(trace.get("name", ""))[0]
        task_instance = trace.get("tag") or split_name(trace.get("name", ""))[1]
        alias = process_alias(process)
        candidates = candidates_by_process.get(alias, [])

        direct = [
            row for row in candidates
            if id(row) not in used_static and row.get("task_instance") == task_instance
        ]
        if len(direct) != 1:
            direct = [
                row for row in candidates
                if id(row) not in used_static
                and row.get("unit_id")
                and row["unit_id"] in task_instance
            ]
        if len(direct) == 1:
            assignments[id(trace)] = direct[0]
            used_static.add(id(direct[0]))
        else:
            unresolved_by_process.setdefault(alias, []).append(trace)

    # Some nf-core processes omit interval identity from task tags. When the
    # remaining manifest and trace counts agree exactly, task_id preserves the
    # channel order and provides a deterministic one-to-one assignment.
    for alias, unresolved in unresolved_by_process.items():
        remaining = [
            row for row in candidates_by_process.get(alias, [])
            if id(row) not in used_static
        ]
        if len(remaining) != len(unresolved):
            continue
        for trace, static in zip(sorted(unresolved, key=task_id_key), remaining):
            assignments[id(trace)] = static
            used_static.add(id(static))

    return assignments


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--static-manifest", required=True)
    parser.add_argument("--instrumentation", choices=["none", "strace", "ebpf"], default="none")
    parser.add_argument("--ebpf-merged", default="")
    parser.add_argument("--strace-summary", default="")
    args = parser.parse_args()

    with Path(args.trace).open(newline="", encoding="utf-8") as handle:
        trace_rows = list(csv.DictReader(handle, delimiter="\t"))

    columns = [
        "workflow", "process", "bucket", "task_type", "task_hash", "task_instance",
        "dataset_id", "static_manifest_path", *STATIC_FIELDS, "static_match", "instrumentation",
        "audit_flag", "audit_requested", "audit_applied", "a_bytes", "c_bytes",
        "M_peak_rss_bytes", "runtime_seconds", "exit_code", "trace_status",
        *STRACE_FIELDS, "ebpf_read_bytes", "ebpf_mmap_bytes", "ebpf_total_bytes", "ebpf_tsv_path",
        "trace_read_bytes", "trace_rchar", "trace_peak_rss", "trace_workdir",
    ]
    ebpf_by_hash: dict[str, dict[str, str]] = {}
    if args.ebpf_merged:
        with Path(args.ebpf_merged).open(newline="", encoding="utf-8") as handle:
            ebpf_by_hash = index_by_task_hash(
                list(csv.DictReader(handle, delimiter="\t"))
            )
    strace_by_hash: dict[str, dict[str, str]] = {}
    if args.strace_summary:
        with Path(args.strace_summary).open(newline="", encoding="utf-8") as handle:
            strace_by_hash = index_by_task_hash(
                list(csv.DictReader(handle, delimiter="\t"))
            )
    with Path(args.static_manifest).open(newline="", encoding="utf-8") as handle:
        static_rows = list(csv.DictReader(handle, delimiter="\t"))
    static_by_trace = match_static_rows(args.workflow, trace_rows, static_rows)
    rows: list[dict[str, str | int | float]] = []
    for trace in trace_rows:
        if trace.get("status") not in {"COMPLETED", "CACHED"}:
            continue
        peak = parse_size(trace.get("peak_rss", ""))
        read_bytes = parse_size(trace.get("read_bytes", ""))
        rchar = parse_size(trace.get("rchar", ""))
        if read_bytes == "" and rchar != "":
            read_bytes = rchar
        task_hash = trace.get("hash", "")
        process, task_instance = split_name(trace.get("name", ""))
        process = trace.get("process") or process
        task_instance = trace.get("tag") or task_instance
        ebpf = ebpf_by_hash.get(task_hash.replace("/", ""), {})
        strace = strace_by_hash.get(task_hash.replace("/", ""), {})
        static = static_by_trace.get(id(trace), {})
        audited = args.instrumentation == "ebpf" and bool(ebpf)
        if args.instrumentation == "ebpf":
            c_bytes: str | int = ebpf.get("ebpf_total_bytes", "")
        elif args.instrumentation == "strace":
            c_bytes = strace.get("strace_read_bytes", "")
        else:
            c_bytes = ""
        rows.append({
            "workflow": args.workflow,
            "process": process,
            "bucket": f"{args.workflow}::{process}",
            "task_type": process,
            "task_hash": task_hash,
            "task_instance": task_instance,
            "dataset_id": args.dataset_id,
            "static_manifest_path": args.static_manifest,
            **{field: static.get(field, "") for field in STATIC_FIELDS},
            "static_match": str(bool(static)).lower(),
            "instrumentation": args.instrumentation,
            "audit_flag": "Audit" if audited else "NoAudit",
            "audit_requested": str(audited).lower(),
            "audit_applied": str(audited).lower(),
            "a_bytes": read_bytes,
            "c_bytes": c_bytes,
            "M_peak_rss_bytes": peak,
            "runtime_seconds": parse_duration(trace.get("realtime", "")),
            "exit_code": trace.get("exit", ""),
            "trace_status": trace.get("status", ""),
            **{field: strace.get(field, "") for field in STRACE_FIELDS},
            "ebpf_read_bytes": ebpf.get("ebpf_read_bytes", "0"),
            "ebpf_mmap_bytes": ebpf.get("ebpf_mmap_bytes", "0"),
            "ebpf_total_bytes": ebpf.get("ebpf_total_bytes", "0"),
            "ebpf_tsv_path": ebpf.get("ebpf_tsv", ""),
            "trace_read_bytes": read_bytes,
            "trace_rchar": rchar,
            "trace_peak_rss": trace.get("peak_rss", ""),
            "trace_workdir": trace.get("workdir", ""),
        })
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
