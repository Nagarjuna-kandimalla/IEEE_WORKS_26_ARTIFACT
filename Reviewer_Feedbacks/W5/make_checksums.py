#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXCLUDED = {".venv", "results", "__pycache__"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


paths = []
for path in ROOT.rglob("*"):
    relative = path.relative_to(ROOT)
    if not path.is_file() or path.name == "MANIFEST.sha256":
        continue
    if any(part in EXCLUDED for part in relative.parts):
        continue
    paths.append(path)
(ROOT / "MANIFEST.sha256").write_text(
    "\n".join(f"{sha256(path)}  {path.relative_to(ROOT)}" for path in sorted(paths))
    + "\n"
)
print(f"Wrote checksums for {len(paths)} files.")
