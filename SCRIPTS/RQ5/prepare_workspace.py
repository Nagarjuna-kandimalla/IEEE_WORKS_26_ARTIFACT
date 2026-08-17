#!/usr/bin/env python3
"""Create a fresh, external RQ5 experiment workspace from packaged inputs."""

from __future__ import annotations

import argparse
import csv
import gzip
import shutil
from pathlib import Path


def materialize_cohort(source: Path, destination: Path) -> int:
    rows = 0
    with gzip.open(source, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        with destination.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output, delimiter="\t", lineterminator="\n")
            for row in reader:
                writer.writerow(row)
                rows += 1
    return rows - 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    args = parser.parse_args()

    artifact = args.artifact_root.resolve()
    work = args.work_root.resolve()
    experiment = work / "fresh_experiment"
    if experiment.exists():
        raise SystemExit(f"refusing to overwrite existing workspace: {experiment}")

    template = artifact / "SCRIPTS" / "RQ5" / "experiment_template"
    cohort = artifact / "DATA" / "RQ2" / "camp_ml_cohort_33516.csv.gz"
    split = artifact / "DATA" / "RQ5" / "inputs" / "split_manifest_primary.tsv.gz"
    for required in (template, cohort, split):
        if not required.exists():
            raise SystemExit(f"required packaged input is missing: {required}")

    work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, experiment)
    inputs = experiment / "inputs"
    inputs.mkdir()
    rows = materialize_cohort(cohort, inputs / "canonical_matched_tasks.tsv")
    with gzip.open(split, "rb") as source:
        with (inputs / "split_manifest_primary.tsv").open("wb") as output:
            shutil.copyfileobj(source, output)
    if rows != 33_516:
        raise SystemExit(f"expected 33,516 cohort rows; materialized {rows}")

    print(f"experiment_root={experiment}")
    print(f"cohort_rows={rows}")
    print("workspace_status=ready")


if __name__ == "__main__":
    main()
