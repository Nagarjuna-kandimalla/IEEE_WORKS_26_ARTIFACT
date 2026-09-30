#!/usr/bin/env python3
"""Validate reproduced ablation tables against the retained reference outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


CSV_FILES = (
    "paper_configuration/native_metrics.csv",
    "paper_configuration/bootstrap_contrasts.csv",
    "route_analysis/route_stratified_metrics.csv",
    "route_analysis/paper_endpoint_reproduction.csv",
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual-root", type=Path, required=True)
    parser.add_argument("--expected-root", type=Path, required=True)
    parser.add_argument("--rtol", type=float, default=1e-9)
    parser.add_argument("--atol", type=float, default=1e-7)
    return parser.parse_args()


def compare_csv(actual_path: Path, expected_path: Path, rtol: float, atol: float) -> None:
    actual = pd.read_csv(actual_path)
    expected = pd.read_csv(expected_path)
    if list(actual.columns) != list(expected.columns):
        raise AssertionError(f"Column mismatch: {actual_path}")
    if actual.shape != expected.shape:
        raise AssertionError(
            f"Shape mismatch for {actual_path}: {actual.shape} != {expected.shape}"
        )
    for column in actual.columns:
        if pd.api.types.is_numeric_dtype(expected[column]):
            if not np.allclose(
                actual[column].to_numpy(float),
                expected[column].to_numpy(float),
                rtol=rtol,
                atol=atol,
                equal_nan=True,
            ):
                delta = np.nanmax(
                    np.abs(
                        actual[column].to_numpy(float)
                        - expected[column].to_numpy(float)
                    )
                )
                raise AssertionError(
                    f"Numeric mismatch in {actual_path}, column {column}; "
                    f"maximum absolute difference={delta}"
                )
        elif not actual[column].fillna("").astype(str).equals(
            expected[column].fillna("").astype(str)
        ):
            raise AssertionError(f"Value mismatch in {actual_path}, column {column}")


def main() -> None:
    args = arguments()
    for relative in CSV_FILES:
        compare_csv(
            args.actual_root / relative,
            args.expected_root / relative,
            args.rtol,
            args.atol,
        )

    actual_manifest = json.loads(
        (args.actual_root / "paper_configuration/analysis_manifest.json").read_text()
    )
    expected_manifest = json.loads(
        (args.expected_root / "paper_configuration/analysis_manifest.json").read_text()
    )
    if actual_manifest != expected_manifest:
        raise AssertionError("Analysis manifest differs from the reference contract")

    native = pd.read_csv(args.actual_root / "paper_configuration/native_metrics.csv")
    if set(native["variant"]) != {"A+P", "A+P+CH", "A+P+CHAT", "A+P+CH+CHAT"}:
        raise AssertionError("Expected all four ablation variants")
    if not (native["rows"] == 5026).all():
        raise AssertionError("Every variant must contain the 5,026 held-out tasks")
    print("Validated four variants, 5,026 held-out rows, metrics, and split contract.")


if __name__ == "__main__":
    main()
