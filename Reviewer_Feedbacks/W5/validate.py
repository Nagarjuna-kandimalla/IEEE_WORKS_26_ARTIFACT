#!/usr/bin/env python3
"""Validate regenerated final tables against the frozen paper-aligned results."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
FILES = ("budget_summary.csv", "per_seed_net_benefit.csv")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual", type=Path, default=ROOT / "results")
    parser.add_argument("--expected", type=Path, default=ROOT / "expected_results")
    args = parser.parse_args()
    for name in FILES:
        actual = pd.read_csv(args.actual / name)
        expected = pd.read_csv(args.expected / name)
        if list(actual.columns) != list(expected.columns) or actual.shape != expected.shape:
            raise AssertionError(f"Schema mismatch for {name}")
        for column in actual.columns:
            if pd.api.types.is_numeric_dtype(expected[column]):
                if not np.allclose(actual[column], expected[column], rtol=1e-12, atol=1e-10):
                    raise AssertionError(f"Numeric mismatch in {name}: {column}")
            elif not actual[column].astype(str).equals(expected[column].astype(str)):
                raise AssertionError(f"Value mismatch in {name}: {column}")
    summary = pd.read_csv(args.actual / "budget_summary.csv")
    positive = summary.loc[summary.paper_mean_net_gib_hours > 0, "budget_percent"].tolist()
    if positive != [5, 10, 15]:
        raise AssertionError(f"Unexpected positive-net budgets: {positive}")
    print("Validated 8 budgets x 5 seeds; positive net budgets are 5%, 10%, and 15%.")


if __name__ == "__main__":
    main()
