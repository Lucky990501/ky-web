"""Compare a release environment file with the process environment safely.

The command never prints configuration values or secrets.  It only reports
per-variable presence and SHA-256 equality, so it is suitable for a production
host before a RAG acceptance run.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from app.settings import RUNTIME_CONFIG_ENV_NAMES, safe_runtime_config_snapshot


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        values[key] = value.strip().strip('"').strip("'")
    return values


def compare(expected: dict[str, str], current: dict[str, str]) -> dict:
    expected_snapshot = safe_runtime_config_snapshot(expected)
    current_snapshot = safe_runtime_config_snapshot(current)
    fields = []
    for name in RUNTIME_CONFIG_ENV_NAMES:
        fields.append(
            {
                "name": name,
                "expected_configured": expected_snapshot["fields"][name]["configured"],
                "runtime_configured": current_snapshot["fields"][name]["configured"],
                "matches": expected_snapshot["fields"][name]["sha256"] == current_snapshot["fields"][name]["sha256"],
            }
        )
    return {
        "expected_fingerprint": expected_snapshot["fingerprint"],
        "runtime_fingerprint": current_snapshot["fingerprint"],
        "matches": all(field["matches"] for field in fields),
        "fields": fields,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment-file", required=True, type=Path)
    args = parser.parse_args()
    result = compare(parse_env_file(args.environment_file), dict(__import__("os").environ))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["matches"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
