#!/usr/bin/env python3
"""Create an immutable initial CAMP bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CAMP_ROOT = ROOT.parent
sys.path.insert(0, str(CAMP_ROOT / "scripts"))

import common
from modeling import fit_final_predictions, materialize_model_features


QUANTILES = [0.5, 0.9, 0.95, 0.99, 0.995]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-model-root", type=Path, required=True)
    parser.add_argument("--initial-features", type=Path, required=True)
    parser.add_argument("--c-hat-oof", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--n-jobs", type=int, default=4)
    args = parser.parse_args()

    if args.output_root.exists():
        raise SystemExit(f"refusing to overwrite {args.output_root}")
    common.CONFIG_PATH = args.config.resolve()
    shutil.copytree(args.source_model_root, args.output_root)

    initial = pd.read_csv(
        args.initial_features,
        sep="\t",
        low_memory=False,
    )
    c_hat = pd.read_csv(args.c_hat_oof, sep="\t")
    if not (
        initial["logical_task_id"].astype(str).to_numpy()
        == c_hat["logical_task_id"].astype(str).to_numpy()
    ).all():
        raise SystemExit("initial and C-hat rows are not aligned")
    initial["c_hat_bytes"] = c_hat["c_hat_bytes"].to_numpy(dtype=float)
    features = materialize_model_features(initial)
    target_mib = (
        pd.to_numeric(
            features["peak_memory_bytes"],
            errors="raise",
        ).to_numpy(dtype=float)
        / 2**20
    )
    fit_final_predictions(
        features,
        features.iloc[:1].copy(),
        target_mib,
        view="A+P",
        quantiles=QUANTILES,
        n_jobs=args.n_jobs,
        model_root=args.output_root / "a_plus_p",
    )
    initial.to_csv(
        args.output_root / "base_training_frame.tsv",
        sep="\t",
        index=False,
    )
    calibration_columns = list(initial.columns) + [
        "calibration_model_version",
        "base_a_mib",
        "base_a_plus_p_plus_c_mib",
        "a_calibration_model_scope",
        "camp_calibration_model_scope",
    ]
    pd.DataFrame(columns=calibration_columns).to_csv(
        args.output_root / "version_calibration.tsv",
        sep="\t",
        index=False,
    )
    source_digest = hashlib.sha256()
    for path in (
        args.initial_features,
        args.c_hat_oof,
        args.config,
    ):
        source_digest.update(path.resolve().read_bytes())
    manifest = {
        "design": "camp_live_online_learning",
        "model_version": args.model_version,
        "parent_model_version": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "training_rows": int(len(initial)),
        "online_training_rows": 0,
        "known_workflows": sorted(
            initial["workflow"].astype(str).str.lower().unique()
        ),
        "feature_views": ["A", "A+P", "A+P+C"],
        "version_calibration_rows": 0,
        "version_calibration_model_version": args.model_version,
        "fresh_source_digest": source_digest.hexdigest(),
        "allocation_contract": (
            "cold leave-one-workflow-out A+P+C; otherwise calibrated "
            "process/global A+P+C with RQ2 I0-I5 residual calibration "
            "and no A prediction floor"
        ),
        "model_update_contract": (
            "matched LightGBM quantile ensembles; frozen model seeds, "
            "quantiles, and per-variant calibration policy; A+P+C is direct"
        ),
    }
    (args.output_root / "online_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
