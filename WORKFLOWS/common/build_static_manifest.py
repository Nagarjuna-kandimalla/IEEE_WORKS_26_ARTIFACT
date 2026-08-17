#!/usr/bin/env python3
"""Build static task manifests for the isolated Minimap2 and Bowtie2 runs."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


COLUMNS = [
    "workflow", "process", "bucket", "task_type", "task_instance", "dataset_id",
    "source_manifest", "unit_id", "window_type", "expected_peak_gb", "input_bytes",
    "planned_chunks", "feature_availability",
]


def write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def base(workflow: str, process: str, instance: str, dataset: str, source: str) -> dict[str, str]:
    return {
        "workflow": workflow, "process": process, "bucket": f"{workflow}::{process}",
        "task_type": process, "task_instance": instance, "dataset_id": dataset,
        "source_manifest": source, "unit_id": "", "window_type": "",
        "expected_peak_gb": "", "input_bytes": "", "planned_chunks": "",
        "feature_availability": "pre_launch",
    }


def minimap(args: argparse.Namespace) -> None:
    source = str(Path(args.windows).resolve())
    with Path(args.windows).open(newline="", encoding="utf-8") as handle:
        windows = list(csv.DictReader(handle, delimiter="\t"))
    rows = [base("minimap2", process, instance, args.dataset_id, source) for process, instance in [
        ("GENERATE_REFERENCE", "genome"), ("PLAN_WINDOWS", f"n{len(windows)}"), ("BUILD_INDEX", "genome_mmi"),
    ]]
    for window in windows:
        for process in ("GENERATE_WINDOW_READS", "MAP_WINDOW", "INDEX_BAM", "FLAGSTAT_BAM"):
            row = base("minimap2", process, f"window_{window['window_id']}", args.dataset_id, source)
            row.update({"unit_id": window["window_id"], "window_type": window["window_type"], "expected_peak_gb": window["expected_peak_gb"]})
            rows.append(row)
    write(Path(args.out), rows)


def bowtie(args: argparse.Namespace) -> None:
    source = str(Path(args.runs).resolve())
    with Path(args.runs).open(newline="", encoding="utf-8") as handle:
        runs = list(csv.DictReader(handle, delimiter="\t"))
    rows = [base("bowtie2", "DOWNLOAD_REFERENCE", "sacCer_ref", args.dataset_id, source), base("bowtie2", "BUILD_INDEX", "sacCer_bt2", args.dataset_id, source)]
    for run in runs:
        accession = run["run_accession"]
        chunks = int(run["chunks"])
        total_bytes = int(run["fastq1_bytes"]) + int(run["fastq2_bytes"])
        for process, instance in (("DOWNLOAD_RUN", accession), ("SPLIT_RUN", accession)):
            row = base("bowtie2", process, instance, args.dataset_id, source)
            row.update({"unit_id": accession, "input_bytes": str(total_bytes), "planned_chunks": str(chunks)})
            rows.append(row)
        for chunk in range(1, chunks + 1):
            for process in ("ALIGN_CHUNK", "INDEX_BAM", "FLAGSTAT_BAM"):
                row = base("bowtie2", process, f"{accession}_chunk_{chunk}", args.dataset_id, source)
                row.update({"unit_id": accession, "input_bytes": str(total_bytes // chunks), "planned_chunks": str(chunks)})
                rows.append(row)
    write(Path(args.out), rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="workflow", required=True)
    mini = sub.add_parser("minimap2")
    mini.add_argument("--windows", required=True); mini.add_argument("--dataset-id", required=True); mini.add_argument("--out", required=True)
    bow = sub.add_parser("bowtie2")
    bow.add_argument("--runs", required=True); bow.add_argument("--dataset-id", required=True); bow.add_argument("--out", required=True)
    args = parser.parse_args()
    minimap(args) if args.workflow == "minimap2" else bowtie(args)


if __name__ == "__main__":
    main()
