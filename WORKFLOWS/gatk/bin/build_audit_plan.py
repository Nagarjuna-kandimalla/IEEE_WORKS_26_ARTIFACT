#!/usr/bin/env python3
"""Create a reproducible random or score-ranked audit plan from static tasks."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static-manifest", required=True)
    parser.add_argument("--out-plan", required=True)
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--predicted-memory-mb", type=int, default=4096)
    parser.add_argument("--strategy", choices=["random", "score"], required=True)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--scores", default="")
    parser.add_argument("--score-column", default="final_score")
    args = parser.parse_args()

    if not 0 < args.budget <= 1:
        parser.error("--budget must be in (0, 1]")
    tasks = read_tsv(Path(args.static_manifest))
    if not tasks:
        parser.error("static manifest has no tasks")
    count = math.ceil(len(tasks) * args.budget)

    if args.strategy == "random":
        rng = random.Random(args.seed)
        selected_ids = {row["task_instance"] for row in rng.sample(tasks, count)}
    else:
        if not args.scores:
            parser.error("--scores is required for --strategy score")
        with open(args.scores, newline="", encoding="utf-8") as handle:
            scores = {row["task_instance"]: float(row[args.score_column]) for row in csv.DictReader(handle)}
        missing = [row["task_instance"] for row in tasks if row["task_instance"] not in scores]
        if missing:
            parser.error(f"scores missing {len(missing)} task instances")
        selected_ids = {
            row["task_instance"]
            for row in sorted(tasks, key=lambda row: scores[row["task_instance"]], reverse=True)[:count]
        }

    output = Path(args.out_plan)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        columns = ["task_type", "task_instance", "predicted_memory", "audit_flag"]
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in tasks:
            writer.writerow({
                "task_type": "HaplotypeCaller",
                "task_instance": row["task_instance"],
                "predicted_memory": args.predicted_memory_mb,
                "audit_flag": "Audit" if row["task_instance"] in selected_ids else "NoAudit",
            })
    metadata = {
        "task_count": len(tasks), "audited_task_count": len(selected_ids), "budget": args.budget,
        "strategy": args.strategy, "seed": args.seed if args.strategy == "random" else None,
        "scores": args.scores if args.strategy == "score" else None,
        "score_column": args.score_column if args.strategy == "score" else None,
    }
    output.with_suffix(output.suffix + ".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
