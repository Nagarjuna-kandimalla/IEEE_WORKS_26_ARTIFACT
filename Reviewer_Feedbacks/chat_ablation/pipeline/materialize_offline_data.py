#!/usr/bin/env python3
"""Materialize the gzipped CSV cohort as the TSV expected by RQ2."""

from __future__ import annotations

import argparse
import csv
import gzip
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.source, "rt", encoding="utf-8", newline="") as source:
        reader = csv.reader(source)
        with args.output.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output, delimiter="\t", lineterminator="\n")
            writer.writerows(reader)


if __name__ == "__main__":
    main()
