#!/usr/bin/env python3
"""Select and record a fresh CAMP policy from training OOF candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-global-coverage", type=float, default=0.995)
    parser.add_argument(
        "--minimum-workflow-coverage",
        type=float,
        default=0.98,
    )
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite fresh policy: {args.output}")
    candidates = pd.read_csv(args.candidates)
    eligible = candidates.loc[
        (candidates["global_coverage"] >= args.minimum_global_coverage)
        & (
            candidates["minimum_workflow_coverage"]
            >= args.minimum_workflow_coverage
        )
    ]
    if eligible.empty:
        raise SystemExit(
            "no training-only policy meets the frozen coverage constraints"
        )
    selected = eligible.sort_values(
        [
            "request_to_peak_ratio",
            "selection_oom_tasks",
            "base_policy",
            "residual_quantile",
        ],
        kind="mergesort",
    ).iloc[0]
    policy = {
        "name": "fresh_camp_training_oof_selected",
        "feature_view": "A+P+C",
        "base_policy": str(selected["base_policy"]),
        "mode": "hierarchical",
        "residual_quantile": float(selected["residual_quantile"]),
        "selection_source": str(args.candidates.resolve()),
        "selection_source_sha256": sha256(args.candidates),
        "selection_rows": int(selected["selection_rows"]),
        "selection_global_coverage": float(selected["global_coverage"]),
        "selection_minimum_workflow_coverage": float(
            selected["minimum_workflow_coverage"]
        ),
        "selection_request_to_peak_ratio": float(
            selected["request_to_peak_ratio"]
        ),
        "selection_oom_tasks": int(selected["selection_oom_tasks"]),
        "minimum_global_coverage": args.minimum_global_coverage,
        "minimum_workflow_coverage": args.minimum_workflow_coverage,
        "uses_a_prediction": False,
        "test_labels_used_for_allocation": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(policy, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(policy, indent=2))


if __name__ == "__main__":
    main()
