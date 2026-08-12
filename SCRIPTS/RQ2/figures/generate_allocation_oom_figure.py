#!/usr/bin/env python3
"""Generate the RQ2 A+P versus A+P+C allocation figure."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ARTIFACT_ROOT = Path(__file__).resolve().parents[3]
DATA = ARTIFACT_ROOT / "RESULTS" / "RQ2" / "csv"
OUTPUT = ARTIFACT_ROOT / "RESULTS" / "RQ2" / "figures"

ACCESS = "#697782"
CAMP = "#16856B"
RANDOM = "#9AA5AD"
STRATIFIED = "#4C78A8"
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


def load_offline() -> tuple[pd.DataFrame, pd.DataFrame]:
    access = pd.read_csv(DATA / "a_plus_p_task_predictions.tsv.gz", sep="\t")
    camp = pd.read_csv(
        DATA / "a_plus_p_plus_c_task_predictions.tsv.gz", sep="\t"
    )
    assert len(access) == len(camp) == 5026
    return access, camp


def allocation_metrics(frame: pd.DataFrame) -> dict[str, float]:
    request = frame["first_allocation_mib"].astype(float)
    peak = frame["actual_peak_mib"].astype(float)
    runtime = frame["runtime_seconds"].astype(float)
    valid_runtime = runtime.notna()
    wastage = (
        (request[valid_runtime] - peak[valid_runtime]).clip(lower=0)
        * runtime[valid_runtime]
        / 3600
        / 1024
    ).sum()
    return {
        "under": float((request < peak).sum()),
        "coverage": float((request >= peak).mean()),
        "requested_gib": float(request.sum() / 1024),
        "peak_gib": float(peak.sum() / 1024),
        "wastage_gibh": float(wastage),
    }


def rq2_figure() -> None:
    access, camp = load_offline()
    am = allocation_metrics(access)
    cm = allocation_metrics(camp)
    assert int(am["under"]) == 87 and int(cm["under"]) == 31
    assert np.isclose(cm["wastage_gibh"] / am["wastage_gibh"], 0.6282, atol=5e-4)

    workflows = [
        "GATK",
        "Minimap2",
        "Sarek",
        "Seqinspector",
        "Taxprofiler",
        "Viralmetagenome",
    ]
    keys = [name.lower() for name in workflows]
    workflow_labels = [
        "GATK",
        "Minimap2",
        "Sarek",
        "Seqinspector",
        "Taxprofiler",
        "Viralmeta.",
    ]
    access_under = [
        int((group["first_allocation_mib"] < group["actual_peak_mib"]).sum())
        for key in keys
        for group in [access[access["workflow"].str.lower() == key]]
    ]
    camp_under = [
        int((group["first_allocation_mib"] < group["actual_peak_mib"]).sum())
        for key in keys
        for group in [camp[camp["workflow"].str.lower() == key]]
    ]

    fig, axes = plt.subplots(
        1,
        3,
        figsize=(7.15, 2.55),
        gridspec_kw={"width_ratios": [0.8, 1.55, 1.0]},
    )

    ax = axes[0]
    bars = ax.bar(
        ["Access", "CAMP"],
        [am["under"], cm["under"]],
        color=[ACCESS, CAMP],
        width=0.62,
    )
    ax.bar_label(bars, fmt="%.0f", padding=2, fontsize=8, fontweight="bold")
    ax.set_ylim(0, 100)
    ax.set_ylabel("Underallocated tasks")
    ax.set_title("First-attempt safety")
    clean_axis(ax)
    panel_label(ax, "(a)", x=-0.25)

    ax = axes[1]
    y = np.arange(len(workflow_labels))
    height = 0.34
    ax.barh(y + height / 2, access_under, height, color=ACCESS, label="Access")
    ax.barh(y - height / 2, camp_under, height, color=CAMP, label="CAMP")
    for yi, value in zip(y + height / 2, access_under):
        ax.text(value + 0.5, yi, str(value), va="center", fontsize=6.8)
    for yi, value in zip(y - height / 2, camp_under):
        ax.text(value + 0.5, yi, str(value), va="center", fontsize=6.8)
    ax.set_yticks(y, workflow_labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 32)
    ax.set_xlabel("Underallocated tasks")
    ax.set_title("Workflow breakdown")
    ax.legend(frameon=False, loc="upper right")
    clean_axis(ax, axis="x")
    panel_label(ax, "(b)")

    ax = axes[2]
    labels = ["Requested\nmemory", "Unused\nmemory-time"]
    access_norm = np.array([100.0, 100.0])
    camp_norm = np.array(
        [
            100 * cm["requested_gib"] / am["requested_gib"],
            100 * cm["wastage_gibh"] / am["wastage_gibh"],
        ]
    )
    x = np.arange(2)
    width = 0.34
    first = ax.bar(x - width / 2, access_norm, width, color=ACCESS, label="Access")
    second = ax.bar(x + width / 2, camp_norm, width, color=CAMP, label="CAMP")
    ax.bar_label(first, labels=["100", "100"], padding=2, fontsize=6.8)
    ax.bar_label(second, fmt="%.1f", padding=2, fontsize=6.8)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Relative to Access (%)")
    ax.set_ylim(0, 150)
    ax.set_title("Capacity trade-off")
    ax.legend(frameon=False, loc="upper right")
    clean_axis(ax)
    panel_label(ax, "(c)")

    fig.subplots_adjust(left=0.07, right=0.995, top=0.82, bottom=0.22, wspace=0.47)
    finish(fig, "fig_rq2_allocation_tradeoff")


def main() -> None:
    configure()
    rq2_figure()


if __name__ == "__main__":
    main()

