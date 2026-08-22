"""Canonical operation and behavioral feature-class metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass


REGISTRY_VERSION = "2026-07-27-v1"


@dataclass(frozen=True)
class TaskMetadata:
    canonical_operation: str
    feature_classes: tuple[str, ...]
    registry_version: str = REGISTRY_VERSION


RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        r"KRAKEN2",
        "KRAKEN2",
        ("database_lookup", "random_io", "mmap_enabled"),
    ),
    (
        r"(?:^|[_:])(?:UNTAR|UNTAR_DB|UNTAR_KRAKEN2DB)$|UNPACK_DB",
        "UNTAR",
        ("decompression", "sequential_io", "mmap_heavy"),
    ),
    (
        r"MERGE_HAPLOTYPECALLER",
        "MERGE_HAPLOTYPECALLER",
        ("jvm", "variant_processing", "sequential_io"),
    ),
    (
        r"HAPLOTYPECALLER",
        "HAPLOTYPECALLER",
        ("jvm", "compute_heavy", "reference_io"),
    ),
    (
        r"MINIMAP|MAP_WINDOW|BUILD_INDEX",
        "MINIMAP2",
        ("alignment", "index_lookup", "mmap_enabled"),
    ),
    (
        r"BWA|ALIGN",
        "ALIGNMENT",
        ("alignment", "compute_heavy", "sequential_io"),
    ),
    (
        r"SAMTOOLS|FLAGSTAT|INDEX_BAM",
        "SAMTOOLS",
        ("alignment_utility", "sequential_io"),
    ),
    (
        r"FASTQC",
        "FASTQC",
        ("quality_control", "sequential_io"),
    ),
    (
        r"FASTP",
        "FASTP",
        ("read_preprocessing", "sequential_io"),
    ),
    (
        r"MOSDEPTH",
        "MOSDEPTH",
        ("coverage_analysis", "random_io"),
    ),
    (
        r"KRONA",
        "KRONA",
        ("report_generation", "metadata_processing"),
    ),
    (
        r"TABIX|BGZIP",
        "HTSLIB_INDEXING",
        ("compression", "indexing", "sequential_io"),
    ),
)


def _terminal_process(process: str) -> str:
    terminal = str(process).strip().split(":")[-1].upper()
    terminal = re.sub(r"[^A-Z0-9]+", "_", terminal).strip("_")
    duplicate = re.fullmatch(r"([A-Z0-9]+)_\1", terminal)
    return duplicate.group(1) if duplicate else terminal


def classify_task(process: str) -> TaskMetadata:
    normalized = str(process).strip().upper()
    for pattern, operation, classes in RULES:
        if re.search(pattern, normalized):
            return TaskMetadata(operation, tuple(sorted(set(classes))))
    operation = _terminal_process(process) or "UNKNOWN_OPERATION"
    return TaskMetadata(operation, ("unclassified",))
