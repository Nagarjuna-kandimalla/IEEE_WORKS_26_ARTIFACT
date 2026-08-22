#!/usr/bin/env python3
"""Generate the Sizey-versus-CAMP paper figure."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ARTIFACT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ARTIFACT_ROOT / "RESULTS" / "RQ3" / "figures"
SIZEY = ARTIFACT_ROOT / "RESULTS" / "RQ3" / "csv"

CAMP = "#16856B"
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


def finish(fig: plt.Figure, name: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT / f"{name}.png", dpi=300)
    fig.savefig(OUTPUT / f"{name}.pdf")
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


def clean_axis(ax: plt.Axes, axis: str = "y") -> None:
    ax.grid(axis=axis, color=GRID, linewidth=0.65, alpha=0.9)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def sizey_figure() -> None:
    metrics = pd.read_csv(SIZEY / "global_metrics.csv")
    sizey = metrics[metrics["method"] == "Sizey"].iloc[0]
    camp = metrics[
        (metrics["method"] == "CAMP") & (metrics["feature_view"] == "A+P+C")
    ].iloc[0]
    assert int(sizey["initial_oom_tasks"]) == 3597
    assert int(camp["initial_oom_tasks"]) == 287

    fig, axes = plt.subplots(1, 3, figsize=(7.15, 2.55))
    colors = [STRATIFIED, CAMP]

    ax = axes[0]
    bars = ax.bar(
        ["Sizey", "CAMP"],
        [sizey["initial_oom_tasks"], camp["initial_oom_tasks"]],
        color=colors,
        width=0.62,
    )
    ax.bar_label(bars, fmt="%.0f", padding=2, fontsize=7.2, fontweight="bold")
    ax.set_ylim(0, 4000)
    ax.set_ylabel("Underallocated tasks")
    ax.set_title("First-attempt safety")
    clean_axis(ax)
    panel_label(ax, "(a)")

    ax = axes[1]
    median_ape = [100 * sizey["median_ape"], 100 * camp["median_ape"]]
    bars = ax.bar(["Sizey", "CAMP"], median_ape, color=colors, width=0.62)
    ax.bar_label(bars, fmt="%.2f%%", padding=2, fontsize=7.2, fontweight="bold")
    ax.set_ylim(0, 32)
    ax.set_ylabel("Median absolute percentage error")
    ax.set_title("Point accuracy")
    clean_axis(ax)
    panel_label(ax, "(b)")

    ax = axes[2]
    labels = ["Request /\npeak", "Unused\nmemory-time"]
    sizey_norm = np.array([100.0, 100.0])
    camp_norm = np.array(
        [
            100 * camp["request_to_peak_ratio"] / sizey["request_to_peak_ratio"],
            100
            * camp["first_attempt_wastage_gib_hours"]
            / sizey["first_attempt_wastage_gib_hours"],
        ]
    )
    x = np.arange(2)
    width = 0.34
    first = ax.bar(x - width / 2, sizey_norm, width, color=STRATIFIED, label="Sizey")
    second = ax.bar(x + width / 2, camp_norm, width, color=CAMP, label="CAMP")
    ax.bar_label(first, labels=["100", "100"], padding=2, fontsize=6.8)
    ax.bar_label(second, fmt="%.1f", padding=2, fontsize=6.8)
    ax.set_xticks(x, labels)
    ax.set_ylim(0, 250)
    ax.set_ylabel("Relative to Sizey (%)")
    ax.set_title("Capacity cost")
    clean_axis(ax)
    ax.legend(frameon=False, loc="upper right")
    panel_label(ax, "(c)")

    fig.subplots_adjust(left=0.08, right=0.995, top=0.82, bottom=0.22, wspace=0.43)
    finish(fig, "fig_sizey_vs_camp")


def main() -> None:
    configure()
    sizey_figure()
    print(f"Generated Sizey-versus-CAMP figure in {OUTPUT}")


if __name__ == "__main__":
    main()
