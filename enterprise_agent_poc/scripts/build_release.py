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
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.bundled_skills import forbidden_path, secret_content, validate_git_bundle
from scripts.release_manifest import validate_manifest_contract

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


def preflight(commit: str) -> list[str]:
    validate_git_bundle(REPO, commit)
    tree = {}
    for record in subprocess.check_output(["git", "-C", str(REPO), "ls-tree", "-r", "-z", commit]).split(b"\0"):
        if record:
            header, name = record.split(b"\t", 1)
            mode, kind, oid = header.decode().split()
            tree[name.decode()] = (mode, kind, oid)
    selected = [name for name in tree if any(name == allowed or name.startswith(allowed) for allowed in ALLOWED)]
    rejected = [
        name
        for name in selected
        if forbidden_path(name) or tree[name][0] not in {"100644", "100755"} or tree[name][1] != "blob"
    ]
    if rejected:
        raise RuntimeError(f"发布包包含禁止文件：{rejected}")
    if not selected:
        raise RuntimeError("该 commit 没有可发布的 Workbench 文件。")
    for name in selected:
        if secret_content(subprocess.check_output(["git", "-C", str(REPO), "cat-file", "blob", tree[name][2]])):
            raise RuntimeError(f"发布文件含禁止的秘密材料：{name}")
    return selected


def build(commit: str, output: Path) -> dict:
    selected = preflight(commit)
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


def write_manifest(result: dict, release_id: str, output: Path, binding_transition: dict | None = None,
                   *, forward_migrations: dict | None = None, deferred_skill: dict | None = None,
                   skill_package_staging: dict | None = None) -> dict:
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
    if binding_transition is not None:
        manifest["binding_transition"] = binding_transition
    if forward_migrations is not None:
        manifest["forward_migrations"] = forward_migrations
    if deferred_skill is not None:
        manifest["deferred_skill"] = deferred_skill
    if skill_package_staging is not None:
        manifest["skill_package_staging"] = skill_package_staging
    validate_manifest_contract(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--release-id")
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--binding-transition", type=Path)
    parser.add_argument("--forward-migrations", type=Path)
    parser.add_argument("--deferred-skill", type=Path)
    parser.add_argument("--skill-package-staging", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if bool(args.release_id) != bool(args.manifest_output) or ((args.binding_transition or args.forward_migrations or args.deferred_skill or args.skill_package_staging) and not args.manifest_output):
        parser.error("--release-id 与 --manifest-output 必须同时提供。")
    commit = validate_commit(args.commit)
    if args.dry_run:
        selected = preflight(commit)
        print(json.dumps({"status": "dry_run", "commit": commit, "allowed_paths": ALLOWED, "selected_files": selected}, ensure_ascii=False))
        return 0
    result = build(commit, args.output)
    if args.manifest_output:
        try:
            transition = json.loads(args.binding_transition.read_text(encoding="utf-8")) if args.binding_transition else None
            migrations = json.loads(args.forward_migrations.read_text(encoding="utf-8")) if args.forward_migrations else None
            deferred = json.loads(args.deferred_skill.read_text(encoding="utf-8")) if args.deferred_skill else None
            staging = json.loads(args.skill_package_staging.read_text(encoding="utf-8")) if args.skill_package_staging else None
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Binding transition declaration is unreadable.") from exc
        write_manifest(result, args.release_id, args.manifest_output, transition,
                       forward_migrations=migrations, deferred_skill=deferred,
                       skill_package_staging=staging)
        result["manifest"] = str(args.manifest_output)
    print(json.dumps({"status": "built", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
