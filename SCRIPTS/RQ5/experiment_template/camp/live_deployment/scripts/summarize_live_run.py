#!/usr/bin/env python3
"""Consolidate CAMP decisions and outcomes for one live run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


VARIANTS = ("A", "A+P", "A+P+C")
VARIANT_LABELS = {
    "A": "a",
    "A+P": "a_plus_p",
    "A+P+C": "a_plus_p_plus_c",
}

ATTEMPT_EXPORT_COLUMNS = (
    "task_id",
    "workflow",
    "process",
    "version",
    "task_instance",
    "input_identity",
    "decision_time",
    "completion_time",
    "static_input_bytes",
    "c_hat_bytes",
    "history_scope",
    "history_support_count",
    "history_confidence",
    "model_version",
    "allocation_source",
    "a_point_q50_mb",
    "a_request_mb",
    "ap_point_q50_mb",
    "ap_request_mb",
    "camp_point_q50_mb",
    "camp_request_mb",
    "submitted_request_mb",
    "peak_memory_bytes",
    "consumed_bytes",
    "runtime_seconds",
    "oom_flag",
    "exit_status",
)


def nested(item: dict[str, Any], *keys: str) -> Any:
    value: Any = item
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def decision_record(path: Path) -> dict[str, Any]:
    item = json.loads(path.read_text(encoding="utf-8"))
    history = item["history_context"]
    variants = item["variant_predictions"]
    record = {
        "attempt_key": path.name.removesuffix(".json"),
        "task_id": item["task_id"],
        "task_key": item["task_key"],
        "workflow": item["workflow"],
        "process": item["process"],
        "version": item["version"],
        "task_instance": item["task_instance"],
        "input_identity": item["input_identity"],
        "attempt": int(item["attempt"]),
        "decision_time": item["decision_time"],
        "static_input_bytes": item["static_input_bytes"],
        "c_hat_bytes": item["c_hat_bytes"],
        "history_scope": history["selected_scope"],
        "history_support_count": history["support_count"],
        "history_confidence": history["confidence"],
        "model_version": item["model_version"],
        "allocation_source": item["allocation_source"],
        "workflow_cold": item["workflow_cold"],
        "applied_allocation_mib": item["recommended_allocation_mb"],
        "retry_floor_mib": item["retry_floor_mb"],
        "a_point_q50_mb": variants["A"]["point_q50_mb"],
        "a_request_mb": variants["A"]["allocation"]["request_mb"],
        "ap_point_q50_mb": variants["A+P"]["point_q50_mb"],
        "ap_request_mb": variants["A+P"]["allocation"]["request_mb"],
        "camp_point_q50_mb": variants["A+P+C"]["point_q50_mb"],
        "camp_request_mb": variants["A+P+C"]["allocation"]["request_mb"],
        "submitted_request_mb": item["recommended_allocation_mb"],
    }
    for variant in VARIANTS:
        label = VARIANT_LABELS[variant]
        prediction = item["variant_predictions"][variant]
        record[f"{label}_point_q50_mib"] = prediction["point_q50_mb"]
        record[f"{label}_q99_mib"] = prediction["q99_mb"]
        record[f"{label}_q995_mib"] = prediction["q995_mb"]
        record[f"{label}_counterfactual_allocation_mib"] = nested(
            prediction,
            "allocation",
            "request_mb",
        )
    return record


def outcome_record(path: Path) -> dict[str, Any]:
    item = json.loads(path.read_text(encoding="utf-8"))
    outcome = item["task_outcome"]
    return {
        "attempt_key": path.name.removesuffix(".outcome.json"),
        "completion_time": outcome["completion_time"],
        "peak_memory_bytes": outcome["peak_memory_bytes"],
        "consumed_bytes": outcome["consumed_bytes"],
        "runtime_seconds": outcome["runtime_seconds"],
        "oom_flag": bool(outcome["oom_flag"]),
        "exit_status": int(item["exit_status"]),
    }


def safe_ratio(
    numerator: pd.Series,
    denominator: pd.Series,
) -> float | None:
    valid = numerator.notna() & denominator.notna() & denominator.gt(0)
    if not valid.any():
        return None
    return float(numerator.loc[valid].sum() / denominator.loc[valid].sum())


def metrics(group: pd.DataFrame) -> dict[str, Any]:
    complete = group["final_peak_mib"].notna()
    successful = group["final_success"]
    result = {
        "task_instances": int(len(group)),
        "successful_task_instances": int(successful.sum()),
        "unfinished_or_failed_task_instances": int((~successful).sum()),
        "first_attempt_ooms": int(group["first_attempt_oom"].sum()),
        "total_oom_attempts": int(group["oom_attempts"].sum()),
        "retried_task_instances": int(group["retry_count"].gt(0).sum()),
        "total_retry_attempts": int(group["retry_count"].sum()),
        "observed_peak_task_instances": int(complete.sum()),
        "observed_consumed_task_instances": int(
            group["final_consumed_bytes"].notna().sum()
        ),
        "peak_rss_sum_mib": (
            float(group.loc[complete, "final_peak_mib"].sum())
            if complete.any()
            else None
        ),
        "peak_rss_median_mib": (
            float(group.loc[complete, "final_peak_mib"].median())
            if complete.any()
            else None
        ),
        "peak_rss_p95_mib": (
            float(group.loc[complete, "final_peak_mib"].quantile(0.95))
            if complete.any()
            else None
        ),
        "peak_rss_max_mib": (
            float(group.loc[complete, "final_peak_mib"].max())
            if complete.any()
            else None
        ),
        "consumed_bytes_sum": (
            int(group["final_consumed_bytes"].dropna().sum())
            if group["final_consumed_bytes"].notna().any()
            else None
        ),
    }
    actual = group["final_peak_mib"]
    for variant in VARIANTS:
        label = VARIANT_LABELS[variant]
        point = group[f"{label}_point_q50_mib"]
        allocation = group[
            f"{label}_counterfactual_allocation_mib"
        ]
        valid_point = complete & point.notna() & actual.gt(0)
        valid_allocation = complete & allocation.notna()
        result[f"{label}_point_median_ape_percent"] = (
            float(
                np.median(
                    np.abs(
                        point.loc[valid_point]
                        - actual.loc[valid_point]
                    )
                    / actual.loc[valid_point]
                    * 100.0
                )
            )
            if valid_point.any()
            else None
        )
        result[f"{label}_allocation_coverage"] = (
            float(
                np.mean(
                    allocation.loc[valid_allocation]
                    >= actual.loc[valid_allocation]
                )
            )
            if valid_allocation.any()
            else None
        )
        result[f"{label}_request_to_peak_ratio"] = safe_ratio(
            allocation,
            actual,
        )
    result["applied_first_request_coverage"] = (
        float(
            np.mean(
                group.loc[complete, "first_applied_allocation_mib"]
                >= actual.loc[complete]
            )
        )
        if complete.any()
        else None
    )
    result["applied_first_request_to_peak_ratio"] = safe_ratio(
        group["first_applied_allocation_mib"],
        actual,
    )
    return result


def display(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_report(summary: dict[str, Any]) -> str:
    overall = summary["global"]
    lines = [
        "# CAMP Live Run Analysis",
        "",
        f"- Run: `{summary['run_root']}`",
        f"- Decision attempts: {summary['decision_attempts']}",
        (
            "- Completed outcome attempts: "
            f"{summary['completed_outcome_attempts']}"
        ),
        (
            "- Successful task instances: "
            f"{overall['successful_task_instances']} / "
            f"{overall['task_instances']}"
        ),
        f"- First-attempt OOMs: {overall['first_attempt_ooms']}",
        f"- Total OOM attempts: {overall['total_oom_attempts']}",
        (
            "- Retried task instances: "
            f"{overall['retried_task_instances']}"
        ),
        "",
        "## Variant comparison",
        "",
        (
            "| Variant | q50 median APE (%) | Allocation coverage | "
            "Request / peak |"
        ),
        "|---|---:|---:|---:|",
    ]
    for variant in VARIANTS:
        label = VARIANT_LABELS[variant]
        lines.append(
            f"| {variant} | "
            f"{display(overall[f'{label}_point_median_ape_percent'])} | "
            f"{display(overall[f'{label}_allocation_coverage'])} | "
            f"{display(overall[f'{label}_request_to_peak_ratio'])} |"
        )
    lines.extend(
        [
            "",
            "## Process results",
            "",
            (
                "| Process | Tasks | OOMs | Retries | Peak p50 MiB | "
                "Peak p95 MiB | Peak max MiB | CAMP coverage | "
                "CAMP request / peak |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for process, values in summary["by_process"].items():
        lines.append(
            f"| {process} | {values['task_instances']} | "
            f"{values['total_oom_attempts']} | "
            f"{values['total_retry_attempts']} | "
            f"{display(values['peak_rss_median_mib'], 1)} | "
            f"{display(values['peak_rss_p95_mib'], 1)} | "
            f"{display(values['peak_rss_max_mib'], 1)} | "
            f"{display(values['a_plus_p_plus_c_allocation_coverage'])} | "
            f"{display(values['a_plus_p_plus_c_request_to_peak_ratio'])} |"
        )
    lines.extend(
        [
            "",
            "The applied live allocation is `A+P+C`. `A` and `A+P` are",
            "counterfactuals computed at the same prelaunch decision point.",
            "Infrastructure retries are retained and are not counted as OOMs.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    camp_root = args.run_root / "camp"
    output_dir = args.output_dir or (
        args.run_root / "results" / "metrics" / "camp"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    decisions = pd.DataFrame.from_records(
        [
            decision_record(path)
            for path in sorted((camp_root / "decisions").glob("*.json"))
        ]
    )
    if decisions.empty:
        raise SystemExit("run has no CAMP decision records")
    outcomes = pd.DataFrame.from_records(
        [
            outcome_record(path)
            for path in sorted(
                (camp_root / "task_outcomes").glob("*.outcome.json")
            )
        ]
    )
    attempts = decisions.merge(
        outcomes,
        on="attempt_key",
        how="left",
        validate="one_to_one",
    )
    attempts["peak_memory_mib"] = (
        pd.to_numeric(attempts["peak_memory_bytes"], errors="coerce")
        / 2**20
    )
    attempts.to_csv(
        output_dir / "task_attempts.tsv",
        sep="\t",
        index=False,
    )
    attempts.loc[:, ATTEMPT_EXPORT_COLUMNS].sort_values(
        ["decision_time", "task_id"],
        kind="mergesort",
    ).to_csv(
        output_dir / "task_attempts.csv",
        index=False,
    )

    task_rows = []
    identity_columns = [
        "workflow",
        "process",
        "task_instance",
        "input_identity",
    ]
    for _, group in attempts.groupby(
        identity_columns,
        sort=False,
        dropna=False,
    ):
        group = group.sort_values("attempt")
        first = group.iloc[0]
        successful = group.loc[group["exit_status"].eq(0)]
        final = successful.iloc[-1] if len(successful) else group.iloc[-1]
        row = {
            key: first[key]
            for key in (
                "task_id",
                "task_key",
                "workflow",
                "process",
                "task_instance",
                "input_identity",
                "static_input_bytes",
                "c_hat_bytes",
                "allocation_source",
                "workflow_cold",
                "applied_allocation_mib",
            )
        }
        row["first_applied_allocation_mib"] = first[
            "applied_allocation_mib"
        ]
        row["final_applied_allocation_mib"] = final[
            "applied_allocation_mib"
        ]
        row["final_peak_mib"] = final["peak_memory_mib"]
        row["final_consumed_bytes"] = final["consumed_bytes"]
        row["final_runtime_seconds"] = final["runtime_seconds"]
        row["first_attempt_oom"] = bool(first.get("oom_flag") == True)
        row["oom_attempts"] = int(group["oom_flag"].eq(True).sum())
        row["attempts_observed"] = int(group["exit_status"].notna().sum())
        row["retry_count"] = max(int(group["attempt"].max()) - 1, 0)
        row["final_success"] = bool(final.get("exit_status") == 0)
        for variant in VARIANTS:
            label = VARIANT_LABELS[variant]
            for suffix in (
                "point_q50_mib",
                "q99_mib",
                "q995_mib",
                "counterfactual_allocation_mib",
            ):
                row[f"{label}_{suffix}"] = first[
                    f"{label}_{suffix}"
                ]
        task_rows.append(row)
    tasks = pd.DataFrame.from_records(task_rows)
    tasks.to_csv(
        output_dir / "task_instances.tsv",
        sep="\t",
        index=False,
    )

    summary = {
        "run_root": str(args.run_root.resolve()),
        "decision_attempts": int(len(decisions)),
        "completed_outcome_attempts": int(
            attempts["exit_status"].notna().sum()
        ),
        "global": metrics(tasks),
        "by_process": {
            str(process): metrics(group)
            for process, group in tasks.groupby("process")
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "ANALYSIS.md").write_text(
        render_report(summary),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
