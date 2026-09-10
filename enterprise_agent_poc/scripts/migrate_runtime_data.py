"""Safely merge legacy release-local Codex Runtime data into one stable root."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def merge(source: Path, destination: Path, *, execute: bool) -> dict:
    if not source.is_dir():
        return {"source": str(source), "status": "missing", "copied": 0, "skipped": 0}
    copied = skipped = 0
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            if execute:
                target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists():
            skipped += 1
            continue
        copied += 1
        if execute:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
    return {"source": str(source), "status": "merged" if execute else "dry_run", "copied": copied, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination == Path("/"):
        raise ValueError("refuse filesystem root")
    print(merge(args.source.resolve(), destination, execute=args.execute))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
