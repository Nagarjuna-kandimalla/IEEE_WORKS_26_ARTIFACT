#!/usr/bin/env python3
"""Generate the RQ2 chronological split-testing figure."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import t


ROOT = Path(__file__).resolve().parents[3]
INPUT = ROOT / "RESULTS" / "RQ2" / "csv"
OUTPUT = ROOT / "RESULTS" / "RQ2" / "figures"
REPETITIONS = INPUT / "route_contract_repetition_effects.csv"

SPLITS = ["30/70", "50/50", "70/30", "85/15", "90/10"]
ACCESS = "#697782"
CAMP = "#16856B"
GLOBAL = "#4C78A8"
PROCESS = "#C56A1A"
BENEFIT = "#16856B"
PENALTY = "#C84A46"
GRID = "#DDE3E7"
TEXT = "#1E2A33"
MUTED = "#66727C"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def clean_axis(axis: plt.Axes) -> None:
    axis.grid(axis="y", color=GRID, linewidth=0.65, alpha=0.9)
    axis.set_axisbelow(True)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)


def describe(values: pd.Series) -> tuple[float, float, float]:
    array = values.to_numpy(dtype=float)
    mean = float(array.mean())
    sd = float(array.std(ddof=1))
    margin = float(
        t.ppf(0.975, len(array) - 1) * sd / math.sqrt(len(array))
    )
    return mean, mean - margin, mean + margin


def save(figure: plt.Figure, name: str) -> dict[str, object]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    png = OUTPUT / f"{name}.png"
    pdf = OUTPUT / f"{name}.pdf"
    figure.savefig(png, dpi=400)
    figure.savefig(pdf)
    size = [float(value) for value in figure.get_size_inches()]
    plt.close(figure)
    return {
        "name": name,
        "png": png.name,
        "png_sha256": sha256(png),
        "pdf": pdf.name,
        "pdf_sha256": sha256(pdf),
        "size_inches": size,
    }


def underallocation_figure(repetitions: pd.DataFrame) -> dict[str, object]:
    figure, axes = plt.subplots(1, 2, figsize=(7.15, 3.15), sharey=True)
    x = np.arange(len(SPLITS))
    width = 0.34
    for axis, contract, title in (
        (axes[0], "uniform_global_tail", "Uniform global-tail CAMP"),
        (axes[1], "uniform_process_tail", "Uniform process-tail CAMP"),
    ):
        selected = repetitions.loc[repetitions["contract"] == contract].copy()
        selected["access_rate"] = (
            1000.0
            * selected["access_underallocations"]
            / selected["test_rows"]
        )
        selected["camp_rate"] = (
            1000.0
            * selected["camp_underallocations"]
            / selected["test_rows"]
        )
        for offset, column, color, label in (
            (-width / 2, "access_rate", ACCESS, "Access"),
            (width / 2, "camp_rate", CAMP, "CAMP"),
        ):
            means = []
            lowers = []
            uppers = []
            for split in SPLITS:
                mean, lower, upper = describe(
                    selected.loc[selected["split"] == split, column]
                )
                means.append(mean)
                lowers.append(mean - lower)
                uppers.append(upper - mean)
            axis.bar(
                x + offset,
                means,
                width,
                color=color,
                label=label,
                yerr=np.vstack([lowers, uppers]),
                capsize=2.5,
                error_kw={"linewidth": 0.9},
            )
        axis.set_xticks(x, SPLITS)
        axis.set_xlabel("Development / holdout split")
        axis.set_title(title, fontweight="bold", pad=7)
        clean_axis(axis)
    axes[0].set_ylabel("Underallocations per 1,000 tasks")
    axes[0].legend(frameon=False, loc="upper right")
    figure.text(
        0.5,
        0.012,
        "Bars are five-seed means; error bars are t-based 95% CIs.",
        ha="center",
        fontsize=7.0,
        color=MUTED,
    )
    figure.subplots_adjust(
        left=0.09,
        right=0.985,
        top=0.88,
        bottom=0.22,
        wspace=0.16,
    )
    return save(figure, "fig_offline_seed_variance_underallocations")


def main() -> None:
    configure()
    repetitions = pd.read_csv(REPETITIONS)
    print(json.dumps(underallocation_figure(repetitions), indent=2))


if __name__ == "__main__":
    main()

