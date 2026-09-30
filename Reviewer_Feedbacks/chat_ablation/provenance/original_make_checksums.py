#!/usr/bin/env python3
"""Create a SHA-256 manifest for the immutable package inputs and source."""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXCLUDED_PARTS = {".venv", "models", "results", "__pycache__"}
EXCLUDED_FILES = {"MANIFEST.sha256"}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> None:
    paths = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if not path.is_file() or path.name in EXCLUDED_FILES:
            continue
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        paths.append(path)
    lines = [f"{digest(path)}  {path.relative_to(ROOT)}" for path in sorted(paths)]
    (ROOT / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
    print(f"Wrote checksums for {len(lines)} files.")


if __name__ == "__main__":
    main()
