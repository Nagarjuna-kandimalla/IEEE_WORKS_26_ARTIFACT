#!/usr/bin/env python3
"""Summarize the exact elastic-worker runs without changing paper routes."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


PACKAGE_ROOT = Path(__file__).resolve().parent

VARIANTS = {
    "A+P": "a_plus_p",
    "A+P+CH": "a_plus_p_plus_ch",
    "A+P+CHAT": "a_plus_p_plus_chat",
    "A+P+CH+CHAT": "a_plus_p_plus_c",
}


def metrics(frame: pd.DataFrame) -> dict:
    actual = frame["actual_peak_mib"].to_numpy(float)
    q50 = frame["point_q50_prediction_mib"].to_numpy(float)
    q99 = frame["q990_prediction_mib"].to_numpy(float)
    request = frame["first_allocation_mib"].to_numpy(float)
    error = actual - q99
    under = request < actual
    return {
        "rows": len(frame),
        "q50_mdape_percent": 100 * np.median(np.abs(q50 - actual) / actual),
        "spearman": spearmanr(q50, actual).statistic,
        "q99_pinball_mib": np.mean(np.maximum(0.99 * error, -0.01 * error)),
        "underallocations": int(under.sum()),
        "coverage_percent": 100 * np.mean(~under),
        "total_request_gib": request.sum() / 1024,
    }


def read(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep="\t")
    if len(frame) != 5026:
        raise ValueError(f"Expected 5,026 test rows in {path}; found {len(frame)}")
    return frame.sort_values("logical_task_id").reset_index(drop=True)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-root",
        type=Path,
        default=PACKAGE_ROOT / "reference_predictions" / "seed_1996",
    )
    parser.add_argument(
        "--published-reference-root",
        type=Path,
        default=PACKAGE_ROOT / "published_reference",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PACKAGE_ROOT / "results" / "route_analysis",
    )
    return parser.parse_args()


def main() -> None:
    args = arguments()
    run_root = args.run_root.resolve()
    reference_root = args.published_reference_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    reference_ids = None
    for route in ("global", "process_tail"):
        for variant, stem in VARIANTS.items():
            directory = f"{stem}_final_global_base" if route == "global" else stem
            frame = read(run_root / directory / "task_predictions.tsv")
            ids = frame["logical_task_id"].astype(str).to_numpy()
            if reference_ids is None:
                reference_ids = ids
            elif not np.array_equal(reference_ids, ids):
                raise ValueError(f"Test population mismatch for {route}/{variant}")
            rows.append({"route": route, "variant": variant, **metrics(frame)})
    route_metrics = pd.DataFrame(rows)
    route_metrics.to_csv(output_dir / "route_stratified_metrics.csv", index=False)

    paper_rows = []
    endpoints = [
        ("Access (A+P)", "a_plus_p_task_predictions.tsv.gz", "a_plus_p_final_global_base"),
        ("Full CAMP", "a_plus_p_plus_c_task_predictions.tsv.gz", "a_plus_p_plus_c"),
    ]
    for method, published_file, reproduced_dir in endpoints:
        for source, path in (
            ("paper_frozen", reference_root / published_file),
            ("elastic_reproduction", run_root / reproduced_dir / "task_predictions.tsv"),
        ):
            paper_rows.append({"method": method, "source": source, **metrics(read(path))})
    pd.DataFrame(paper_rows).to_csv(
        output_dir / "paper_endpoint_reproduction.csv", index=False
    )

    with (output_dir / "README.md").open("w") as handle:
        handle.write(
            "# Paper-aligned C-hat ablation\n\n"
            "All rows use the paper's seed-1996 85/15 chronological split: "
            "28,490 development tasks and 5,026 held-out tasks. No model or "
            "allocation policy was reselected with test outcomes.\n\n"
            "`paper_endpoint_reproduction.csv` preserves the paper routes: "
            "global routing for Access (A+P) and process-tail routing for full CAMP.\n\n"
            "`route_stratified_metrics.csv` shows all four feature variants under "
            "each route. Comparisons within a route isolate feature changes; the "
            "paper endpoint comparison preserves the deployed paper configurations.\n"
        )


if __name__ == "__main__":
    main()
