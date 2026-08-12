#!/usr/bin/env python3
"""Build the pre-launch static feature manifest for every HaplotypeCaller task."""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path


def file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else -1


def task_hash(sample: dict[str, str], interval: dict[str, str], reference_size: int) -> str:
    """Stable task identity shared with the per-task result row."""
    identity = "|".join([
        "gatk_synthetic_nextflow", "HaplotypeCaller", sample["sample_id"], sample["coverage"],
        sample["seed"], interval["scatter_id"], interval["chrom"], interval["start"],
        interval["end"], str(reference_size), "3072", "2",
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", required=True)
    parser.add_argument("--intervals", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.samples, newline="", encoding="utf-8") as handle:
        samples = list(csv.DictReader(handle, delimiter="\t"))
    with open(args.intervals, newline="", encoding="utf-8") as handle:
        intervals = list(csv.DictReader(handle, delimiter="\t"))

    reference = Path(args.reference)
    reference_size = file_size(reference)
    columns = [
        "workflow", "dataset_id", "process", "bucket", "task_type", "task_instance", "task_hash", "sample_id", "coverage", "seed",
        "scatter_id", "chrom", "start", "end", "interval_length_bp", "contig_length_bp",
        "has_str_region", "reference_path", "reference_size_bytes", "planned_java_heap_mb",
        "planned_hc_threads", "feature_availability",
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for sample in samples:
            for interval in intervals:
                start = int(interval["start"])
                end = int(interval["end"])
                writer.writerow({
                    "workflow": "gatk_synthetic_nextflow",
                    "dataset_id": args.dataset_id,
                    "process": "HaplotypeCaller",
                    "bucket": "GATK::HaplotypeCaller",
                    "task_type": "HaplotypeCaller",
                    "task_instance": f"{sample['sample_id']}.scatter_{interval['scatter_id']}",
                    "task_hash": task_hash(sample, interval, reference_size),
                    "sample_id": sample["sample_id"],
                    "coverage": sample["coverage"],
                    "seed": sample["seed"],
                    "scatter_id": interval["scatter_id"],
                    "chrom": interval["chrom"],
                    "start": start,
                    "end": end,
                    "interval_length_bp": end - start + 1,
                    "contig_length_bp": interval["contig_length"],
                    "has_str_region": interval["has_str_region"],
                    "reference_path": str(reference.resolve()),
                    "reference_size_bytes": reference_size,
                    "planned_java_heap_mb": 3072,
                    "planned_hc_threads": 2,
                    "feature_availability": "pre_launch",
                })


if __name__ == "__main__":
    main()
