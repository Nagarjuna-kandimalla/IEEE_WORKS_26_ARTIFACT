#!/usr/bin/env python3
"""Create the RQ1 consumed-data visibility and full-audit overhead figure."""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd


ARTIFACT_ROOT = Path(__file__).resolve().parents[3]
RQ1_RESULTS = ARTIFACT_ROOT / "RESULTS" / "RQ1"
SIGNAL_SOURCE = RQ1_RESULTS / "csv" / "strace_vs_ebpf_workflow_summary.csv"
OVERHEAD_SOURCE = RQ1_RESULTS / "csv" / "rq1_workflow_overhead.csv"
FIGURES = RQ1_RESULTS / "figures"

WORKFLOW_ORDER = [
    "gatk",
    "minimap2",
    "sarek",
    "seqinspector",
    "taxprofiler",
    "viralmetagenome",
]
WORKFLOW_LABELS = {
    "gatk": "GATK",
    "minimap2": "Minimap2",
    "sarek": "Sarek",
    "seqinspector": "Seqinspector",
    "taxprofiler": "Taxprofiler",
    "viralmetagenome": "Viralmetagenome",
}

INK = "#17212B"
MUTED = "#5F6B76"
GRID = "#DDE3E8"
READ = "#4477AA"
MMAP = "#EE6677"
STRACE = "#66717C"
EBPF = "#228B73"


def configure_style() -> None:
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.2,
            "axes.titlesize": 9.2,
            "axes.labelsize": 8.2,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.8,
            "legend.fontsize": 7.4,
            "axes.edgecolor": "#AAB4BD",
            "axes.linewidth": 0.7,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.04,
        }
    )


def load_signal_data() -> pd.DataFrame:
    data = (
        pd.read_csv(SIGNAL_SOURCE)
        .set_index("workflow")
        .loc[WORKFLOW_ORDER]
        .reset_index()
    )
    required = [
        "strace_read_gib",
        "ebpf_read_gib",
        "ebpf_mmap_page_fault_gib",
        "ebpf_total_gib",
        "ebpf_mmap_byte_share_pct",
        "ebpf_total_vs_strace_increase_pct",
    ]
    if data[required].isna().any().any():
        raise ValueError("RQ1 source contains missing consumed-data values")
    return data


def load_overhead_data() -> pd.DataFrame:
    data = (
        pd.read_csv(OVERHEAD_SOURCE)
        .set_index("workflow")
        .loc[WORKFLOW_ORDER]
        .reset_index()
    )
    required = [
        "baseline_minutes",
        "strace_minutes",
        "ebpf_minutes",
        "strace_overhead_pct",
        "ebpf_overhead_pct",
        "timing_basis",
    ]
    if data[required[:-1]].isna().any().any():
        raise ValueError("RQ1 source contains missing workflow-overhead values")
    return data


def make_figure(signal: pd.DataFrame, overhead: pd.DataFrame) -> plt.Figure:
    y = np.arange(len(signal))
    labels = [WORKFLOW_LABELS[value] for value in signal["workflow"]]
    mmap_share = signal["ebpf_mmap_byte_share_pct"].to_numpy()
    read_share = 100.0 - mmap_share

    fig, (composition, comparison) = plt.subplots(
        1,
        2,
        figsize=(7.15, 3.05),
        sharey=False,
        gridspec_kw={"width_ratios": [1.15, 1.0], "wspace": 0.36},
    )

    # Panel A: normalize within each eBPF observation so mmap visibility remains
    # readable even though absolute workflow volumes span almost two orders.
    composition.barh(
        y,
        read_share,
        height=0.52,
        color=READ,
        edgecolor="white",
        linewidth=0.5,
        label="Explicit reads",
    )
    composition.barh(
        y,
        mmap_share,
        left=read_share,
        height=0.52,
        color=MMAP,
        edgecolor="white",
        linewidth=0.5,
        label="mmap page faults",
    )
    for row, share, gib in zip(
        y,
        mmap_share,
        signal["ebpf_mmap_page_fault_gib"],
    ):
        composition.text(
            102.0,
            row,
            f"{gib:,.1f} GiB  ({share:.1f}%)",
            ha="left",
            va="center",
            fontsize=7.0,
            color=INK,
        )
    composition.set_xlim(0, 145)
    composition.set_xticks([0, 25, 50, 75, 100])
    composition.set_xlabel("Share of eBPF consumed-data signal (%)")
    composition.set_yticks(y, labels)
    composition.invert_yaxis()
    composition.set_title(
        "(a) eBPF reveals mmap-backed consumption",
        loc="left",
        color=INK,
        fontweight="semibold",
    )
    composition.grid(axis="x", color=GRID, linewidth=0.6)
    composition.set_axisbelow(True)
    composition.tick_params(axis="y", length=0, colors=INK)
    composition.tick_params(axis="x", colors=MUTED)

    # Panel B: compare complete-workflow overhead at the same visual boundary.
    # Sarek is retained but explicitly marked because its active runtime is
    # reconstructed across resumed sessions.
    y_overhead = np.arange(len(overhead))
    bar_height = 0.32
    strace_overhead = overhead["strace_overhead_pct"].to_numpy()
    ebpf_overhead = overhead["ebpf_overhead_pct"].to_numpy()
    comparison.barh(
        y_overhead - bar_height / 2,
        strace_overhead,
        height=bar_height,
        color=STRACE,
        label="STRACE overhead",
    )
    comparison.barh(
        y_overhead + bar_height / 2,
        ebpf_overhead,
        height=bar_height,
        color=EBPF,
        label="eBPF overhead",
    )
    for row, value in zip(y_overhead, strace_overhead):
        comparison.text(
            value + 2.0,
            row - bar_height / 2,
            f"{value:.1f}%",
            ha="left",
            va="center",
            fontsize=7.0,
            color=INK,
        )
    for row, value in zip(y_overhead, ebpf_overhead):
        comparison.text(
            value + 2.0,
            row + bar_height / 2,
            f"{value:.1f}%",
            ha="left",
            va="center",
            fontsize=7.0,
            color=INK,
        )
    overhead_labels = [
        "GATK",
        "Minimap2",
        "Sarek*",
        "Seqinsp.",
        "Taxprof.",
        "Viralmeta.",
    ]
    comparison.set_yticks(y_overhead, overhead_labels)
    comparison.invert_yaxis()
    comparison.set_xlim(0, 160)
    comparison.set_xticks([0, 40, 80, 120, 160])
    comparison.set_xlabel("Runtime increase over no audit (%)")
    comparison.set_title(
        "(b) Full-audit workflow overhead",
        loc="left",
        color=INK,
        fontweight="semibold",
    )
    comparison.grid(axis="x", color=GRID, linewidth=0.6)
    comparison.set_axisbelow(True)
    comparison.tick_params(axis="y", length=0, colors=INK)
    comparison.tick_params(axis="x", colors=MUTED)
    comparison.text(
        0.0,
        -0.18,
        "* Sarek uses reconstructed active runtime across resumed sessions.",
        transform=comparison.transAxes,
        ha="left",
        va="top",
        fontsize=6.8,
        color=MUTED,
    )

    legend_items = [
        Line2D([0], [0], color=READ, linewidth=6, label="eBPF explicit reads"),
        Line2D([0], [0], color=MMAP, linewidth=6, label="eBPF mmap-fault bytes"),
        Line2D(
            [0],
            [0],
            color=STRACE,
            linewidth=6,
            label="STRACE overhead",
        ),
        Line2D(
            [0],
            [0],
            color=EBPF,
            linewidth=6,
            label="eBPF overhead",
        ),
    ]
    fig.legend(
        handles=legend_items,
        loc="upper center",
        bbox_to_anchor=(0.52, 1.035),
        ncol=4,
        frameon=False,
        columnspacing=1.2,
        handletextpad=0.5,
    )
    fig.subplots_adjust(top=0.82, bottom=0.18, left=0.14, right=0.98)
    return fig


def main() -> None:
    configure_style()
    FIGURES.mkdir(parents=True, exist_ok=True)
    signal = load_signal_data()
    overhead = load_overhead_data()
    figure = make_figure(signal, overhead)
    figure.savefig(FIGURES / "fig_rq1_signal_overhead.pdf")
    figure.savefig(FIGURES / "fig_rq1_signal_overhead.png", dpi=400)
    plt.close(figure)


if __name__ == "__main__":
    main()
