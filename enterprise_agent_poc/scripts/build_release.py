"""Create a clean, attestable release archive from one committed Git revision."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
ALLOWED = (
    "enterprise_agent_poc/app/",
    "enterprise_agent_poc/migrations/",
    "enterprise_agent_poc/scripts/",
    "enterprise_agent_poc/skill_packages/",
    "enterprise_agent_poc/deploy/",
    "enterprise_agent_poc/Dockerfile",
    "enterprise_agent_poc/docker-compose.yml",
    "enterprise_agent_poc/pyproject.toml",
)


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(REPO), *args], text=True).strip()


def validate_commit(revision: str) -> str:
    resolved = git("rev-parse", "--verify", f"{revision}^{{commit}}")
    if not re.fullmatch(r"[0-9a-f]{40}", resolved):
        raise RuntimeError("无法解析为明确的 Git commit。")
    return resolved


def build(commit: str, output: Path) -> dict:
    names = git("ls-tree", "-r", "--name-only", commit).splitlines()
    selected = [name for name in names if any(name == allowed or name.startswith(allowed) for allowed in ALLOWED)]
    rejected = [name for name in selected if name.endswith((".env", ".pem", ".key")) or "/.runtime-data/" in name]
    if rejected:
        raise RuntimeError(f"发布包包含禁止文件：{rejected}")
    if not selected:
        raise RuntimeError("该 commit 没有可发布的 Workbench 文件。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        raw = Path(temp_dir) / "source.tar"
        subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", "--output", str(raw), commit, "--", *selected], check=True)
        with tarfile.open(raw) as source, tarfile.open(output, "w:gz") as destination:
            for member in source.getmembers():
                if member.isfile() or member.isdir():
                    destination.addfile(member, source.extractfile(member) if member.isfile() else None)
    return {"commit": commit, "archive": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "files": len(selected)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    commit = validate_commit(args.commit)
    if args.dry_run:
        print(json.dumps({"status": "dry_run", "commit": commit, "allowed_paths": ALLOWED}, ensure_ascii=False))
        return 0
    print(json.dumps({"status": "built", **build(commit, args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
