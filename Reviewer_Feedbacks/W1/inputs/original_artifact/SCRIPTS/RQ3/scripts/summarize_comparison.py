#!/usr/bin/env python3
"""Validate and summarize the fresh matched Sizey-versus-CAMP replay."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.metrics import r2_score


ROOT = Path(__file__).resolve().parents[1]
SEED = 1996
MIB = 1024.0**2
METHODS = (
    ("Sizey", "native_A_with_peak_feedback"),
    ("CAMP", "A"),
    ("CAMP", "A+P"),
    ("CAMP", "A+P+C"),
)
VARIANT_PATHS = {
    "A": "a",
    "A+P": "a_plus_p",
    "A+P+C": "a_plus_p_plus_c",
}
WORKFLOWS = (
    "gatk",
    "minimap2",
    "sarek",
    "seqinspector",
    "taxprofiler",
    "viralmetagenome",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def common_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    actual = pd.to_numeric(result["actual_peak_mib"], errors="raise")
    raw = pd.to_numeric(result["raw_prediction_mib"], errors="raise")
    allocation = pd.to_numeric(
        result["first_allocation_mib"], errors="raise"
    )
    runtime = pd.to_numeric(result["runtime_seconds"], errors="raise")
    result["absolute_error_mib"] = (raw - actual).abs()
    result["absolute_percentage_error"] = (
        result["absolute_error_mib"] / actual.replace(0.0, np.nan)
    )
    result["initial_oom"] = allocation < actual
    result["underallocation_mib"] = (actual - allocation).clip(lower=0.0)
    result["overallocation_mib"] = (allocation - actual).clip(lower=0.0)
    result["allocation_to_peak_ratio"] = allocation / actual
    result["requested_gib"] = allocation / 1024.0
    result["overallocation_gib"] = result["overallocation_mib"] / 1024.0
    result["underallocation_gib"] = result["underallocation_mib"] / 1024.0
    result["usage_gib_hours"] = actual * runtime / 1024.0 / 3600.0
    first_waste_mib = np.where(
        allocation >= actual,
        allocation - actual,
        allocation,
    )
    result["first_attempt_wastage_gib_hours"] = (
        first_waste_mib * runtime / 1024.0 / 3600.0
    )
    return result


def load_split() -> pd.DataFrame:
    frames = []
    for workflow in WORKFLOWS:
        path = ROOT / "data" / "split_manifests" / (
            f"{workflow}_{SEED}.tsv"
        )
        frames.append(pd.read_csv(path, sep="\t", low_memory=False))
    return pd.concat(frames, ignore_index=True)


def load_results() -> pd.DataFrame:
    frames = []
    for workflow in WORKFLOWS:
        path = (
            ROOT
            / "baseline"
            / "sizey"
            / workflow
            / "normalized_task_predictions.tsv"
        )
        frames.append(common_metrics(pd.read_csv(path, sep="\t")))
    for variant, directory in VARIANT_PATHS.items():
        path = (
            ROOT
            / "results"
            / f"seed_{SEED}"
            / directory
            / "task_predictions.tsv"
        )
        frame = pd.read_csv(path, sep="\t", low_memory=False)
        frame["method"] = "CAMP"
        frame["feature_view"] = variant
        frames.append(common_metrics(frame))
    return pd.concat(frames, ignore_index=True, sort=False)


def validate_results(frame: pd.DataFrame) -> dict[str, object]:
    split = load_split()
    test_ids = set(
        split.loc[split["role"].eq("test"), "logical_task_id"].astype(str)
    )
    canonical = pd.read_csv(
        ROOT / "inputs" / "canonical_tasks.tsv",
        sep="\t",
        usecols=["logical_task_id", "peak_memory_bytes"],
    ).set_index("logical_task_id")
    expected_peak = canonical["peak_memory_bytes"] / MIB

    method_hashes = {}
    for method, view in METHODS:
        subset = frame.loc[
            frame["method"].eq(method) & frame["feature_view"].eq(view)
        ]
        label = f"{method} {view}"
        ids = subset["logical_task_id"].astype(str)
        if len(subset) != 21337 or ids.nunique() != 21337:
            raise ValueError(f"{label}: expected 21,337 unique test tasks")
        if set(ids) != test_ids:
            raise ValueError(f"{label}: task IDs differ from Sizey test split")
        expected = expected_peak.loc[ids].to_numpy(dtype=float)
        observed = subset["actual_peak_mib"].to_numpy(dtype=float)
        if not np.allclose(expected, observed, rtol=0.0, atol=1e-9):
            raise ValueError(f"{label}: target peak RSS differs")
        method_hashes[label] = hashlib.sha256(
            "\n".join(sorted(ids)).encode("utf-8")
        ).hexdigest()

    apc = frame.loc[
        frame["method"].eq("CAMP")
        & frame["feature_view"].eq("A+P+C")
    ]
    forbidden_columns = {
        "a_prediction_mib",
        "a_safety_bound_mib",
        "a_plus_p_safety_bound_mib",
        "dual_bound_source",
    }
    present_forbidden = sorted(forbidden_columns & set(apc.columns))
    if present_forbidden:
        raise ValueError(
            f"APC output contains forbidden fallback columns: {present_forbidden}"
        )
    base_names = sorted(
        apc["allocation_base_policy"].dropna().astype(str).unique()
    )
    if any("a_plus" in value.lower() or value == "A" for value in base_names):
        raise ValueError(f"APC allocation references A/AP: {base_names}")
    policy_path = (
        ROOT
        / "results"
        / f"seed_{SEED}"
        / "a_plus_p_plus_c"
        / "selected_policy.json"
    )
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if "process_policies" in policy:
        policy_bases = sorted(
            {
                str(value["base_policy"])
                for value in policy["process_policies"].values()
            }
        )
    else:
        policy_bases = [str(policy["base_policy"])]
    if set(base_names) != set(policy_bases):
        raise ValueError("APC task policy does not match selected policy")

    return {
        "status": "passed",
        "test_tasks_per_method": 21337,
        "method_task_id_hashes": method_hashes,
        "all_method_task_sets_equal": len(set(method_hashes.values())) == 1,
        "apc_allocation_base_policies": base_names,
        "apc_uses_a_or_ap_allocation_floor": False,
        "apc_policy": policy,
        "apc_policy_sha256": sha256(policy_path),
    }


def summarize_group(group: pd.DataFrame) -> dict[str, float | int]:
    actual = group["actual_peak_mib"].to_numpy(dtype=float)
    raw = group["raw_prediction_mib"].to_numpy(dtype=float)
    allocation = group["first_allocation_mib"].to_numpy(dtype=float)
    ape = np.abs(raw - actual) / actual
    usage = float(group["usage_gib_hours"].sum())
    waste = float(group["first_attempt_wastage_gib_hours"].sum())
    observed_peak_gib = float(actual.sum() / 1024.0)
    requested_gib = float(allocation.sum() / 1024.0)
    return {
        "tasks": len(group),
        "mae_mib": float(np.mean(np.abs(raw - actual))),
        "mape": float(np.mean(ape)),
        "median_ape": float(np.median(ape)),
        "p95_ape": float(np.quantile(ape, 0.95)),
        "r2_log": float(
            r2_score(np.log1p(actual), np.log1p(np.maximum(raw, 0.0)))
        ),
        "raw_underprediction_tasks": int(np.sum(raw < actual)),
        "raw_underprediction_rate": float(np.mean(raw < actual)),
        "initial_oom_tasks": int(np.sum(allocation < actual)),
        "first_attempt_coverage": float(np.mean(allocation >= actual)),
        "observed_peak_gib": observed_peak_gib,
        "requested_gib": requested_gib,
        "request_to_peak_ratio": requested_gib / observed_peak_gib,
        "median_allocation_to_peak_ratio": float(
            group["allocation_to_peak_ratio"].median()
        ),
        "p95_allocation_to_peak_ratio": float(
            group["allocation_to_peak_ratio"].quantile(0.95)
        ),
        "overallocation_gib": float(group["overallocation_gib"].sum()),
        "underallocation_gib": float(group["underallocation_gib"].sum()),
        "usage_gib_hours": usage,
        "first_attempt_wastage_gib_hours": waste,
        "maq": usage / (usage + waste),
        "tasks_with_native_retry": int(
            (pd.to_numeric(group["native_retry_count"]) > 0).sum()
        ),
        "native_failed_attempts": int(
            pd.to_numeric(group["native_retry_count"]).sum()
        ),
        "native_unresolved_ooms": int(
            (
                pd.to_numeric(group["native_final_allocation_mib"])
                < group["actual_peak_mib"]
            ).sum()
        ),
    }


def summary_table(frame: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    records = []
    for keys, group in frame.groupby(groups, sort=True):
        values = keys if isinstance(keys, tuple) else (keys,)
        record = dict(zip(groups, values))
        record.update(summarize_group(group))
        records.append(record)
    return pd.DataFrame.from_records(records)


def heavy_tail_rows(frame: pd.DataFrame) -> pd.DataFrame:
    identity = frame.loc[
        frame["method"].eq("Sizey"),
        ["logical_task_id", "workflow", "actual_peak_mib"],
    ].copy()
    thresholds = identity.groupby("workflow")["actual_peak_mib"].quantile(0.95)
    identity["workflow_q95_peak_mib"] = identity["workflow"].map(thresholds)
    tail_ids = set(
        identity.loc[
            identity["actual_peak_mib"]
            >= identity["workflow_q95_peak_mib"],
            "logical_task_id",
        ].astype(str)
    )
    result = frame.loc[
        frame["logical_task_id"].astype(str).isin(tail_ids)
    ].copy()
    result["workflow_q95_peak_mib"] = result["workflow"].map(thresholds)
    return result


def paired_bootstrap(
    frame: pd.DataFrame,
    left: tuple[str, str],
    right: tuple[str, str],
    *,
    replicates: int = 2000,
) -> list[dict[str, object]]:
    left_frame = frame.loc[
        frame["method"].eq(left[0]) & frame["feature_view"].eq(left[1])
    ].sort_values("logical_task_id")
    right_frame = frame.loc[
        frame["method"].eq(right[0]) & frame["feature_view"].eq(right[1])
    ].sort_values("logical_task_id")
    left_ids = left_frame["logical_task_id"].astype(str).to_numpy()
    right_ids = right_frame["logical_task_id"].astype(str).to_numpy()
    if not np.array_equal(left_ids, right_ids):
        raise ValueError("paired bootstrap task IDs differ")

    def vectors(data: pd.DataFrame) -> dict[str, np.ndarray]:
        return {
            "absolute_percentage_error": data[
                "absolute_percentage_error"
            ].to_numpy(dtype=float),
            "initial_oom": data["initial_oom"].to_numpy(dtype=float),
            "requested_mib": data["first_allocation_mib"].to_numpy(dtype=float),
            "allocation_to_peak_ratio": data[
                "allocation_to_peak_ratio"
            ].to_numpy(dtype=float),
            "first_attempt_wastage_gib_hours": data[
                "first_attempt_wastage_gib_hours"
            ].to_numpy(dtype=float),
        }

    left_values = vectors(left_frame)
    right_values = vectors(right_frame)
    rng = np.random.default_rng(SEED)
    sample_count = len(left_frame)
    records = []
    for metric in left_values:
        delta = right_values[metric] - left_values[metric]
        bootstrap = np.empty(replicates, dtype=float)
        for index in range(replicates):
            sample = rng.integers(0, sample_count, size=sample_count)
            bootstrap[index] = float(np.mean(delta[sample]))
        records.append(
            {
                "left": f"{left[0]} {left[1]}",
                "right": f"{right[0]} {right[1]}",
                "metric": metric,
                "left_mean": float(np.mean(left_values[metric])),
                "right_mean": float(np.mean(right_values[metric])),
                "right_minus_left": float(np.mean(delta)),
                "ci95_low": float(np.quantile(bootstrap, 0.025)),
                "ci95_high": float(np.quantile(bootstrap, 0.975)),
                "bootstrap_replicates": replicates,
            }
        )
    return records


def markdown_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| Method | Tasks | Median APE | First OOM | Coverage | "
        "Request/Peak | Wastage GiB-h | MAQ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    order = {value: index for index, value in enumerate(METHODS)}
    ordered = frame.assign(
        _order=[
            order[(method, view)]
            for method, view in zip(frame["method"], frame["feature_view"])
        ]
    ).sort_values("_order")
    for row in ordered.itertuples(index=False):
        label = (
            "Sizey"
            if row.method == "Sizey"
            else f"CAMP {row.feature_view}"
        )
        lines.append(
            f"| {label} | {int(row.tasks):,} | "
            f"{100 * row.median_ape:.2f}% | "
            f"{int(row.initial_oom_tasks):,} | "
            f"{100 * row.first_attempt_coverage:.2f}% | "
            f"{row.request_to_peak_ratio:.2f}x | "
            f"{row.first_attempt_wastage_gib_hours:.3f} | "
            f"{100 * row.maq:.2f}% |"
        )
    return lines


def key_hashes(paths: Iterable[Path]) -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): sha256(path)
        for path in paths
        if path.is_file()
    }


def main() -> None:
    output_root = ROOT / "results" / "comparison"
    output_root.mkdir(parents=True, exist_ok=False)
    frame = load_results()
    validation = validate_results(frame)
    frame.to_csv(
        output_root / "matched_task_predictions.tsv",
        sep="\t",
        index=False,
    )

    global_summary = summary_table(frame, ["method", "feature_view"])
    workflow_summary = summary_table(
        frame, ["workflow", "method", "feature_view"]
    )
    process_summary = summary_table(
        frame, ["workflow", "process", "method", "feature_view"]
    )
    tail = heavy_tail_rows(frame)
    tail_summary = summary_table(
        tail, ["workflow", "method", "feature_view"]
    )
    global_summary.to_csv(output_root / "global_metrics.csv", index=False)
    workflow_summary.to_csv(output_root / "workflow_metrics.csv", index=False)
    process_summary.to_csv(output_root / "process_metrics.csv", index=False)
    tail_summary.to_csv(
        output_root / "workflow_heavy_tail_q95_metrics.csv", index=False
    )

    comparisons = (
        (
            ("Sizey", "native_A_with_peak_feedback"),
            ("CAMP", "A+P+C"),
        ),
        (("CAMP", "A+P"), ("CAMP", "A+P+C")),
        (("CAMP", "A"), ("CAMP", "A+P")),
    )
    records = []
    for left, right in comparisons:
        records.extend(paired_bootstrap(frame, left, right))
    pd.DataFrame.from_records(records).to_csv(
        output_root / "paired_bootstrap_95ci.csv", index=False
    )

    validation_path = output_root / "validation_manifest.json"
    validation_path.write_text(
        json.dumps(validation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run_manifest_path = ROOT / "results" / f"seed_{SEED}" / "run_manifest.json"
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    report = [
        "# Fresh Sizey vs CAMP APC-Only Comparison",
        "",
        "## Scope",
        "",
        "- Seed: `1996`",
        "- Matched cohort: 30,478 tasks; 9,141 initial and 21,337 test.",
        "- Split: exact unchanged Sizey per-process 30%/70% assignments.",
        "- Sizey: unchanged commit and immutable prior raw predictions.",
        "- CAMP: fresh model fit, calibration, predictions, and summaries.",
        "- CAMP test labels are revealed only after each causal decision.",
        "- OOM outcomes are simulated as `allocation < observed peak RSS`.",
        "- Final CAMP allocation is direct `A+P+C`; it has no `A` or `A+P` "
        "allocation floor.",
        "",
        "## Global Results",
        "",
        *markdown_table(global_summary),
        "",
        "## Evidence",
        "",
        "- `matched_task_predictions.tsv`: all four methods on identical tasks.",
        "- `workflow_metrics.csv`: workflow-level metrics.",
        "- `process_metrics.csv`: process-level metrics.",
        "- `workflow_heavy_tail_q95_metrics.csv`: workflow-local q95 peak tasks.",
        "- `paired_bootstrap_95ci.csv`: paired task-level uncertainty.",
        "- `validation_manifest.json`: task parity and APC-only assertions.",
        "",
        "## Method Boundary",
        "",
        "CAMP model weights are fitted on the initial 30% and frozen during the "
        "70% replay. Causal `P`, `C`, and residual histories are updated after "
        "each revealed test task. Sizey retains its native sequential behavior.",
        "",
    ]
    (output_root / "RESULTS.md").write_text(
        "\n".join(report), encoding="utf-8"
    )

    key_paths = [
        ROOT / "config" / "experiment.json",
        ROOT / "inputs" / "canonical_tasks.tsv",
        ROOT / "manifests" / "preflight_manifest.json",
        run_manifest_path,
        validation_path,
        output_root / "matched_task_predictions.tsv",
        output_root / "global_metrics.csv",
        output_root / "workflow_metrics.csv",
        output_root / "process_metrics.csv",
        output_root / "workflow_heavy_tail_q95_metrics.csv",
        output_root / "paired_bootstrap_95ci.csv",
        output_root / "RESULTS.md",
    ]
    completion = {
        "status": "complete",
        "experiment": "sizey_vs_camp_fresh_apc_only_20260805_v2",
        "seed": SEED,
        "initial_rows": run_manifest["initial_rows"],
        "test_rows": run_manifest["test_rows"],
        "variants": run_manifest["variants"],
        "apc_uses_a_or_ap_allocation_floor": False,
        "test_labels_used_for_model_training": False,
        "validation": validation,
        "key_artifact_sha256": key_hashes(key_paths),
    }
    (ROOT / "manifests" / "completion_manifest.json").write_text(
        json.dumps(completion, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(global_summary.to_string(index=False))
    print(f"wrote {output_root / 'RESULTS.md'}")


if __name__ == "__main__":
    main()
