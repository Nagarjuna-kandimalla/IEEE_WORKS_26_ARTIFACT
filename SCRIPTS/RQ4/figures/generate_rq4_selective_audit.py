#!/usr/bin/env python3
"""Generate the RQ4 risk-target selective-auditing figure."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ARTIFACT_ROOT = Path(__file__).resolve().parents[3]
INPUT = ARTIFACT_ROOT / "RESULTS" / "RQ4" / "csv"
OUTPUT = ARTIFACT_ROOT / "RESULTS" / "RQ4" / "figures"

CAMP = "#16856B"
RANDOM = "#9AA5AD"
STRATIFIED = "#4C78A8"
GRID = "#DDE3E7"
TEXT = "#1E2A33"


def configure() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.2,
            "axes.titlesize": 9.2,
            "axes.labelsize": 8.2,
            "xtick.labelsize": 7.4,
            "ytick.labelsize": 7.4,
            "legend.fontsize": 7.2,
            "axes.edgecolor": "#89959E",
            "axes.labelcolor": TEXT,
            "xtick.color": TEXT,
            "ytick.color": TEXT,
            "text.color": TEXT,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "savefig.bbox": "tight",
        }
    )


def finish(fig: plt.Figure) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT / "fig_rq4_selective_audit.png", dpi=300)
    fig.savefig(OUTPUT / "fig_rq4_selective_audit.pdf")
    plt.close(fig)


def panel_label(
    ax: plt.Axes,
    label: str,
    *,
    x: float = -0.13,
    y: float = 1.16,
) -> None:
    ax.text(
        x,
        y,
        label,
        transform=ax.transAxes,
        fontsize=9.2,
        fontweight="bold",
        va="top",
    )


def clean_axis(ax: plt.Axes) -> None:
    ax.grid(axis="y", color=GRID, linewidth=0.65, alpha=0.9)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def replay_curve(
    ax: plt.Axes,
    repetitions: pd.DataFrame,
    scenario: str,
    metric: str,
    title: str,
) -> None:
    subset = repetitions[
        (repetitions["scenario"] == scenario)
        & (repetitions["budget"] <= 0.5)
    ]
    styles = [
        ("three_gate", "Three-gate", CAMP, "o"),
        ("uniform_random", "Uniform random", RANDOM, "s"),
        (
            "process_stratified_random",
            "Process-stratified",
            STRATIFIED,
            "^",
        ),
    ]
    for policy, label, color, marker in styles:
        policy_rows = subset[subset["policy"] == policy]
        grouped = policy_rows.groupby("budget")[metric]
        x = np.array(sorted(grouped.groups)) * 100
        means = grouped.mean().reindex(x / 100).to_numpy() * 100
        low = grouped.quantile(0.025).reindex(x / 100).to_numpy() * 100
        high = grouped.quantile(0.975).reindex(x / 100).to_numpy() * 100
        ax.plot(
            x,
            means,
            color=color,
            marker=marker,
            linewidth=1.6,
            markersize=3.8,
            label=label,
        )
        if policy != "three_gate":
            ax.fill_between(
                x,
                low,
                high,
                color=color,
                alpha=0.12,
                linewidth=0,
            )
    ax.plot(
        [5, 50],
        [5, 50],
        color="#BCC4CA",
        linestyle=":",
        linewidth=1,
    )
    ax.set_xticks([5, 10, 20, 30, 50])
    ax.set_xlabel("Audit budget (% of tasks)")
    ax.set_ylabel("Recall (%)")
    ax.set_title(title)
    ax.set_xlim(3, 52)
    clean_axis(ax)


def main() -> None:
    configure()
    repetitions = pd.read_csv(INPUT / "budget_repetitions.csv.gz")
    assert set(repetitions["repetition"]) == set(range(1000))
    required_policies = {
        "three_gate",
        "uniform_random",
        "process_stratified_random",
    }
    ap_rows = repetitions[repetitions["scenario"] == "a_plus_p"]
    assert set(ap_rows["policy"]) == required_policies
    assert set(ap_rows["budget"]) >= {0.05, 0.10, 0.20, 0.30, 0.50}

    fig, ax = plt.subplots(figsize=(3.665, 2.65))
    replay_curve(
        ax,
        repetitions,
        "a_plus_p",
        "underallocation_recall",
        "Risk targets found",
    )
    panel_label(ax, "(a)", x=-0.16)
    fig.subplots_adjust(
        left=0.18,
        right=0.98,
        top=0.83,
        bottom=0.2,
    )
    finish(fig)
    print(f"Generated RQ4 figure in {OUTPUT}")


if __name__ == "__main__":
    main()
