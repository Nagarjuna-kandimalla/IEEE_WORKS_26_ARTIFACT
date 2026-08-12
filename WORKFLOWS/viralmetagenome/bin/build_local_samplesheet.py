#!/usr/bin/env python3
"""Build a Viralmetagenome samplesheet from verified local FASTQs."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from urllib.parse import urlparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--fastq-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    source = Path(args.source)
    fastq_dir = Path(args.fastq_dir).resolve()
    output = Path(args.out)
    rows: list[dict[str, str]] = []

    with source.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            local_paths: dict[str, str] = {}
            for url_field, size_field in (
                ("fastq_1", "fastq_1_bytes"),
                ("fastq_2", "fastq_2_bytes"),
            ):
                filename = Path(urlparse(row[url_field]).path).name
                local_path = fastq_dir / filename
                if not local_path.is_file():
                    raise SystemExit(f"Missing FASTQ: {local_path}")
                expected_size = int(row[size_field])
                actual_size = local_path.stat().st_size
                if actual_size != expected_size:
                    raise SystemExit(
                        f"Size mismatch for {local_path}: "
                        f"{actual_size} != {expected_size}"
                    )
                local_paths[url_field] = str(local_path)

            rows.append({
                "sample": row["viralmetagenome_sample"],
                "fastq_1": local_paths["fastq_1"],
                "fastq_2": local_paths["fastq_2"],
            })

    if len(rows) != 3000:
        raise SystemExit(f"Expected 3000 samples, found {len(rows)}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("sample", "fastq_1", "fastq_2"),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} verified local samples to {output}")


if __name__ == "__main__":
    main()
