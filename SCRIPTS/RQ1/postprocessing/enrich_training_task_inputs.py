#!/usr/bin/env python3
"""Add staged task input byte features to normalized training metrics.

The script reads each task's Nextflow work directory from trace_workdir, parses
the nxf_stage() block in .command.run, and sums the byte size of staged inputs.
Inputs pointing to workflow data/reference locations are treated as original
inputs. Inputs pointing to another Nextflow work directory are treated as
intermediate inputs.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shlex
from pathlib import Path


STAGE_RE = re.compile(r"^\s*ln\s+-s\s+(.+?)\s+(.+?)\s*$")
WORK_RE = re.compile(r"/runs/[^/]+/work/")
SKIP_TARGETS = {".command.begin", ".command.err", ".command.log", ".command.out", ".command.run", ".command.sh", ".command.trace", ".exitcode"}


def parse_stage_links(command_run: Path) -> list[tuple[str, str]]:
    if not command_run.exists():
        return []
    links: list[tuple[str, str]] = []
    in_stage = False
    for line in command_run.read_text(errors="replace").splitlines():
        if line.startswith("nxf_stage()"):
            in_stage = True
            continue
        if in_stage and line.startswith("}"):
            break
        if not in_stage:
            continue
        match = STAGE_RE.match(line)
        if not match:
            continue
        try:
            parts = shlex.split(line.strip())
        except ValueError:
            continue
        if len(parts) >= 4 and parts[0] == "ln" and parts[1] == "-s":
            links.append((parts[2], parts[3]))
    return links


def staged_symlinks(workdir: Path) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    try:
        with os.scandir(workdir) as entries:
            for entry in entries:
                if entry.name in SKIP_TARGETS or entry.name.startswith("."):
                    continue
                if not entry.is_symlink():
                    continue
                try:
                    source = os.readlink(entry.path)
                except OSError:
                    continue
                links.append((source, entry.name))
    except OSError:
        return []
    return links


def path_size(path: Path, cache: dict[str, int]) -> int:
    key = str(path)
    if key in cache:
        return cache[key]
    try:
        if path.is_dir():
            total = 0
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d not in {".nextflow", ".git"}]
                for name in files:
                    fp = Path(root) / name
                    try:
                        total += fp.stat().st_size
                    except OSError:
                        pass
            cache[key] = total
        else:
            cache[key] = path.stat().st_size
    except OSError:
        cache[key] = 0
    return cache[key]


def classify_source(source: str, workflow_root: Path) -> str:
    if WORK_RE.search(source):
        if "/work/stage-" in source:
            return "original"
        return "intermediate"
    if source.startswith(("s3://", "ftp://", "https://", "http://")):
        return "external"
    try:
        src = Path(source)
        resolved = src.resolve(strict=False)
    except OSError:
        return "unknown"
    root = workflow_root.resolve(strict=False)
    if "/WORKS_AT_SC/workflows_run/" in str(resolved) and "/data/" in str(resolved):
        return "original"
    if "/work/stage-" in str(resolved):
        return "original"
    if str(resolved).startswith(str(root / "data")):
        return "original"
    if str(resolved).startswith(str(root / "source")):
        return "original"
    if str(resolved).startswith(str(root / "references")):
        return "original"
    if WORK_RE.search(str(resolved)):
        return "intermediate"
    return "external"


def enrich(input_tsv: Path, output_tsv: Path, workflow_root: Path) -> None:
    size_cache: dict[str, int] = {}
    with input_tsv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    added = [
        "staged_input_file_count",
        "staged_original_input_bytes",
        "staged_intermediate_input_bytes",
        "staged_external_input_bytes",
        "staged_total_input_bytes",
        "staged_input_local_names",
        "staged_input_sources",
        "runtime_total_read_bytes",
        "runtime_total_rchar_bytes",
        "runtime_audited_read_bytes",
        "runtime_audited_mmap_bytes",
    ]
    out_fields = fieldnames + [c for c in added if c not in fieldnames]

    for row in rows:
        workdir = Path(row.get("trace_workdir", ""))
        links = staged_symlinks(workdir) if str(workdir) else []
        if not links and str(workdir):
            links = parse_stage_links(workdir / ".command.run")
        original = intermediate = external = 0
        local_names: list[str] = []
        sources: list[str] = []
        for source, local_name in links:
            if Path(local_name).name in SKIP_TARGETS:
                continue
            cls = classify_source(source, workflow_root)
            size = 0 if source.startswith(("s3://", "ftp://", "https://", "http://")) else path_size(Path(source), size_cache)
            if cls == "original":
                original += size
            elif cls == "intermediate":
                intermediate += size
            else:
                external += size
            local_names.append(local_name)
            sources.append(source)

        row["staged_input_file_count"] = str(len(local_names))
        row["staged_original_input_bytes"] = str(original)
        row["staged_intermediate_input_bytes"] = str(intermediate)
        row["staged_external_input_bytes"] = str(external)
        row["staged_total_input_bytes"] = str(original + intermediate + external)
        row["staged_input_local_names"] = "|".join(local_names)
        row["staged_input_sources"] = "|".join(sources)
        row["runtime_total_read_bytes"] = row.get("trace_read_bytes", "")
        row["runtime_total_rchar_bytes"] = row.get("trace_rchar", "")
        row["runtime_audited_read_bytes"] = row.get("strace_read_bytes") or row.get("ebpf_read_bytes", "")
        row["runtime_audited_mmap_bytes"] = row.get("ebpf_mmap_bytes", "")

    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    with output_tsv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=out_fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="training_task_metrics.tsv")
    parser.add_argument("--output", required=True, help="enriched output TSV")
    parser.add_argument("--workflow-root", required=True, help="workflow directory, e.g. workflows_run/Taxprofiler")
    args = parser.parse_args()
    enrich(Path(args.input), Path(args.output), Path(args.workflow_root))


if __name__ == "__main__":
    main()
