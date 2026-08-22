#!/usr/bin/env python3
"""Create an empty or transactionally copied run-local CAMP history DB."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from camp_ml.online_history import SQLiteHistoryStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("empty", "snapshot"), required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()

    destination = args.destination.resolve()
    if destination.exists():
        if not args.reuse:
            raise SystemExit(f"history destination already exists: {destination}")
        with SQLiteHistoryStore(destination) as store:
            print(f"history_rows={store.count()}")
        print(f"history_db={destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "snapshot":
        if args.source is None or not args.source.exists():
            raise SystemExit("--source is required for snapshot mode")
        source = sqlite3.connect(args.source.resolve())
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
    with SQLiteHistoryStore(destination) as store:
        print(f"history_rows={store.count()}")
    print(f"history_db={destination}")


if __name__ == "__main__":
    main()
