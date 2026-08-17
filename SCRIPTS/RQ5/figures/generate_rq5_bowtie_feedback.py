#!/usr/bin/env python3
"""Generate the RQ5 Bowtie2 cold-to-warm online-feedback figure."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ARTIFACT = Path(__file__).resolve().parents[3]
INPUT = ARTIFACT / "RESULTS" / "RQ5" / "csv"
OUTPUT = ARTIFACT / "RESULTS" / "RQ5" / "figures"

CAMP = "#16856B"
PEAK = "#D0D6DB"
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


def panel_label(ax: plt.Axes, label: str, *, y: float = 1.40) -> None:
    ax.text(
        -0.13,
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


def load_tasks(phase: str) -> pd.DataFrame:
    path = INPUT / f"bowtie2_{phase}_task_instances.tsv.gz"
    frame = pd.read_csv(path, sep="\t", compression="gzip", low_memory=False)
    if len(frame) != 6_000 or frame["task_key"].duplicated().any():
        raise ValueError(f"{path}: expected 6,000 unique logical tasks")
    required = {
        "first_applied_allocation_mib",
        "final_peak_mib",
        "final_runtime_seconds",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path}: missing columns: {sorted(missing)}")
    return frame


def metrics(tasks: pd.DataFrame) -> dict[str, float]:
    request = tasks["first_applied_allocation_mib"].astype(float)
    peak = tasks["final_peak_mib"].astype(float)
    runtime = tasks["final_runtime_seconds"].astype(float)
    wastage = ((request - peak).clip(lower=0) * runtime / 3600 / 1024).sum()
    return {
        "request_gib": float(request.sum() / 1024),
        "peak_gib": float(peak.sum() / 1024),
        "wastage_gibh": float(wastage),
    }


def allocation_margin_shares(
    tasks: pd.DataFrame,
) -> tuple[list[str], np.ndarray]:
    request = tasks["first_applied_allocation_mib"].astype(float).to_numpy()
    peak = tasks["final_peak_mib"].astype(float).to_numpy()
    ratio = request / np.maximum(peak, 1e-9)
    masks = (
        ratio < 1.0,
        (ratio >= 1.0) & (ratio <= 1.05),
        (ratio > 1.05) & (ratio <= 1.10),
        (ratio > 1.10) & (ratio <= 1.25),
        ratio > 1.25,
    )
    labels = ["Below peak", "0-5%", "5-10%", "10-25%", ">25%"]
    shares = np.asarray([100.0 * mask.mean() for mask in masks], dtype=float)
    if not np.isclose(shares.sum(), 100.0):
        raise ValueError("allocation-margin buckets do not cover every task")
    return labels, shares


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--new-run",
        action="store_true",
        help="plot a fresh cold/warm run without checking retained numeric values",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure()
    cold_tasks = load_tasks("cold")
    warm_tasks = load_tasks("warm")
    cold = metrics(cold_tasks)
    warm = metrics(warm_tasks)
    margin_labels, cold_margins = allocation_margin_shares(cold_tasks)
    _, warm_margins = allocation_margin_shares(warm_tasks)

    if not args.new_run:
        if not np.isclose(cold["request_gib"], 898.3857, atol=0.01):
            raise ValueError("cold requested-capacity contract changed")
        if not np.isclose(warm["request_gib"], 251.9268, atol=0.01):
            raise ValueError("warm requested-capacity contract changed")
        if not np.isclose(cold_margins[-1], 16.7333, atol=0.01):
            raise ValueError("cold margin distribution changed")
        if not np.isclose(warm_margins[-1], 0.2667, atol=0.01):
            raise ValueError("warm margin distribution changed")

    fig, axes = plt.subplots(1, 3, figsize=(7.15, 2.55))
    x = np.arange(2)

    ax = axes[0]
    width = 0.34
    peak_bars = ax.bar(
        x - width / 2,
        [cold["peak_gib"], warm["peak_gib"]],
        width,
        color=PEAK,
        edgecolor="#AAB4BB",
        label="Observed peak",
    )
    request_bars = ax.bar(
        x + width / 2,
        [cold["request_gib"], warm["request_gib"]],
        width,
        color=CAMP,
        label="CAMP request",
    )
    ax.bar_label(peak_bars, fmt="%.1f", padding=2, fontsize=6.7)
    ax.bar_label(request_bars, fmt="%.1f", padding=2, fontsize=6.7)
    ax.set_xticks(x, ["Cold", "Warm"])
    ax.set_ylabel("Aggregate memory (GiB)")
    ax.set_title("Requested capacity", pad=30)
    ax.legend(
        frameon=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=2,
        fontsize=6.4,
        columnspacing=0.9,
    )
    clean_axis(ax)
    panel_label(ax, "(a)")

    ax = axes[1]
    bars = ax.bar(
        ["Cold", "Warm"],
        [cold["wastage_gibh"], warm["wastage_gibh"]],
        color=CAMP,
        width=0.6,
    )
    ax.set_yscale("log")
    ax.set_ylim(0.008, 60)
    ax.bar_label(
        bars,
        labels=[f"{cold['wastage_gibh']:.2f}", f"{warm['wastage_gibh']:.3f}"],
        padding=3,
        fontsize=7,
        fontweight="bold",
    )
    ax.set_ylabel("Unused memory-time (GiB-h, log)")
    ax.set_title("Reservation wastage", pad=30)
    clean_axis(ax)
    panel_label(ax, "(b)")

    ax = axes[2]
    margin_colors = ["#C65E4A", "#16856B", "#59A99A", "#6E91A7", "#9AA5AD"]
    bottom = np.zeros(2, dtype=float)
    for label, color, cold_share, warm_share in zip(
        margin_labels, margin_colors, cold_margins, warm_margins
    ):
        values = np.asarray([cold_share, warm_share], dtype=float)
        bars = ax.bar(x, values, bottom=bottom, color=color, width=0.62, label=label)
        for bar, value, offset in zip(bars, values, bottom):
            if value >= 6.0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    offset + value / 2,
                    f"{value:.1f}%",
                    ha="center",
                    va="center",
                    fontsize=6.3,
                    color="white" if color in {"#16856B", "#6E91A7"} else TEXT,
                    fontweight="bold",
                )
        bottom += values
    ax.set_xticks(x, ["Cold", "Warm"])
    ax.set_ylim(0, 100)
    ax.set_ylabel("Tasks (%)")
    ax.set_title("First-attempt margin", pad=30)
    clean_axis(ax)
    panel_label(ax, "(c)")
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        frameon=False,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.02),
        ncol=5,
        fontsize=6.2,
        columnspacing=0.85,
        handlelength=1.1,
    )

    fig.subplots_adjust(left=0.08, right=0.995, top=0.78, bottom=0.30, wspace=0.43)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT / "fig_rq5_bowtie_feedback.png", dpi=300)
    fig.savefig(OUTPUT / "fig_rq5_bowtie_feedback.pdf")
    plt.close(fig)
    print(f"generated={OUTPUT / 'fig_rq5_bowtie_feedback.png'}")
    print(f"generated={OUTPUT / 'fig_rq5_bowtie_feedback.pdf'}")


if __name__ == "__main__":
    main()
