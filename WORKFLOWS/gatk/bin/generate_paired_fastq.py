#!/usr/bin/env python3
"""Create a deterministic paired-end synthetic read set for one sample."""

from __future__ import annotations

import argparse
import gzip
import random
from pathlib import Path


READ_LENGTH = 150
INSERT_MEAN = 350
INSERT_SD = 50
ERROR_RATE = 0.001
QUAL_CHAR = "I"
BASES = "ACGT"
COMPLEMENT = str.maketrans("ACGT", "TGCA")


def load_reference(path: Path) -> dict[str, str]:
    sequences: dict[str, list[str]] = {}
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(">"): 
            current = line[1:].split()[0]
            sequences[current] = []
        elif current:
            sequences[current].append(line.strip().upper())
    return {name: "".join(parts) for name, parts in sequences.items()}


def reverse_complement(sequence: str) -> str:
    return sequence.translate(COMPLEMENT)[::-1]


def mutate(sequence: str, rng: random.Random) -> str:
    bases = list(sequence)
    for index, base in enumerate(bases):
        if rng.random() < ERROR_RATE:
            alternatives = [candidate for candidate in BASES if candidate != base]
            bases[index] = rng.choice(alternatives)
    return "".join(bases)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--coverage", type=float, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--read1", required=True)
    parser.add_argument("--read2", required=True)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    chroms = load_reference(Path(args.reference))
    if not chroms:
        raise ValueError("reference has no sequences")

    usable = [(name, sequence) for name, sequence in chroms.items() if len(sequence) > INSERT_MEAN + 2 * INSERT_SD]
    if not usable:
        raise ValueError("reference sequences are too short for synthetic inserts")

    genome_size = sum(len(sequence) for _, sequence in usable)
    pair_count = int((genome_size * args.coverage + (2 * READ_LENGTH - 1)) // (2 * READ_LENGTH))
    weights = [len(sequence) for _, sequence in usable]
    quality = QUAL_CHAR * READ_LENGTH

    read1 = Path(args.read1)
    read2 = Path(args.read2)
    read1.parent.mkdir(parents=True, exist_ok=True)
    read2.parent.mkdir(parents=True, exist_ok=True)

    with gzip.open(read1, "wt", encoding="utf-8") as first, gzip.open(read2, "wt", encoding="utf-8") as second:
        for number in range(1, pair_count + 1):
            chrom, sequence = rng.choices(usable, weights=weights, k=1)[0]
            insert_size = max(2 * READ_LENGTH, int(rng.gauss(INSERT_MEAN, INSERT_SD)))
            start = rng.randint(0, len(sequence) - insert_size)
            fragment = sequence[start : start + insert_size]
            mate1 = mutate(fragment[:READ_LENGTH], rng)
            mate2 = mutate(reverse_complement(fragment[-READ_LENGTH:]), rng)
            name = f"@{args.sample_id}.{number} {chrom}:{start + 1}"
            first.write(f"{name}/1\n{mate1}\n+\n{quality}\n")
            second.write(f"{name}/2\n{mate2}\n+\n{quality}\n")


if __name__ == "__main__":
    main()
