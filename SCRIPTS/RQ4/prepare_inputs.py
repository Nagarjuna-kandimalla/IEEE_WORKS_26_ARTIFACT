#!/usr/bin/env python3
"""Materialize the compact frozen gate inputs into the expected result layout."""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ARTIFACT_ROOT = ROOT.parents[1]


def main() -> None:
    source = ARTIFACT_ROOT / "DATA" / "RQ4" / "inputs" / "seed_1996"
    target = ROOT / "results" / "seed_1996"
    for compressed in sorted(source.rglob("*.gz")):
        relative = compressed.relative_to(source)
        output = target / relative.with_suffix("")
        output.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(compressed, "rb") as reader, output.open("wb") as writer:
            shutil.copyfileobj(reader, writer)
        print(output.relative_to(ROOT))


if __name__ == "__main__":
    main()
