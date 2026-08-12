#!/usr/bin/env python3
"""Build the paper-aligned, ML-ready workflow results package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median


csv.field_size_limit(sys.maxsize)
GIB = 1024**3


@dataclass(frozen=True)
class RunSpec:
    workflow: str
    version: str
    experiment_id: int
    experiment_name: str
    instrumentation: str
    run_label: str
    system_config_id: str
    overhead_evidence_grade: str
    evidence_note: str


RUNS = (
    RunSpec(
        "minimap2",
        "custom",
        0,
        "baseline",
        "none",
        "minimap2_1500_sc13a_exp0_baseline",
        "sc13a",
        "controlled",
        "Matched 13-worker baseline for the standardized Minimap2 cohort.",
    ),
    RunSpec(
        "minimap2",
        "custom",
        1,
        "strace_full_online",
        "strace",
        "minimap2_1500_sc13a_exp1_strace",
        "sc13a",
        "controlled",
        "Online full Strace audit on the matched Minimap2 cohort.",
    ),
    RunSpec(
        "minimap2",
        "custom",
        2,
        "ebpf_full_online",
        "ebpf",
        "minimap2_1500_sc13a_exp2_ebpf",
        "sc13a",
        "controlled",
        "Online full eBPF audit on the matched Minimap2 cohort.",
    ),
    RunSpec(
        "sarek",
        "3.6.0",
        0,
        "baseline",
        "none",
        "sarek_6003_sc13a_exp0_baseline",
        "sc13a",
        "feature_only",
        "Most rows are cached; do not use workflow wall time as overhead evidence.",
    ),
    RunSpec(
        "sarek",
        "3.6.0",
        1,
        "strace_full_online",
        "strace",
        "sarek_6003_sc13a_exp1_strace",
        "sc13a",
        "feature_only",
        "Some cached tasks lack Strace records; consumed-data rows remain usable.",
    ),
    RunSpec(
        "sarek",
        "3.6.0",
        2,
        "ebpf_full_online",
        "ebpf",
        "sarek_6003_sc13a_exp2_ebpf",
        "sc13a",
        "feature_only",
        "Resume/cache history invalidates a simple workflow-wall overhead ratio.",
    ),
    RunSpec(
        "seqinspector",
        "1.1.0",
        0,
        "baseline",
        "none",
        "seqinspector_3000_sc13b_local_exp0_baseline",
        "sc13b",
        "controlled",
        "Local-input baseline selected to match the local Strace and eBPF runs.",
    ),
    RunSpec(
        "seqinspector",
        "1.1.0",
        1,
        "strace_full_online",
        "strace",
        "seqinspector_3000_sc13b_local_exp1_strace",
        "sc13b",
        "controlled",
        "Online full Strace audit using the same local input cohort.",
    ),
    RunSpec(
        "seqinspector",
        "1.1.0",
        2,
        "ebpf_full_online",
        "ebpf",
        "seqinspector_3000_sc13b_local_exp2_ebpf",
        "sc13b",
        "controlled",
        "Online full eBPF audit using the same local input cohort.",
    ),
    RunSpec(
        "taxprofiler",
        "2.0.1",
        0,
        "baseline",
        "none",
        "taxprofiler_6000_sc13b_local_exp0_baseline",
        "sc13b",
        "controlled",
        "Matched 13-worker local-input baseline.",
    ),
    RunSpec(
        "taxprofiler",
        "2.0.1",
        1,
        "strace_full_online",
        "strace",
        "taxprofiler_6000_sc13b_local_exp1_strace",
        "sc13b",
        "controlled",
        "Online full Strace audit with corrected syscall parsing.",
    ),
    RunSpec(
        "taxprofiler",
        "2.0.1",
        2,
        "ebpf_full_online",
        "ebpf",
        "taxprofiler_6000_sc13b_local_exp2_ebpf",
        "sc13b",
        "controlled",
        "Online full eBPF audit on the matched local-input cohort.",
    ),
    RunSpec(
        "viralmetagenome",
        "1.1.3",
        0,
        "baseline",
        "none",
        "viralmetagenome_3000_sc13a_local_exp0_baseline",
        "sc13a",
        "directional",
        "Complete local-input run; overlapping worker-lane activity may affect wall time.",
    ),
    RunSpec(
        "viralmetagenome",
        "1.1.3",
        1,
        "strace_full_online",
        "strace",
        "viralmetagenome_3000_sc13b_local_exp1_strace",
        "sc13b",
        "directional",
        "Complete local-input run with three successful retries; timing is directional.",
    ),
    RunSpec(
        "viralmetagenome",
        "1.1.3",
        2,
        "ebpf_full_online",
        "ebpf",
        "viralmetagenome_3000_sc13a_local_exp2_ebpf",
        "sc13a",
        "directional",
        "Complete local-input run; lane reuse and cache warmth may affect wall time.",
    ),
)


TASK_COLUMNS = (
    "task_id",
    "event_time",
    "completion_time",
    "parent_run_id",
    "workflow",
    "process",
    "bucket",
    "task_type",
    "task_instance",
    "unit_id",
    "input_identity",
    "dataset_id",
    "version",
    "workflow_source_fingerprint",
    "experiment_id",
    "experiment_name",
    "instrumentation",
    "system_config_id",
    "worker_count",
    "worker_vcpu",
    "worker_memory_gib",
    "task_hash",
    "trace_status",
    "exit_code",
    "static_input_bytes",
    "static_input_derivation",
    "staged_original_input_bytes",
    "staged_intermediate_input_bytes",
    "staged_external_input_bytes",
    "staged_total_input_bytes",
    "static_manifest_input_bytes",
    "static_match",
    "feature_availability",
    "observed_consumed_bytes",
    "observed_consumed_signal",
    "observed_explicit_read_bytes",
    "observed_mmap_bytes",
    "observed_mmap_calls",
    "observed_read_calls",
    "trace_read_bytes_post_run",
    "trace_rchar_bytes_post_run",
    "peak_memory_bytes",
    "runtime_seconds",
    "audit_requested",
    "audit_applied",
    "ml_eligible",
    "audit_signal_eligible",
    "overhead_evidence_grade",
    "source_metrics_path",
    "trace_workdir",
)

STRACE_SUMMARY_FIELDS = (
    "strace_log_prefix",
    "strace_file_count",
    "strace_mmap_calls",
    "strace_read_calls",
    "strace_read_bytes",
    "strace_write_calls",
    "strace_write_bytes",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workflows-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "workflows_run_results",
    )
    parser.add_argument(
        "--skip-ml-ready",
        action="store_true",
        help="build RQ1 run/figure summaries without the downstream ML dataset",
    )
    return parser.parse_args()


def number(value: object) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def positive(value: object) -> float | None:
    parsed = number(value)
    return parsed if parsed is not None and parsed > 0 else None


def integer_string(value: float | None) -> str:
    return "" if value is None else str(int(round(value)))


def logical_input_identity(source: dict[str, str]) -> str:
    names = sorted(
        name
        for name in source.get("staged_input_local_names", "").split("|")
        if name
    )
    if not names:
        return ""
    return hashlib.sha256("|".join(names).encode()).hexdigest()[:16]


def bool_string(value: bool) -> str:
    return "true" if value else "false"


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_table(
    path: Path,
    rows: list[dict[str, object]],
    columns: tuple[str, ...] | list[str],
    delimiter: str,
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


def source_fingerprint(source_dir: Path) -> str:
    digest = hashlib.sha256()
    matched = False
    for name in ("main.nf", "nextflow.config", "nextflow_schema.json"):
        path = source_dir / name
        if path.is_file():
            matched = True
            digest.update(name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16] if matched else "unavailable"


def trace_index(path: Path) -> tuple[dict[str, dict[str, str]], float | None]:
    rows = read_tsv(path)
    by_hash: dict[str, dict[str, str]] = {}
    starts: list[datetime] = []
    ends: list[datetime] = []
    for row in rows:
        task_hash = row.get("hash", "")
        if task_hash:
            by_hash[task_hash] = row
        submit = parse_datetime(row.get("submit", ""))
        if submit is None:
            continue
        starts.append(submit)
        complete = parse_datetime(row.get("complete", ""))
        if complete is not None:
            ends.append(complete)
        else:
            duration = parse_duration_seconds(row.get("duration", ""))
            if duration is not None:
                ends.append(datetime.fromtimestamp(submit.timestamp() + duration))
    wall_seconds = None
    if starts and ends:
        wall_seconds = (max(ends) - min(starts)).total_seconds()
    return by_hash, wall_seconds


def parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


_DURATION_TOKEN = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(ms|d|h|m|s)")
_DURATION_SCALE = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def parse_duration_seconds(value: str) -> float | None:
    tokens = _DURATION_TOKEN.findall(value.strip())
    if not tokens:
        return None
    return sum(float(amount) * _DURATION_SCALE[unit] for amount, unit in tokens)


def trace_completion_time(trace: dict[str, str]) -> str:
    complete = parse_datetime(trace.get("complete", ""))
    if complete is not None:
        return complete.isoformat(sep=" ")
    submit = parse_datetime(trace.get("submit", ""))
    duration = parse_duration_seconds(trace.get("duration", ""))
    if submit is None or duration is None:
        return ""
    return (submit + timedelta(seconds=duration)).isoformat(sep=" ")


def trace_row_for(
    row: dict[str, str], by_hash: dict[str, dict[str, str]]
) -> dict[str, str]:
    short_hash = row.get("task_hash", "")
    if short_hash in by_hash:
        return by_hash[short_hash]
    workdir = row.get("trace_workdir", "")
    if workdir:
        parts = Path(workdir).parts
        if len(parts) >= 2:
            candidate = f"{parts[-2]}/{parts[-1][:6]}"
            if candidate in by_hash:
                return by_hash[candidate]
    return {}


def apply_strace_summary(
    source_rows: list[dict[str, str]], summary_path: Path
) -> None:
    summary_by_full_hash = {
        row["task_hash"].replace("/", ""): row for row in read_tsv(summary_path)
    }
    for row in source_rows:
        workdir = row.get("trace_workdir", "")
        full_hash = Path(workdir).name if workdir else ""
        summary = summary_by_full_hash.get(full_hash)
        if summary is None:
            short_hash = row.get("task_hash", "").replace("/", "")
            matches = [
                item
                for item_hash, item in summary_by_full_hash.items()
                if item_hash.startswith(short_hash)
            ]
            summary = matches[0] if len(matches) == 1 else None
        if summary is None:
            continue
        for field in STRACE_SUMMARY_FIELDS:
            row[field] = summary.get(field, "")


def build_task_row(
    source: dict[str, str],
    trace: dict[str, str],
    spec: RunSpec,
    metrics_path: Path,
    fingerprint: str,
) -> dict[str, object]:
    staged_total = positive(source.get("staged_total_input_bytes"))
    manifest_input = positive(source.get("input_bytes"))
    if staged_total is not None:
        static_input = staged_total
        derivation = "staged_original_plus_intermediate_plus_external"
    elif manifest_input is not None:
        static_input = manifest_input
        derivation = "static_manifest_input_bytes"
    else:
        static_input = None
        derivation = "missing"

    if spec.instrumentation == "strace":
        consumed = positive(source.get("strace_read_bytes"))
        consumed_signal = "strace_read_return_bytes"
        explicit_read = consumed
        mmap_bytes = None
        mmap_calls = positive(source.get("strace_mmap_calls"))
        read_calls = positive(source.get("strace_read_calls"))
    elif spec.instrumentation == "ebpf":
        consumed = positive(source.get("ebpf_total_bytes"))
        consumed_signal = "ebpf_read_plus_filemap_fault_bytes"
        explicit_read = positive(source.get("ebpf_read_bytes"))
        mmap_bytes = positive(source.get("ebpf_mmap_bytes"))
        mmap_calls = None
        read_calls = None
    else:
        consumed = static_input
        consumed_signal = "static_input_file_size_proxy"
        explicit_read = None
        mmap_bytes = None
        mmap_calls = None
        read_calls = None

    peak_memory = positive(source.get("M_peak_rss_bytes"))
    runtime = positive(source.get("runtime_seconds"))
    exit_code = number(source.get("exit_code"))
    ml_eligible = static_input is not None and peak_memory is not None and exit_code == 0
    audit_signal_eligible = (
        spec.instrumentation in {"strace", "ebpf"}
        and consumed is not None
        and exit_code == 0
    )

    workdir = source.get("trace_workdir", "")
    full_hash = Path(workdir).name if workdir else source.get("task_hash", "").replace("/", "")
    task_id = f"{spec.run_label}:{full_hash}"
    return {
        "task_id": task_id,
        "event_time": trace.get("submit", ""),
        "completion_time": trace_completion_time(trace),
        "parent_run_id": spec.run_label,
        "workflow": spec.workflow,
        "process": source.get("process", ""),
        "bucket": source.get("bucket", ""),
        "task_type": source.get("task_type", ""),
        "task_instance": source.get("task_instance", ""),
        "unit_id": source.get("unit_id", ""),
        "input_identity": logical_input_identity(source),
        "dataset_id": source.get("dataset_id", ""),
        "version": spec.version,
        "workflow_source_fingerprint": fingerprint,
        "experiment_id": spec.experiment_id,
        "experiment_name": spec.experiment_name,
        "instrumentation": spec.instrumentation,
        "system_config_id": spec.system_config_id,
        "worker_count": 13,
        "worker_vcpu": 32,
        "worker_memory_gib": 256,
        "task_hash": source.get("task_hash", ""),
        "trace_status": source.get("trace_status", ""),
        "exit_code": integer_string(exit_code),
        "static_input_bytes": integer_string(static_input),
        "static_input_derivation": derivation,
        "staged_original_input_bytes": integer_string(
            positive(source.get("staged_original_input_bytes"))
        ),
        "staged_intermediate_input_bytes": integer_string(
            positive(source.get("staged_intermediate_input_bytes"))
        ),
        "staged_external_input_bytes": integer_string(
            positive(source.get("staged_external_input_bytes"))
        ),
        "staged_total_input_bytes": integer_string(staged_total),
        "static_manifest_input_bytes": integer_string(manifest_input),
        "static_match": source.get("static_match", ""),
        "feature_availability": source.get("feature_availability", ""),
        "observed_consumed_bytes": integer_string(consumed),
        "observed_consumed_signal": consumed_signal,
        "observed_explicit_read_bytes": integer_string(explicit_read),
        "observed_mmap_bytes": integer_string(mmap_bytes),
        "observed_mmap_calls": integer_string(mmap_calls),
        "observed_read_calls": integer_string(read_calls),
        "trace_read_bytes_post_run": integer_string(
            positive(source.get("trace_read_bytes"))
        ),
        "trace_rchar_bytes_post_run": integer_string(
            positive(source.get("trace_rchar"))
        ),
        "peak_memory_bytes": integer_string(peak_memory),
        "runtime_seconds": "" if runtime is None else f"{runtime:.6f}".rstrip("0").rstrip("."),
        "audit_requested": source.get("audit_requested", ""),
        "audit_applied": source.get("audit_applied", ""),
        "ml_eligible": bool_string(ml_eligible),
        "audit_signal_eligible": bool_string(audit_signal_eligible),
        "overhead_evidence_grade": spec.overhead_evidence_grade,
        "source_metrics_path": str(metrics_path),
        "trace_workdir": workdir,
    }


def pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 6) if denominator else 0.0


def sum_field(rows: list[dict[str, object]], field: str) -> float:
    return sum(number(row.get(field)) or 0.0 for row in rows)


def count_positive(rows: list[dict[str, object]], field: str) -> int:
    return sum(positive(row.get(field)) is not None for row in rows)


def median_field(rows: list[dict[str, object]], field: str) -> float | str:
    values = [value for row in rows if (value := positive(row.get(field))) is not None]
    return round(median(values), 6) if values else ""


def summarize_run(
    spec: RunSpec,
    rows: list[dict[str, object]],
    wall_seconds: float | None,
    metrics_path: Path,
) -> dict[str, object]:
    total = len(rows)
    read_bytes = sum_field(rows, "observed_explicit_read_bytes")
    mmap_bytes = sum_field(rows, "observed_mmap_bytes")
    consumed_bytes = sum_field(rows, "observed_consumed_bytes")
    trace_join_rows = sum(bool(row["event_time"]) for row in rows)
    return {
        "workflow": spec.workflow,
        "version": spec.version,
        "experiment_id": spec.experiment_id,
        "experiment_name": spec.experiment_name,
        "instrumentation": spec.instrumentation,
        "run_label": spec.run_label,
        "system_config_id": spec.system_config_id,
        "worker_count": 13,
        "worker_vcpu": 32,
        "worker_memory_gib": 256,
        "task_rows": total,
        "process_count": len({str(row["process"]) for row in rows}),
        "bucket_count": len({str(row["bucket"]) for row in rows}),
        "completed_rows": sum(row["trace_status"] == "COMPLETED" for row in rows),
        "cached_rows": sum(row["trace_status"] == "CACHED" for row in rows),
        "trace_join_rows": trace_join_rows,
        "trace_join_coverage_pct": pct(trace_join_rows, total),
        "static_input_rows": count_positive(rows, "static_input_bytes"),
        "static_input_coverage_pct": pct(count_positive(rows, "static_input_bytes"), total),
        "peak_memory_rows": count_positive(rows, "peak_memory_bytes"),
        "peak_memory_coverage_pct": pct(count_positive(rows, "peak_memory_bytes"), total),
        "runtime_rows": count_positive(rows, "runtime_seconds"),
        "runtime_coverage_pct": pct(count_positive(rows, "runtime_seconds"), total),
        "consumed_signal_rows": count_positive(rows, "observed_consumed_bytes"),
        "consumed_signal_coverage_pct": pct(
            count_positive(rows, "observed_consumed_bytes"), total
        ),
        "ml_eligible_rows": sum(row["ml_eligible"] == "true" for row in rows),
        "audit_signal_eligible_rows": sum(
            row["audit_signal_eligible"] == "true" for row in rows
        ),
        "workflow_wall_seconds": "" if wall_seconds is None else round(wall_seconds, 6),
        "workflow_wall_minutes": ""
        if wall_seconds is None
        else round(wall_seconds / 60.0, 6),
        "task_runtime_seconds_sum": round(sum_field(rows, "runtime_seconds"), 6),
        "task_runtime_seconds_median": median_field(rows, "runtime_seconds"),
        "peak_memory_bytes_median": median_field(rows, "peak_memory_bytes"),
        "explicit_read_gib": round(read_bytes / GIB, 6),
        "mmap_page_fault_gib": round(mmap_bytes / GIB, 6),
        "consumed_total_gib": round(consumed_bytes / GIB, 6),
        "mmap_byte_share_pct": round(
            100 * mmap_bytes / (read_bytes + mmap_bytes), 6
        )
        if read_bytes + mmap_bytes
        else "",
        "strace_mmap_calls": int(sum_field(rows, "observed_mmap_calls")),
        "strace_read_calls": int(sum_field(rows, "observed_read_calls")),
        "overhead_evidence_grade": spec.overhead_evidence_grade,
        "evidence_note": spec.evidence_note,
        "source_metrics_path": str(metrics_path),
    }


def summarize_buckets(all_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, int, str], list[dict[str, object]]] = defaultdict(list)
    for row in all_rows:
        grouped[(str(row["workflow"]), int(row["experiment_id"]), str(row["bucket"]))].append(
            row
        )
    output: list[dict[str, object]] = []
    for (workflow, experiment_id, bucket), rows in sorted(grouped.items()):
        read_bytes = sum_field(rows, "observed_explicit_read_bytes")
        mmap_bytes = sum_field(rows, "observed_mmap_bytes")
        output.append(
            {
                "workflow": workflow,
                "experiment_id": experiment_id,
                "experiment_name": rows[0]["experiment_name"],
                "instrumentation": rows[0]["instrumentation"],
                "bucket": bucket,
                "process": rows[0]["process"],
                "task_rows": len(rows),
                "static_input_rows": count_positive(rows, "static_input_bytes"),
                "peak_memory_rows": count_positive(rows, "peak_memory_bytes"),
                "runtime_rows": count_positive(rows, "runtime_seconds"),
                "consumed_signal_rows": count_positive(rows, "observed_consumed_bytes"),
                "ml_eligible_rows": sum(row["ml_eligible"] == "true" for row in rows),
                "static_input_bytes_median": median_field(rows, "static_input_bytes"),
                "peak_memory_bytes_median": median_field(rows, "peak_memory_bytes"),
                "runtime_seconds_median": median_field(rows, "runtime_seconds"),
                "explicit_read_gib": round(read_bytes / GIB, 6),
                "mmap_page_fault_gib": round(mmap_bytes / GIB, 6),
                "consumed_total_gib": round(
                    sum_field(rows, "observed_consumed_bytes") / GIB, 6
                ),
                "mmap_byte_share_pct": round(
                    100 * mmap_bytes / (read_bytes + mmap_bytes), 6
                )
                if read_bytes + mmap_bytes
                else "",
                "strace_mmap_calls": int(sum_field(rows, "observed_mmap_calls")),
                "strace_read_calls": int(sum_field(rows, "observed_read_calls")),
            }
        )
    return output


def workflow_comparison(
    summaries: list[dict[str, object]]
) -> list[dict[str, object]]:
    grouped: dict[str, dict[int, dict[str, object]]] = defaultdict(dict)
    for row in summaries:
        grouped[str(row["workflow"])][int(row["experiment_id"])] = row
    output: list[dict[str, object]] = []
    for workflow, experiments in sorted(grouped.items()):
        if set(experiments) != {0, 1, 2}:
            continue
        baseline, strace, ebpf = experiments[0], experiments[1], experiments[2]
        baseline_wall = positive(baseline["workflow_wall_seconds"])
        strace_wall = positive(strace["workflow_wall_seconds"])
        ebpf_wall = positive(ebpf["workflow_wall_seconds"])
        strace_bytes = float(strace["consumed_total_gib"]) * GIB
        ebpf_bytes = float(ebpf["consumed_total_gib"]) * GIB
        grade = (
            "controlled"
            if all(
                row["overhead_evidence_grade"] == "controlled"
                for row in (baseline, strace, ebpf)
            )
            else "not_controlled"
        )
        output.append(
            {
                "workflow": workflow,
                "task_rows_exp0": baseline["task_rows"],
                "task_rows_exp1": strace["task_rows"],
                "task_rows_exp2": ebpf["task_rows"],
                "baseline_wall_minutes": baseline["workflow_wall_minutes"],
                "strace_wall_minutes": strace["workflow_wall_minutes"],
                "ebpf_wall_minutes": ebpf["workflow_wall_minutes"],
                "strace_wall_overhead_pct": round(
                    100 * (strace_wall - baseline_wall) / baseline_wall, 6
                )
                if baseline_wall and strace_wall
                else "",
                "ebpf_wall_overhead_pct": round(
                    100 * (ebpf_wall - baseline_wall) / baseline_wall, 6
                )
                if baseline_wall and ebpf_wall
                else "",
                "strace_read_gib": strace["consumed_total_gib"],
                "ebpf_read_gib": ebpf["explicit_read_gib"],
                "ebpf_mmap_page_fault_gib": ebpf["mmap_page_fault_gib"],
                "ebpf_total_gib": ebpf["consumed_total_gib"],
                "ebpf_mmap_byte_share_pct": ebpf["mmap_byte_share_pct"],
                "ebpf_total_minus_strace_gib": round(
                    (ebpf_bytes - strace_bytes) / GIB, 6
                ),
                "ebpf_total_vs_strace_increase_pct": round(
                    100 * (ebpf_bytes - strace_bytes) / strace_bytes, 6
                )
                if strace_bytes
                else "",
                "strace_consumed_coverage_pct": strace[
                    "consumed_signal_coverage_pct"
                ],
                "ebpf_consumed_coverage_pct": ebpf[
                    "consumed_signal_coverage_pct"
                ],
                "overhead_comparison_grade": grade,
            }
        )
    return output


def add_oracle_ranks(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["workflow"]), str(row["bucket"]))].append(row)
    for (_, _), bucket_rows in sorted(grouped.items()):
        peak_sorted = sorted(
            bucket_rows,
            key=lambda row: number(row["peak_memory_bytes"]) or -1,
            reverse=True,
        )
        consumed_sorted = sorted(
            bucket_rows,
            key=lambda row: number(row["observed_consumed_bytes"]) or -1,
            reverse=True,
        )
        peak_rank = {str(row["task_id"]): index + 1 for index, row in enumerate(peak_sorted)}
        consumed_rank = {
            str(row["task_id"]): index + 1 for index, row in enumerate(consumed_sorted)
        }
        n = len(bucket_rows)
        for row in bucket_rows:
            item = dict(row)
            item["bucket_instance_count"] = n
            item["oracle_peak_memory_rank"] = peak_rank[str(row["task_id"])]
            item["oracle_consumed_bytes_rank"] = consumed_rank[str(row["task_id"])]
            for threshold in (1, 5, 10):
                limit = max(1, math.ceil(n * threshold / 100))
                item[f"oracle_top_{threshold}pct_peak_memory"] = bool_string(
                    peak_rank[str(row["task_id"])] <= limit
                )
                item[f"oracle_top_{threshold}pct_consumed_bytes"] = bool_string(
                    consumed_rank[str(row["task_id"])] <= limit
                )
            output.append(item)
    return output


def legacy_training_row(
    row: dict[str, object], c_bytes: object = ""
) -> dict[str, object]:
    return {
        "workflow": row["workflow"],
        "process": row["process"],
        "a_bytes": row["static_input_bytes"],
        "c_bytes": c_bytes,
        "M_peak_rss_bytes": row["peak_memory_bytes"],
        "runtime_seconds": row["runtime_seconds"],
        "task_id": row["task_id"],
        "event_time": row["event_time"],
        "parent_run_id": row["parent_run_id"],
        "dataset_id": row["dataset_id"],
        "task_instance": row["task_instance"],
        "unit_id": row["unit_id"],
        "input_identity": row["input_identity"],
        "experiment_id": row["experiment_id"],
        "instrumentation": row["instrumentation"],
        "c_availability": "POST_RUN" if c_bytes not in ("", None) else "UNAVAILABLE",
    }


def paired_baseline_ebpf_rows(
    all_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    key_fields = (
        "workflow",
        "dataset_id",
        "process",
        "task_instance",
        "unit_id",
        "input_identity",
    )

    def key(row: dict[str, object]) -> tuple[str, ...]:
        return tuple(str(row[field]) for field in key_fields)

    baseline_grouped: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
    ebpf_grouped: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
    for row in all_rows:
        if row["experiment_id"] == 0 and row["ml_eligible"] == "true":
            baseline_grouped[key(row)].append(row)
        elif (
            row["experiment_id"] == 2
            and row["audit_signal_eligible"] == "true"
        ):
            ebpf_grouped[key(row)].append(row)

    output: list[dict[str, object]] = []
    for pair_key in sorted(set(baseline_grouped) & set(ebpf_grouped)):
        baseline_candidates = baseline_grouped[pair_key]
        ebpf_candidates = ebpf_grouped[pair_key]
        if len(baseline_candidates) != 1 or len(ebpf_candidates) != 1:
            continue
        baseline = baseline_candidates[0]
        ebpf = ebpf_candidates[0]
        output.append(
            {
                "workflow": baseline["workflow"],
                "process": baseline["process"],
                "a_bytes": baseline["static_input_bytes"],
                "c_bytes": ebpf["observed_consumed_bytes"],
                "M_peak_rss_bytes": baseline["peak_memory_bytes"],
                "runtime_seconds": baseline["runtime_seconds"],
                "task_id": baseline["task_id"],
                "event_time": baseline["event_time"],
                "parent_run_id": baseline["parent_run_id"],
                "dataset_id": baseline["dataset_id"],
                "task_instance": baseline["task_instance"],
                "unit_id": baseline["unit_id"],
                "input_identity": baseline["input_identity"],
                "baseline_task_id": baseline["task_id"],
                "ebpf_task_id": ebpf["task_id"],
                "ebpf_explicit_read_bytes": ebpf["observed_explicit_read_bytes"],
                "ebpf_mmap_bytes": ebpf["observed_mmap_bytes"],
                "c_availability": "ORACLE_POST_RUN_MATCHED_EXP2",
                "usage_rule": "sizey_online_joint_oracle_only",
            }
        )
    return output


def reset_output_dir(path: Path) -> None:
    if path.exists():
        for child in path.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    path.mkdir(parents=True, exist_ok=True)


def copy_result_docs(output_root: Path) -> None:
    docs_root = Path(__file__).resolve().parent / "results_docs"
    for source in sorted(docs_root.glob("*.md")):
        shutil.copy2(source, output_root / source.name)


def write_checksums(output_root: Path) -> None:
    checksum_path = output_root / "CHECKSUMS.sha256"
    lines: list[str] = []
    for path in sorted(output_root.rglob("*")):
        if not path.is_file() or path == checksum_path:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(output_root)}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    workflows_root = args.workflows_root.resolve()
    output_root = args.output_root.resolve()
    reset_output_dir(output_root)

    all_rows: list[dict[str, object]] = []
    run_summaries: list[dict[str, object]] = []
    manifest_rows: list[dict[str, object]] = []

    for spec in RUNS:
        workflow_dir = workflows_root / spec.workflow.capitalize()
        if spec.workflow == "minimap2":
            workflow_dir = workflows_root / "Minimap2"
        elif spec.workflow == "seqinspector":
            workflow_dir = workflows_root / "Seqinspector"
        elif spec.workflow == "taxprofiler":
            workflow_dir = workflows_root / "Taxprofiler"
        elif spec.workflow == "viralmetagenome":
            workflow_dir = workflows_root / "Viralmetagenome"
        historical_run_dir = workflow_dir / "runs" / spec.run_label
        mode_dir = {0: "exp0", 1: "exp1", 2: "exp2"}[spec.experiment_id]
        fresh_candidates = (
            workflows_root / spec.workflow / mode_dir,
            workflows_root / spec.workflow.capitalize() / mode_dir,
        )
        run_dir = historical_run_dir
        if not run_dir.is_dir():
            run_dir = next(
                (candidate for candidate in fresh_candidates if candidate.is_dir()),
                historical_run_dir,
            )
        metrics_path = run_dir / "training_task_metrics_with_inputs.tsv"
        trace_path = run_dir / "nextflow_trace.tsv"
        if not metrics_path.is_file() or not trace_path.is_file():
            raise FileNotFoundError(f"missing selected run input under {run_dir}")
        fingerprint = source_fingerprint(workflow_dir / "source")
        trace_by_hash, wall_seconds = trace_index(trace_path)
        source_rows = read_tsv(metrics_path)
        strace_summary_path = run_dir / "strace_summary.tsv"
        if spec.instrumentation == "strace" and strace_summary_path.is_file():
            apply_strace_summary(source_rows, strace_summary_path)
        rows = [
            build_task_row(
                row,
                trace_row_for(row, trace_by_hash),
                spec,
                metrics_path,
                fingerprint,
            )
            for row in source_rows
        ]
        trace_join_rows = sum(bool(row["event_time"]) for row in rows)
        if trace_join_rows < 0.9 * len(rows):
            wall_seconds = None
        all_rows.extend(rows)
        run_summaries.append(
            summarize_run(spec, rows, wall_seconds, metrics_path)
        )
        manifest_rows.append(
            {
                "workflow": spec.workflow,
                "version": spec.version,
                "experiment_id": spec.experiment_id,
                "experiment_name": spec.experiment_name,
                "instrumentation": spec.instrumentation,
                "run_label": spec.run_label,
                "system_config_id": spec.system_config_id,
                "worker_count": 13,
                "worker_vcpu": 32,
                "worker_memory_gib": 256,
                "workflow_source_fingerprint": fingerprint,
                "source_metrics_path": str(metrics_path),
                "source_trace_path": str(trace_path),
                "task_rows": len(rows),
                "trace_join_rows": trace_join_rows,
                "trace_join_coverage_pct": pct(trace_join_rows, len(rows)),
                "overhead_evidence_grade": spec.overhead_evidence_grade,
                "evidence_note": spec.evidence_note,
            }
        )

    manifest_columns = list(manifest_rows[0])
    summary_columns = list(run_summaries[0])
    bucket_rows = summarize_buckets(all_rows)
    bucket_columns = list(bucket_rows[0])
    comparison_rows = workflow_comparison(run_summaries)
    comparison_columns = list(comparison_rows[0])

    write_table(output_root / "run_manifest.tsv", manifest_rows, manifest_columns, "\t")
    write_table(
        output_root / "workflow_level_successful_results.csv",
        run_summaries,
        summary_columns,
        ",",
    )
    write_table(
        output_root / "bucket_level_results.csv",
        bucket_rows,
        bucket_columns,
        ",",
    )
    write_table(
        output_root / "paper_tables" / "strace_vs_ebpf_workflow_summary.csv",
        comparison_rows,
        comparison_columns,
        ",",
    )
    write_table(
        output_root / "task_level" / "all_task_observations.tsv",
        all_rows,
        TASK_COLUMNS,
        "\t",
    )

    ml_rows = [row for row in all_rows if row["ml_eligible"] == "true"]
    write_table(
        output_root / "task_level" / "memory_model_training_rows.tsv",
        ml_rows,
        TASK_COLUMNS,
        "\t",
    )
    audit_rows = [row for row in all_rows if row["audit_signal_eligible"] == "true"]
    write_table(
        output_root / "task_level" / "audit_signal_rows.tsv",
        audit_rows,
        TASK_COLUMNS,
        "\t",
    )
    for experiment_id, file_name in (
        (0, "experiment_0_baseline.tsv"),
        (1, "experiment_1_strace_full_online.tsv"),
        (2, "experiment_2_ebpf_full_online.tsv"),
    ):
        experiment_rows = [
            row for row in all_rows if row["experiment_id"] == experiment_id
        ]
        write_table(
            output_root / "task_level" / file_name,
            experiment_rows,
            TASK_COLUMNS,
            "\t",
        )

    for workflow in sorted({str(row["workflow"]) for row in all_rows}):
        workflow_rows = [row for row in all_rows if row["workflow"] == workflow]
        workflow_summaries = [
            row for row in run_summaries if row["workflow"] == workflow
        ]
        write_table(
            output_root / "by_workflow" / workflow / "all_experiments.tsv",
            workflow_rows,
            TASK_COLUMNS,
            "\t",
        )
        write_table(
            output_root / "by_workflow" / workflow / "workflow_results.csv",
            workflow_summaries,
            summary_columns,
            ",",
        )
        for experiment_id, file_name in (
            (0, "exp_0_baseline.tsv"),
            (1, "exp_1_strace_full_online.tsv"),
            (2, "exp_2_ebpf_full_online.tsv"),
        ):
            write_table(
                output_root / "by_workflow" / workflow / file_name,
                [
                    row
                    for row in workflow_rows
                    if row["experiment_id"] == experiment_id
                ],
                TASK_COLUMNS,
                "\t",
            )

    ebpf_oracle = add_oracle_ranks(
        [
            row
            for row in all_rows
            if row["experiment_id"] == 2
            and row["audit_signal_eligible"] == "true"
            and row["ml_eligible"] == "true"
        ]
    )
    oracle_columns = list(TASK_COLUMNS) + [
        "bucket_instance_count",
        "oracle_peak_memory_rank",
        "oracle_consumed_bytes_rank",
        "oracle_top_1pct_peak_memory",
        "oracle_top_1pct_consumed_bytes",
        "oracle_top_5pct_peak_memory",
        "oracle_top_5pct_consumed_bytes",
        "oracle_top_10pct_peak_memory",
        "oracle_top_10pct_consumed_bytes",
    ]
    write_table(
        output_root
        / "task_level"
        / "experiment_3_full_ebpf_oracle_candidates.tsv",
        ebpf_oracle,
        oracle_columns,
        "\t",
    )

    legacy_columns = [
        "workflow",
        "process",
        "a_bytes",
        "c_bytes",
        "M_peak_rss_bytes",
        "runtime_seconds",
        "task_id",
        "event_time",
        "parent_run_id",
        "dataset_id",
        "task_instance",
        "unit_id",
        "input_identity",
        "experiment_id",
        "instrumentation",
        "c_availability",
    ]
    baseline_training = [
        legacy_training_row(row)
        for row in all_rows
        if row["experiment_id"] == 0 and row["ml_eligible"] == "true"
    ]
    strace_feedback = [
        legacy_training_row(row, row["observed_consumed_bytes"])
        for row in all_rows
        if row["experiment_id"] == 1
        and row["ml_eligible"] == "true"
        and row["audit_signal_eligible"] == "true"
    ]
    ebpf_feedback = [
        legacy_training_row(row, row["observed_consumed_bytes"])
        for row in all_rows
        if row["experiment_id"] == 2
        and row["ml_eligible"] == "true"
        and row["audit_signal_eligible"] == "true"
    ]
    paired_oracle = paired_baseline_ebpf_rows(all_rows)
    paired_columns = list(paired_oracle[0]) if paired_oracle else legacy_columns
    write_table(
        output_root / "ml_inputs" / "exp0_static_memory_training.csv",
        baseline_training,
        legacy_columns,
        ",",
    )
    write_table(
        output_root / "ml_inputs" / "exp1_strace_feedback.csv",
        strace_feedback,
        legacy_columns,
        ",",
    )
    write_table(
        output_root / "ml_inputs" / "exp2_ebpf_feedback.csv",
        ebpf_feedback,
        legacy_columns,
        ",",
    )
    write_table(
        output_root
        / "ml_inputs"
        / "paired_exp0_target_exp2_ebpf_oracle.csv",
        paired_oracle,
        paired_columns,
        ",",
    )
    write_table(
        output_root / "ml_inputs" / "all_workflows.csv",
        paired_oracle,
        paired_columns,
        ",",
    )
    if not args.skip_ml_ready:
        from build_ml_ready_data import build_ml_ready_data

        build_ml_ready_data(workflows_root, output_root)
    copy_result_docs(output_root)
    write_checksums(output_root)

    print(f"output_root={output_root}")
    print(f"selected_runs={len(RUNS)}")
    print(f"all_task_rows={len(all_rows)}")
    print(f"ml_eligible_rows={len(ml_rows)}")
    print(f"audit_signal_rows={len(audit_rows)}")
    print(f"experiment_3_oracle_rows={len(ebpf_oracle)}")
    print(f"baseline_static_training_rows={len(baseline_training)}")
    print(f"paired_baseline_ebpf_oracle_rows={len(paired_oracle)}")


if __name__ == "__main__":
    main()
