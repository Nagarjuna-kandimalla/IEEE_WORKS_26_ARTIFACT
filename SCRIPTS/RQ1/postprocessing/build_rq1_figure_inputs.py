#!/usr/bin/env python3
"""Build the two RQ1 CSV inputs from completed workflow-run artifacts."""

from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path


GIB = 1024**3
GATK_RUN_LABELS = {
    "baseline": "gatk_500_sc13_local_exp0_baseline",
    "strace": "gatk_500_sc13_local_exp1_strace",
    "ebpf": "gatk_500_sc13_local_exp2_ebpf",
}
GATK_MODE_DIRS = {"baseline": "exp0", "strace": "exp1", "ebpf": "exp2"}
LOG_TIMESTAMP = re.compile(
    r"^(?P<stamp>[A-Z][a-z]{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) "
)


def arguments() -> argparse.Namespace:
    artifact = Path(__file__).resolve().parents[3]
    results = artifact / "RESULTS" / "RQ1" / "csv"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workflow-summary",
        type=Path,
        default=results / "strace_vs_ebpf_workflow_summary.csv",
        help="five-workflow builder output, or the packaged six-workflow CSV",
    )
    parser.add_argument(
        "--gatk-runs-root",
        type=Path,
        help="directory containing the three named GATK run directories",
    )
    parser.add_argument(
        "--sarek-active-runtime",
        type=Path,
        default=results / "sarek_reconstructed_active_runtime.csv",
    )
    parser.add_argument(
        "--signal-output",
        type=Path,
        default=results / "strace_vs_ebpf_workflow_summary.csv",
    )
    parser.add_argument(
        "--overhead-output",
        type=Path,
        default=results / "rq1_workflow_overhead.csv",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"no rows to write: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def nextflow_lifecycle_minutes(path: Path) -> float:
    stamps: list[datetime] = []
    completion: datetime | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = LOG_TIMESTAMP.match(line)
        if match:
            stamp = datetime.strptime(match.group("stamp"), "%b-%d %H:%M:%S.%f")
            stamps.append(stamp)
            if "Execution complete -- Goodbye" in line:
                completion = stamp
    if not stamps or completion is None:
        raise ValueError(f"incomplete Nextflow lifecycle log: {path}")
    elapsed = (completion - stamps[0]).total_seconds()
    if elapsed <= 0:
        raise ValueError(f"invalid Nextflow lifecycle timestamps: {path}")
    return elapsed / 60.0


def gatk_metrics(run: Path) -> tuple[int, float, float, float]:
    path = run / "results" / "metrics" / "haplotypecaller_task_metrics.tsv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) != 500:
        raise ValueError(f"expected 500 GATK tasks in {path}; found {len(rows)}")
    return (
        len(rows),
        sum(float(row["strace_read_syscall_bytes"] or 0) for row in rows) / GIB,
        sum(float(row["ebpf_file_read_bytes"] or 0) for row in rows) / GIB,
        sum(float(row["ebpf_file_mmap_bytes"] or 0) for row in rows) / GIB,
    )


def build_gatk_row(root: Path) -> dict[str, object]:
    runs = {}
    for mode, label in GATK_RUN_LABELS.items():
        candidates = (root / label, root / "gatk" / GATK_MODE_DIRS[mode], root / GATK_MODE_DIRS[mode])
        runs[mode] = next((path for path in candidates if path.is_dir()), candidates[0])
    times = {
        mode: nextflow_lifecycle_minutes(run / "nextflow.log")
        for mode, run in runs.items()
    }
    baseline_count, _, _, _ = gatk_metrics(runs["baseline"])
    strace_count, strace_gib, _, _ = gatk_metrics(runs["strace"])
    ebpf_count, _, ebpf_read_gib, ebpf_mmap_gib = gatk_metrics(runs["ebpf"])
    ebpf_total_gib = ebpf_read_gib + ebpf_mmap_gib
    return {
        "workflow": "gatk",
        "task_rows_exp0": baseline_count,
        "task_rows_exp1": strace_count,
        "task_rows_exp2": ebpf_count,
        "baseline_wall_minutes": f"{times['baseline']:.3f}",
        "strace_wall_minutes": f"{times['strace']:.3f}",
        "ebpf_wall_minutes": f"{times['ebpf']:.3f}",
        "strace_wall_overhead_pct": f"{100 * (times['strace'] / times['baseline'] - 1):.2f}",
        "ebpf_wall_overhead_pct": f"{100 * (times['ebpf'] / times['baseline'] - 1):.2f}",
        "strace_read_gib": f"{strace_gib:.6f}",
        "ebpf_read_gib": f"{ebpf_read_gib:.6f}",
        "ebpf_mmap_page_fault_gib": f"{ebpf_mmap_gib:.6f}",
        "ebpf_total_gib": f"{ebpf_total_gib:.6f}",
        "ebpf_mmap_byte_share_pct": f"{100 * ebpf_mmap_gib / ebpf_total_gib:.6f}",
        "ebpf_total_minus_strace_gib": f"{round(ebpf_total_gib, 6) - round(strace_gib, 6):.6f}",
        "ebpf_total_vs_strace_increase_pct": f"{100 * (ebpf_total_gib / strace_gib - 1):.6f}",
        "strace_consumed_coverage_pct": "100.0",
        "ebpf_consumed_coverage_pct": "100.0",
        "overhead_comparison_grade": "pilot",
    }


def main() -> None:
    args = arguments()
    signal = read_csv(args.workflow_summary)
    by_workflow = {row["workflow"].lower(): row for row in signal}
    if args.gatk_runs_root:
        by_workflow["gatk"] = {
            key: str(value) for key, value in build_gatk_row(args.gatk_runs_root).items()
        }
    if "gatk" not in by_workflow:
        raise ValueError("GATK is absent; provide --gatk-runs-root")

    order = ["gatk", "minimap2", "sarek", "seqinspector", "taxprofiler", "viralmetagenome"]
    missing = [name for name in order if name not in by_workflow]
    if missing:
        raise ValueError(f"workflow summary is missing: {', '.join(missing)}")
    signal_rows = [by_workflow[name] for name in order]
    write_csv(args.signal_output, signal_rows)

    sarek_rows = read_csv(args.sarek_active_runtime)
    if len(sarek_rows) != 1:
        raise ValueError("Sarek active-runtime input must have exactly one row")
    sarek = sarek_rows[0]
    overhead_rows: list[dict[str, object]] = []
    for name in order:
        source = by_workflow[name]
        direct_values = (
            source.get("baseline_wall_minutes", ""),
            source.get("strace_wall_minutes", ""),
            source.get("ebpf_wall_minutes", ""),
        )
        if name == "sarek" and not all(direct_values):
            baseline = float(sarek["baseline_minutes"])
            strace = float(sarek["strace_minutes"])
            ebpf = float(sarek["ebpf_minutes"])
            basis = "reconstructed_active"
        else:
            baseline = float(source["baseline_wall_minutes"])
            strace = float(source["strace_wall_minutes"])
            ebpf = float(source["ebpf_wall_minutes"])
            basis = "direct"
        overhead_rows.append(
            {
                "workflow": name,
                "baseline_minutes": f"{baseline:.3f}",
                "strace_minutes": f"{strace:.3f}",
                "ebpf_minutes": f"{ebpf:.3f}",
                "strace_overhead_pct": f"{100 * (strace / baseline - 1):.2f}",
                "ebpf_overhead_pct": f"{100 * (ebpf / baseline - 1):.2f}",
                "timing_basis": basis,
            }
        )
    write_csv(args.overhead_output, overhead_rows)


if __name__ == "__main__":
    main()
