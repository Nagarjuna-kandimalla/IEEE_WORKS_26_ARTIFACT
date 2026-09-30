#!/usr/bin/env python3
"""Ablation using each system's paper execution path."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


PACKAGE_ROOT = Path(__file__).resolve().parent

# Access retains its paper global route. CAMP variants retain CAMP's paper
# process-tail eligibility and global-fallback route.
SOURCE_PATHS = {
    "A+P": "a_plus_p_final_global_base/task_predictions.tsv",
    "A+P+CH": "a_plus_p_plus_ch/task_predictions.tsv",
    "A+P+CHAT": "a_plus_p_plus_chat/task_predictions.tsv",
    "A+P+CH+CHAT": "a_plus_p_plus_c/task_predictions.tsv",
}


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-root",
        type=Path,
        default=PACKAGE_ROOT / "reference_predictions" / "seed_1996",
        help="Directory containing test_causal_features.tsv and variant directories.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PACKAGE_ROOT / "results" / "paper_configuration",
    )
    parser.add_argument("--bootstrap-repetitions", type=int, default=5000)
    return parser.parse_args()


def met(frame, indices=None):
    if indices is not None:
        frame = frame.iloc[indices]
    actual = frame.actual_peak_mib.to_numpy(float)
    q50 = frame.point_q50_prediction_mib.to_numpy(float)
    q99 = frame.q990_prediction_mib.to_numpy(float)
    request = frame.first_allocation_mib.to_numpy(float)
    runtime = pd.to_numeric(frame.runtime_seconds, errors="coerce").fillna(0).to_numpy(float)
    ape = np.abs(q50 - actual) / np.maximum(actual, 1e-12)
    under = request < actual
    error = actual - q99
    return {
        "rows": len(frame),
        "mdape_percent": 100 * np.median(ape),
        "mean_ape_percent": 100 * np.mean(ape),
        "p95_ape_percent": 100 * np.quantile(ape, 0.95),
        "spearman": spearmanr(q50, actual).statistic,
        "q99_pinball_mib": np.mean(np.maximum(0.99 * error, -0.01 * error)),
        "q99_coverage_percent": 100 * np.mean(q99 >= actual),
        "underallocations": int(under.sum()),
        "coverage_percent": 100 * np.mean(~under),
        "total_request_gib": request.sum() / 1024,
        "request_to_peak": request.sum() / actual.sum(),
        "unused_gib_hours": np.sum(np.maximum(request - actual, 0) * runtime) / 1024 / 3600,
    }


def main():
    args = arguments()
    run_root = args.run_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    test = pd.read_csv(run_root / "test_causal_features.tsv", sep="\t", low_memory=False)
    ids = test.logical_task_id.astype(str).to_numpy()
    frames = {}
    for variant, relative_path in SOURCE_PATHS.items():
        path = run_root / relative_path
        frame = pd.read_csv(path, sep="\t", low_memory=False)
        if len(frame) != 5026 or not np.array_equal(frame.logical_task_id.astype(str), ids):
            raise ValueError(f"Population mismatch: {variant}")
        frames[variant] = frame

    native = pd.DataFrame([{"variant": key, **met(frame)} for key, frame in frames.items()])
    native.to_csv(output_dir / "native_metrics.csv", index=False)

    groups = {}
    indexed = test.reset_index()
    for (workflow, _), rows in indexed.groupby(["workflow", "split_group_id"], sort=True):
        groups.setdefault(str(workflow), []).append(rows["index"].to_numpy(int))

    rng = np.random.default_rng(20260928)
    metric_names = [
        "mdape_percent", "mean_ape_percent", "p95_ape_percent", "spearman",
        "q99_pinball_mib", "underallocations", "request_to_peak", "unused_gib_hours",
    ]
    variants = ["A+P+CH", "A+P+CHAT", "A+P+CH+CHAT"]
    samples = {(variant, metric): [] for variant in variants for metric in metric_names}
    for _ in range(args.bootstrap_repetitions):
        indices = np.concatenate([
            arrays[index]
            for arrays in groups.values()
            for index in rng.integers(0, len(arrays), size=len(arrays))
        ])
        baseline = met(frames["A+P"], indices)
        for variant in variants:
            current = met(frames[variant], indices)
            for metric in metric_names:
                samples[(variant, metric)].append(float(current[metric]) - float(baseline[metric]))

    baseline = met(frames["A+P"])
    contrasts = []
    for variant in variants:
        current = met(frames[variant])
        for metric in metric_names:
            values = np.asarray(samples[(variant, metric)])
            contrasts.append({
                "comparison": f"{variant} minus A+P",
                "metric": metric,
                "observed_difference": float(current[metric]) - float(baseline[metric]),
                "ci95_low": np.quantile(values, 0.025),
                "ci95_high": np.quantile(values, 0.975),
                "repetitions": args.bootstrap_repetitions,
            })
    pd.DataFrame(contrasts).to_csv(output_dir / "bootstrap_contrasts.csv", index=False)

    manifest = {
        "development_rows": 28490,
        "test_rows": 5026,
        "split": "85/15 chronological",
        "split_seed": 1996,
        "access_route": "global",
        "camp_variant_route": "process-tail with global fallback",
        "bootstrap_repetitions": args.bootstrap_repetitions,
        "bootstrap_unit": "split_group within workflow",
        "test_labels_used_for_training_or_policy_selection": False,
    }
    (output_dir / "analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(native.to_string(index=False))


if __name__ == "__main__":
    main()
