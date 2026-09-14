"""Compare a release environment file with the process environment safely.

The command never prints configuration values or secrets.  It only reports
per-variable presence and SHA-256 equality, so it is suitable for a production
host before a RAG acceptance run.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
from pathlib import Path

# Read the literal contract without importing Settings (which loads workspace
# secrets and constructs business settings). No code from that module executes.
def _runtime_config_names() -> tuple[str, ...]:
    tree = ast.parse((Path(__file__).resolve().parents[1] / "app/settings.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "RUNTIME_CONFIG_ENV_NAMES"
            for target in node.targets
        ):
            names = ast.literal_eval(node.value)
            if isinstance(names, tuple) and all(isinstance(name, str) for name in names):
                return names
    raise RuntimeError("Runtime configuration contract unavailable")


RUNTIME_CONFIG_ENV_NAMES = _runtime_config_names()


def safe_runtime_config_snapshot(environ: dict[str, str] | None = None) -> dict:
    """Pure counterpart of Settings' fingerprint; contract equivalence is tested."""
    source = os.environ if environ is None else environ
    fields = {
        name: {"configured": bool(source.get(name, "")),
               "sha256": hashlib.sha256(source.get(name, "").encode("utf-8")).hexdigest()}
        for name in RUNTIME_CONFIG_ENV_NAMES
    }
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return {"algorithm": "sha256", "fingerprint": hashlib.sha256(canonical.encode()).hexdigest(),
            "fields": fields}


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
