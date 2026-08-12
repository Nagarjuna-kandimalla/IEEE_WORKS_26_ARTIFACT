#!/usr/bin/env python3
"""Generate the CAMP-only underallocation seed-variance figure."""

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
SOURCE = ROOT / "RESULTS" / "RQ2" / "csv" / "repetition_allocator_metrics.csv"
SUMMARY = ROOT / "RESULTS" / "RQ2" / "csv" / "camp_only_seed_variance_summary.csv"
OUTPUT = ROOT / "RESULTS" / "RQ2" / "figures"
SPLITS = ["30/70", "50/50", "70/30", "85/15", "90/10"]
SEEDS = [1996, 1997, 1998, 1999, 2000]
SEED_COLORS = {
    1996: "#3B6FB6",
    1997: "#D17A22",
    1998: "#16856B",
    1999: "#C84A46",
    2000: "#7A5AA6",
}
SEED_OFFSETS = {
    1996: -0.18,
    1997: -0.09,
    1998: 0.00,
    1999: 0.09,
    2000: 0.18,
}
TEXT = "#1E2A33"
MUTED = "#66727C"
GRID = "#DDE3E7"


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
            "axes.titlesize": 10.0,
            "axes.labelsize": 8.5,
            "xtick.labelsize": 7.8,
            "ytick.labelsize": 7.8,
            "legend.fontsize": 7.1,
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


def summarize(camp: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for split in SPLITS:
        selected = camp.loc[camp["split"].eq(split)].copy()
        if sorted(selected["seed"].tolist()) != SEEDS:
            raise ValueError(f"{split} does not contain exactly five seeds")
        values = selected["underallocations_per_1000"].to_numpy(
            dtype=float
        )
        mean = float(values.mean())
        sd = float(values.std(ddof=1))
        margin = float(
            t.ppf(0.975, len(values) - 1)
            * sd
            / math.sqrt(len(values))
        )
        rows.append(
            {
                "split": split,
                "test_rows_per_seed": int(selected["test_rows"].iloc[0]),
                "n_seeds": len(values),
                "mean_underallocations": float(
                    selected["underallocations"].mean()
                ),
                "sample_sd_underallocations": float(
                    selected["underallocations"].std(ddof=1)
                ),
                "mean_underallocations_per_1000": mean,
                "sample_sd_underallocations_per_1000": sd,
                "coefficient_of_variation_percent": 100.0 * sd / mean,
                "ci95_lower_per_1000": mean - margin,
                "ci95_upper_per_1000": mean + margin,
                "minimum_per_1000": float(values.min()),
                "maximum_per_1000": float(values.max()),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    configure()
    data = pd.read_csv(SOURCE)
    camp = data.loc[data["allocator"].eq("camp")].copy()
    if len(camp) != 25:
        raise ValueError(f"expected 25 CAMP rows, found {len(camp)}")
    summary = summarize(camp)
    summary.to_csv(SUMMARY, index=False)

    figure, axis = plt.subplots(figsize=(7.15, 3.75))
    x = np.arange(len(SPLITS), dtype=float)
    for seed in SEEDS:
        selected = (
            camp.loc[camp["seed"].eq(seed)]
            .set_index("split")
            .reindex(SPLITS)
        )
        axis.scatter(
            x + SEED_OFFSETS[seed],
            selected["underallocations_per_1000"],
            s=31,
            color=SEED_COLORS[seed],
            edgecolor="white",
            linewidth=0.6,
            label=str(seed),
            zorder=3,
        )

    means = summary["mean_underallocations_per_1000"].to_numpy()
    lower = means - summary["ci95_lower_per_1000"].to_numpy()
    upper = summary["ci95_upper_per_1000"].to_numpy() - means
    axis.errorbar(
        x,
        means,
        yerr=np.vstack([lower, upper]),
        fmt="D",
        markersize=5.0,
        markerfacecolor="white",
        markeredgecolor=TEXT,
        color=TEXT,
        elinewidth=1.15,
        capsize=3.5,
        label="Mean and 95% CI",
        zorder=4,
    )

    for index, row in summary.iterrows():
        selected = camp.loc[camp["split"].eq(row["split"])]
        label_height = max(
            float(selected["underallocations_per_1000"].max()),
            float(row["ci95_upper_per_1000"]),
        ) + 1.0
        axis.text(
            x[index],
            label_height,
            f"CV {row['coefficient_of_variation_percent']:.1f}%",
            ha="center",
            va="bottom",
            fontsize=6.8,
            color=MUTED,
        )
        axis.annotate(
            f"{row['mean_underallocations_per_1000']:.1f}",
            (x[index], row["mean_underallocations_per_1000"]),
            xytext=(0, -13),
            textcoords="offset points",
            ha="center",
            va="top",
            fontsize=6.6,
            color=TEXT,
        )

    axis.set_xlim(-0.45, len(SPLITS) - 0.55)
    axis.set_ylim(0, 48)
    axis.set_xticks(x, SPLITS)
    axis.set_xlabel("Development / holdout split")
    axis.set_ylabel("CAMP underallocations per 1,000 tasks")
    figure.suptitle(
        "CAMP underallocation variance across five training seeds",
        fontweight="bold",
        y=0.975,
    )
    axis.grid(axis="y", color=GRID, linewidth=0.65, alpha=0.9)
    axis.set_axisbelow(True)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.legend(
        ncol=6,
        frameon=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.015),
        handletextpad=0.35,
        columnspacing=0.9,
    )
    figure.text(
        0.5,
        0.012,
        (
            "Uniform APC-only process-tail CAMP; dots are independently "
            "retrained seeds and diamonds are five-seed means."
        ),
        ha="center",
        fontsize=7.0,
        color=MUTED,
    )
    figure.subplots_adjust(
        left=0.105,
        right=0.985,
        top=0.79,
        bottom=0.20,
    )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    name = "fig_camp_only_seed_variance_underallocations"
    png = OUTPUT / f"{name}.png"
    pdf = OUTPUT / f"{name}.pdf"
    figure.savefig(png, dpi=400)
    figure.savefig(pdf)
    plt.close(figure)

    manifest = {
        "experiment": "CAMP-only offline training-seed variance",
        "allocator": "uniform APC-only process-tail CAMP",
        "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": sha256(SOURCE),
        "summary": str(SUMMARY.relative_to(ROOT)),
        "summary_sha256": sha256(SUMMARY),
        "seeds": SEEDS,
        "splits": SPLITS,
        "figure_png": png.name,
        "figure_png_sha256": sha256(png),
        "figure_pdf": pdf.name,
        "figure_pdf_sha256": sha256(pdf),
    }
    manifest_path = OUTPUT / "camp_only_figure_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    checksums = OUTPUT / "CAMP_ONLY_FIGURE_CHECKSUMS.sha256"
    checksums.write_text(
        "\n".join(
            [
                f"{sha256(png)}  {png.name}",
                f"{sha256(pdf)}  {pdf.name}",
                f"{sha256(manifest_path)}  {manifest_path.name}",
                f"{sha256(SUMMARY)}  ../{SUMMARY.relative_to(ROOT)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
