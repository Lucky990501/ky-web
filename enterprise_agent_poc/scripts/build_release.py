"""Create a clean, attestable release archive from one committed Git revision."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
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
    rejected = [
        name
        for name in selected
        if name.lower().endswith((".env", ".pem", ".key"))
        or "/.runtime-data/" in name
        or Path(name).name in {".DS_Store", ".AppleDouble", ".LSOverride", "Thumbs.db", "Desktop.ini"}
    ]
    if rejected:
        raise RuntimeError(f"发布包包含禁止文件：{rejected}")
    if not selected:
        raise RuntimeError("该 commit 没有可发布的 Workbench 文件。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        raw = Path(temp_dir) / "source.tar"
        subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", "--output", str(raw), commit, "--", *selected], check=True)
        with raw.open("rb") as source, output.open("wb") as destination:
            with gzip.GzipFile(filename="", mode="wb", fileobj=destination, compresslevel=9, mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
    archive_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    return {
        "commit": commit,
        "source_commit": commit,
        "archive": str(output),
        "sha256": archive_sha256,
        "archive_sha256": archive_sha256,
        "files": len(selected),
        "selected_files": selected,
        "build_platform": f"{platform.system()}-{platform.machine()}",
    }


def write_manifest(result: dict, release_id: str, output: Path) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", release_id):
        raise ValueError("Release ID 只能包含字母、数字、点、下划线和连字符。")
    manifest = {
        "release_id": release_id,
        "source_commit": result["source_commit"],
        "archive_sha256": result["archive_sha256"],
        "selected_files": result["selected_files"],
        "selected_file_count": result["files"],
        "build_platform": result["build_platform"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--release-id")
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if bool(args.release_id) != bool(args.manifest_output):
        parser.error("--release-id 与 --manifest-output 必须同时提供。")
    commit = validate_commit(args.commit)
    if args.dry_run:
        print(json.dumps({"status": "dry_run", "commit": commit, "allowed_paths": ALLOWED}, ensure_ascii=False))
        return 0
    result = build(commit, args.output)
    if args.manifest_output:
        write_manifest(result, args.release_id, args.manifest_output)
        result["manifest"] = str(args.manifest_output)
    print(json.dumps({"status": "built", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
