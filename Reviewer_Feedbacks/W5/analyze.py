#!/usr/bin/env python3
"""Reproduce the paper-aligned audit-cost net-benefit accounting."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
BUDGETS = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.50, 1.00)
SEEDS = (1996, 1997, 1998, 1999, 2000)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--selections",
        type=Path,
        default=ROOT / "inputs/audit_selections.tsv.gz",
    )
    parser.add_argument(
        "--task-costs",
        type=Path,
        default=ROOT / "inputs/task_level_full_ebpf_net_benefit.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results")
    return parser.parse_args()


def bootstrap_seed_mean(values: np.ndarray, seed: int = 73129) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(10_000, len(values)), replace=True).mean(axis=1)
    return tuple(np.quantile(means, [0.025, 0.975]))


def load_inputs(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame]:
    selections = pd.read_csv(args.selections, sep="\t", low_memory=False)
    expected_rows = len(BUDGETS) * len(SEEDS) * 5026
    if len(selections) != expected_rows:
        raise ValueError(f"Expected {expected_rows} selection rows; found {len(selections)}")
    observed_groups = set(zip(selections.budget, selections.seed))
    expected_groups = {(budget, seed) for budget in BUDGETS for seed in SEEDS}
    if observed_groups != expected_groups:
        raise ValueError("Budget/seed groups do not match the fixed experiment contract")
    for key, frame in selections.groupby(["budget", "seed"], sort=True):
        if len(frame) != 5026 or not frame.logical_task_id.is_unique:
            raise ValueError(f"Invalid held-out population for {key}")

    costs = pd.read_csv(args.task_costs, low_memory=False)[
        [
            "logical_task_id",
            "workflow",
            "positive_runtime_delta_seconds",
            "audit_runtime_available",
            "camp_request_mib",
            "camp_unused_gib_hours",
            "camp_underallocated",
            "unused_gib_hours_saved",
            "audit_cost_positive_gib_hours",
            "paper_runtime_available",
        ]
    ]
    if len(costs) != 5026 or not costs.logical_task_id.is_unique:
        raise ValueError("The paired cost table must contain 5,026 unique tasks")
    return selections, costs


def main() -> None:
    args = arguments()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    selections, costs = load_inputs(args)
    records = []
    task_records = []

    for (budget, seed), selected in selections.groupby(["budget", "seed"], sort=True):
        frame = selected.merge(
            costs, on=["logical_task_id", "workflow"], validate="one_to_one"
        )
        audited = frame.audited.astype(bool)
        if not frame.loc[audited, "audit_runtime_available"].all():
            raise ValueError(f"Missing paired audit runtime for budget={budget}, seed={seed}")
        frame["paper_audit_cost_gib_hours"] = (
            frame.audit_cost_positive_gib_hours * audited
        )
        frame["paper_net_contribution_gib_hours"] = (
            frame.unused_gib_hours_saved - frame.paper_audit_cost_gib_hours
        )
        records.append(
            {
                "budget": budget,
                "seed": seed,
                "test_tasks": len(frame),
                "audited_test_tasks": int(audited.sum()),
                "paper_test_underallocations": int(frame.camp_underallocated.sum()),
                "paper_test_coverage_percent": float(100 * (~frame.camp_underallocated).mean()),
                "paper_camp_unused_gib_hours": float(frame.camp_unused_gib_hours.sum()),
                "paper_allocation_savings_gib_hours": float(frame.unused_gib_hours_saved.sum()),
                "paper_audit_cost_gib_hours": float(frame.paper_audit_cost_gib_hours.sum()),
                "paper_net_gib_hours": float(frame.paper_net_contribution_gib_hours.sum()),
                "missing_audit_runtimes": int((~frame.audit_runtime_available).sum()),
                "missing_savings_runtimes": int((~frame.paper_runtime_available).sum()),
            }
        )
        task_records.append(
            frame[
                [
                    "budget",
                    "seed",
                    "logical_task_id",
                    "workflow",
                    "process",
                    "batch",
                    "audited",
                    "audit_lane",
                    "positive_runtime_delta_seconds",
                    "camp_request_mib",
                    "unused_gib_hours_saved",
                    "paper_audit_cost_gib_hours",
                    "paper_net_contribution_gib_hours",
                ]
            ]
        )

    per_seed = pd.DataFrame(records).sort_values(["budget", "seed"])
    summary_rows = []
    for budget, group in per_seed.groupby("budget", sort=True):
        net = group.paper_net_gib_hours.to_numpy(float)
        low, high = bootstrap_seed_mean(net)
        summary_rows.append(
            {
                "budget_percent": int(round(100 * budget)),
                "audited_test_tasks": int(group.audited_test_tasks.iloc[0]),
                "sampling_seeds": len(group),
                "paper_test_underallocations": int(group.paper_test_underallocations.iloc[0]),
                "paper_test_coverage_percent": group.paper_test_coverage_percent.iloc[0],
                "paper_camp_unused_gib_hours": group.paper_camp_unused_gib_hours.mean(),
                "paper_allocation_savings_gib_hours": group.paper_allocation_savings_gib_hours.mean(),
                "paper_mean_audit_cost_gib_hours": group.paper_audit_cost_gib_hours.mean(),
                "paper_mean_net_gib_hours": net.mean(),
                "net_seed_mean_ci95_low": low,
                "net_seed_mean_ci95_high": high,
                "missing_audit_runtimes": int(group.missing_audit_runtimes.max()),
                "missing_savings_runtimes": int(group.missing_savings_runtimes.max()),
            }
        )

    summary = pd.DataFrame(summary_rows)
    per_seed.to_csv(args.output_dir / "per_seed_net_benefit.csv", index=False)
    summary.to_csv(args.output_dir / "budget_summary.csv", index=False)
    pd.concat(task_records, ignore_index=True).to_csv(
        args.output_dir / "task_level_net_benefit.csv.gz",
        index=False,
        compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
    )
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
