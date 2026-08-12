#!/usr/bin/env python3
"""Summarize per-task file attribution from the targeted eBPF overlay."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rows = []
    for path in sorted(Path(args.root).glob("*/task_*/ebpf_attribution_*.tsv")):
        process_file = path.parent / "process_name.txt"
        process = process_file.read_text(encoding="utf-8").strip() if process_file.exists() else path.parent.parent.name
        read_bytes = mmap_bytes = 0
        with path.open(newline="", encoding="utf-8") as handle:
            for item in csv.DictReader(handle, delimiter="\t"):
                read_bytes += int(item.get("read_bytes") or 0)
                mmap_bytes += int(item.get("mmap_bytes") or 0)
        total = read_bytes + mmap_bytes
        rows.append({
            "process": process,
            "task_hash": path.parent.name.removeprefix("task_"),
            "ebpf_read_bytes": read_bytes,
            "ebpf_mmap_bytes": mmap_bytes,
            "ebpf_total_bytes": total,
            "mmap_percentage": round(100 * mmap_bytes / total, 6) if total else 0.0,
            "ebpf_tsv": str(path),
        })

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = ["process", "task_hash", "ebpf_read_bytes", "ebpf_mmap_bytes", "ebpf_total_bytes", "mmap_percentage", "ebpf_tsv"]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} task rows to {output}")


if __name__ == "__main__":
    main()
