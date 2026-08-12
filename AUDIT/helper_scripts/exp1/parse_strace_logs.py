#!/usr/bin/env python3
"""Summarize online strace logs emitted by the shared Nextflow overlay."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


CALL_NAMES = (
    "read|pread64|readv|preadv|write|pwrite64|writev|pwritev|mmap|mmap2"
)
CALL_RE = re.compile(rf"^(?:\[pid +\d+\] +)?({CALL_NAMES})\(")
RESUMED_RE = re.compile(
    rf"^(?:\[pid +\d+\] +)?<\.\.\. ({CALL_NAMES}) resumed>"
)
RETURN_RE = re.compile(
    r"=\s*(-?(?:0x[0-9a-fA-F]+|\d+))(?:\s+<[\d.]+>)?\s*$"
)
READ_CALLS = {"read", "pread64", "readv", "preadv"}
MMAP_CALLS = {"mmap", "mmap2"}


def parse_return_value(raw_value: str) -> int:
    sign = -1 if raw_value.startswith("-") else 1
    unsigned_value = raw_value[1:] if sign == -1 else raw_value
    base = 16 if unsigned_value.lower().startswith("0x") else 10
    return sign * int(unsigned_value, base)


def record_call(call: str, line: str, stats: dict[str, int]) -> None:
    if call in MMAP_CALLS:
        stats["strace_mmap_calls"] += 1
        return

    returned = RETURN_RE.search(line)
    value = parse_return_value(returned.group(1)) if returned else 0
    if call in READ_CALLS:
        stats["strace_read_calls"] += 1
        stats["strace_read_bytes"] += max(value, 0)
    else:
        stats["strace_write_calls"] += 1
        stats["strace_write_bytes"] += max(value, 0)


def summarize_lines(lines: object, stats: dict[str, int]) -> None:
    for line in lines:
        call_match = CALL_RE.match(line)
        if call_match:
            if "<unfinished ...>" not in line:
                record_call(call_match.group(1), line, stats)
            continue

        resumed_match = RESUMED_RE.match(line)
        if resumed_match:
            # The unfinished half was deliberately not counted. Count the
            # syscall once, using the return value from its resumed half.
            record_call(resumed_match.group(1), line, stats)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="results/strace directory")
    parser.add_argument("--out", required=True)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--trace")
    parser.add_argument("--successful-only", action="store_true")
    args = parser.parse_args()

    if args.shard_count < 1:
        parser.error("--shard-count must be positive")
    if not 0 <= args.shard_index < args.shard_count:
        parser.error("--shard-index must be between 0 and shard-count - 1")
    if args.successful_only and not args.trace:
        parser.error("--successful-only requires --trace")

    rows: list[dict[str, str | int]] = []
    task_dirs = sorted(Path(args.root).glob("*/task_*"))
    if args.successful_only:
        with Path(args.trace).open(newline="", encoding="utf-8") as handle:
            successful_prefixes = {
                row["hash"].replace("/", "")
                for row in csv.DictReader(handle, delimiter="\t")
                if row.get("status") in {"COMPLETED", "CACHED"}
            }
        task_dirs = [
            task_dir for task_dir in task_dirs
            if any(
                task_dir.name.removeprefix("task_").startswith(prefix)
                for prefix in successful_prefixes
            )
        ]

    for task_index, task_dir in enumerate(task_dirs):
        if task_index % args.shard_count != args.shard_index:
            continue
        process_file = task_dir / "process_name.txt"
        process = process_file.read_text(encoding="utf-8").strip() if process_file.exists() else task_dir.parent.name
        task_hash = task_dir.name.removeprefix("task_")
        stats = {
            "strace_log_prefix": str(task_dir / "strace.log"),
            "strace_file_count": 0,
            "strace_mmap_calls": 0,
            "strace_read_calls": 0,
            "strace_read_bytes": 0,
            "strace_write_calls": 0,
            "strace_write_bytes": 0,
        }
        logs = sorted(task_dir.glob("strace.log.*"))
        count_file = task_dir / "strace_file_count.txt"
        if count_file.exists():
            stats["strace_file_count"] = int(count_file.read_text(encoding="utf-8").strip())
        else:
            stats["strace_file_count"] = len(logs)

        for log in logs:
            with log.open(encoding="utf-8", errors="replace") as handle:
                summarize_lines(handle, stats)
        rows.append({"process": process, "task_hash": task_hash, **stats})

    columns = [
        "process", "task_hash", "strace_log_prefix", "strace_file_count", "strace_mmap_calls",
        "strace_read_calls", "strace_read_bytes", "strace_write_calls", "strace_write_bytes",
    ]
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
