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
import tarfile
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.bundled_skills import deterministic_zip, forbidden_path, safe_path, secret_content, validate_git_bundle
from scripts.release_manifest import validate_manifest_contract

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
ALLOWED = (
    "enterprise_agent_poc/app/",
    "enterprise_agent_poc/migrations/",
    "enterprise_agent_poc/scripts/",
    "enterprise_agent_poc/skill_packages/",
    "enterprise_agent_poc/integrations/",
    "enterprise_agent_poc/skill_sources/",
    "enterprise_agent_poc/deploy/",
    "enterprise_agent_poc/Dockerfile",
    "enterprise_agent_poc/docker-compose.yml",
    "enterprise_agent_poc/pyproject.toml",
)
STATIC_ROOT = "enterprise_agent_poc/app/static"
STATIC_INDEX = f"{STATIC_ROOT}/index.html"
STATIC_ASSETS = {
    "javascript": f"{STATIC_ROOT}/workbench.js",
    "stylesheet": f"{STATIC_ROOT}/workbench.css",
}
STATIC_VERSION_PLACEHOLDERS = {
    "javascript": b"__JS_SHA256_V1__",
    "stylesheet": b"__CSS_SHA256_V1_",
}
STATIC_VERSION_LENGTH = 16
WECHAT_INTEGRATIONS = (
    "artifacts/wechat-html-draft-1.0.0.zip", "controlled-skill-action.v1.json",
    "skill-dispatch.v1.json", "skill-only-test-qualification.v1.json",
    "wechat-html-draft.dependencies.v1.json", "wechat-html-draft.original.v1.json",
    "wechat-html-draft.revision.v1.json", "wechat-python-runtime.v1.json",
    "wechat-python311-linux.v1.lock.json", "wechat-python311-linux.v1.requirements.txt",
)


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(REPO), *args], text=True).strip()


def validate_commit(revision: str) -> str:
    resolved = git("rev-parse", "--verify", f"{revision}^{{commit}}")
    if not re.fullmatch(r"[0-9a-f]{40}", resolved):
        raise RuntimeError("无法解析为明确的 Git commit。")
    return resolved


def _git_blob(commit: str, path: str) -> bytes:
    try:
        return subprocess.check_output(["git", "-C", str(REPO), "show", f"{commit}:{path}"])
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"发布文件缺失：{path}") from exc


def _wechat_resource_contract(commit: str, selected: list[str]) -> dict:
    """Package inputs only; this does not authorize/install/enable Skill runtime."""
    prefix = "enterprise_agent_poc/"
    if not any(name.startswith((prefix + "integrations/", prefix + "skill_sources/"))
               or name == prefix + "app/wechat_skill.py" for name in selected):
        return {}  # Historical releases without this integration stay buildable.
    required = {prefix + "integrations/" + name for name in WECHAT_INTEGRATIONS}
    required.update(prefix + "scripts/" + name for name in (
        "verify_wechat_skill.py", "lock_wechat_python_runtime.py", "wechat_revision_python_runtime.py"))
    if required - set(selected):
        raise RuntimeError(f"微信 Skill 发布资源缺失：{sorted(required - set(selected))}")
    blob = lambda path: _git_blob(commit, prefix + path)
    digest = lambda raw: hashlib.sha256(raw).hexdigest()
    revision = json.loads(blob("integrations/wechat-html-draft.revision.v1.json"))
    source_root = "skill_sources/wechat-html-draft/1.0.0"
    if (revision["slug"], revision["version"], revision["source_root"]) != ("wechat-html-draft", "1.0.0", source_root):
        raise RuntimeError("微信 Skill 发布 Revision 不匹配。")
    files = {}
    for entry in revision["files"]:
        name = safe_path(entry["path"])
        path = source_root + "/" + name
        if name in files or prefix + path not in selected:
            raise RuntimeError("微信 Skill 发布 Source 清单不完整。")
        raw = blob(path)
        if (digest(raw) != entry["sha256"] or entry["git_mode"] != "100644" or b"\r" in raw
                or git("ls-tree", commit, "--", prefix + path).split()[0] != entry["git_mode"]):
            raise RuntimeError(f"微信 Skill 发布 Source 身份不匹配：{path}")
        files[name] = (raw, entry["git_mode"])
    expected = {prefix + source_root + "/" + name for name in files}
    actual = {name for name in selected if name.startswith(prefix + source_root + "/")}
    if expected != actual or not {"SKILL.md", "scripts/wechat_draft.py", "scripts/requirements.txt"} <= files.keys():
        raise RuntimeError("微信 Skill 发布 Source 清单不匹配。")
    artifact = blob("integrations/artifacts/wechat-html-draft-1.0.0.zip")
    if digest(artifact) != revision["artifact_sha256"] or artifact != deterministic_zip("wechat-html-draft", "1.0.0", files):
        raise RuntimeError("微信 Skill 发布 ZIP 身份不匹配。")
    dependencies_raw = blob("integrations/wechat-html-draft.dependencies.v1.json")
    if digest(dependencies_raw) != revision["dependencies_sha256"]:
        raise RuntimeError("微信 Skill 发布依赖定义身份不匹配。")
    dependencies = json.loads(dependencies_raw)
    runtime = json.loads(blob("integrations/wechat-python-runtime.v1.json"))
    lock_raw = blob("integrations/wechat-python311-linux.v1.lock.json")
    lock = json.loads(lock_raw)
    requirements = blob("integrations/wechat-python311-linux.v1.requirements.txt")
    if (runtime["artifact_file"] != "integrations/artifacts/wechat-html-draft-1.0.0.zip"
            or runtime["artifact_sha256"] != digest(artifact)
            or runtime["lock_file"] != "integrations/wechat-python311-linux.v1.lock.json"
            or runtime["lock_sha256"] != digest(lock_raw)
            or lock["requirements_file"] != "wechat-python311-linux.v1.requirements.txt"
            or lock["requirements_sha256"] != digest(requirements)
            or lock["input_requirements_sha256"] != digest(files["scripts/requirements.txt"][0])
            or runtime["target"] != lock["target"]):
        raise RuntimeError("微信 Skill 发布 Python 依赖锁身份不匹配。")
    normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    packages = {normalize(item["package"]): item["version"] for item in lock["packages"]}
    if len(packages) != len(lock["packages"]) or packages != {normalize(k): v for k, v in dependencies["packages"].items()}:
        raise RuntimeError("微信 Skill 发布 Python 依赖闭包不匹配。")
    identities = {name: digest(_git_blob(commit, name)) for name in sorted(required | expected)}
    return {"resource_sha256": identities, "source_file_count": len(files),
            "artifact_sha256": digest(artifact), "lock_sha256": digest(lock_raw),
            "dependency_package_count": len(packages), "runtime_activation": "NOT_AUTHORIZED"}


def _static_asset_contract(commit: str) -> dict:
    index = _git_blob(commit, STATIC_INDEX)
    identities = {}
    for kind, path in STATIC_ASSETS.items():
        content = _git_blob(commit, path)
        digest = hashlib.sha256(content).hexdigest()
        identities[kind] = {"path": path, "sha256": digest, "version": digest[:STATIC_VERSION_LENGTH]}

    packaged_index = index
    for kind, placeholder in STATIC_VERSION_PLACEHOLDERS.items():
        if len(placeholder) != STATIC_VERSION_LENGTH:
            raise RuntimeError(f"静态版本占位符长度无效：{kind}")
        if packaged_index.count(placeholder) != 1:
            raise RuntimeError(f"静态版本占位符必须且只能出现一次：{kind}")
        packaged_index = packaged_index.replace(placeholder, identities[kind]["version"].encode("ascii"))

    if any(placeholder in packaged_index for placeholder in STATIC_VERSION_PLACEHOLDERS.values()):
        raise RuntimeError("发布 HTML 仍包含静态版本占位符。")
    expected_references = {
        "javascript": f'/static/workbench.js?v={identities["javascript"]["version"]}'.encode("ascii"),
        "stylesheet": f'/static/workbench.css?v={identities["stylesheet"]["version"]}'.encode("ascii"),
    }
    for kind, reference in expected_references.items():
        if packaged_index.count(reference) != 1:
            raise RuntimeError(f"发布 HTML 静态资源引用不匹配：{kind}")
    return {"source_index": index, "packaged_index": packaged_index, "identities": identities}


def _inject_and_verify_static_assets(raw_archive: Path, contract: dict) -> None:
    with tarfile.open(raw_archive, "r:") as archive:
        try:
            index_member = archive.getmember(STATIC_INDEX)
        except KeyError as exc:
            raise RuntimeError(f"发布归档缺少静态入口：{STATIC_INDEX}") from exc
        if not index_member.isfile():
            raise RuntimeError(f"发布静态入口不是普通文件：{STATIC_INDEX}")
        archived_index = archive.extractfile(index_member)
        if archived_index is None or archived_index.read() != contract["source_index"]:
            raise RuntimeError("发布归档静态入口与 Git source 不一致。")
        offset = index_member.offset_data
        size = index_member.size

    packaged_index = contract["packaged_index"]
    if len(packaged_index) != size:
        raise RuntimeError("静态版本注入不得改变归档成员长度。")
    with raw_archive.open("r+b") as archive_bytes:
        archive_bytes.seek(offset)
        archive_bytes.write(packaged_index)

    with tarfile.open(raw_archive, "r:") as archive:
        packaged_member = archive.extractfile(STATIC_INDEX)
        if packaged_member is None or packaged_member.read() != packaged_index:
            raise RuntimeError("发布归档静态入口注入验证失败。")
        for kind, identity in contract["identities"].items():
            try:
                asset_member = archive.extractfile(identity["path"])
            except KeyError as exc:
                raise RuntimeError(f"发布归档缺少静态文件：{identity['path']}") from exc
            if asset_member is None:
                raise RuntimeError(f"发布归档静态文件不可读：{identity['path']}")
            actual_sha256 = hashlib.sha256(asset_member.read()).hexdigest()
            if actual_sha256 != identity["sha256"] or actual_sha256[:STATIC_VERSION_LENGTH] != identity["version"]:
                raise RuntimeError(f"发布归档静态文件身份不匹配：{kind}")


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
    _static_asset_contract(commit)
    _wechat_resource_contract(commit, selected)
    return selected


def build(commit: str, output: Path) -> dict:
    selected = preflight(commit)
    static_contract = _static_asset_contract(commit)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        raw = Path(temp_dir) / "source.tar"
        subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", "--output", str(raw), commit, "--", *selected], check=True)
        _inject_and_verify_static_assets(raw, static_contract)
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
        "static_assets": static_contract["identities"],
        "skill_resources": _wechat_resource_contract(commit, selected),
    }


def write_manifest(result: dict, release_id: str, output: Path, binding_transition: dict | None = None,
                   *, forward_migrations: dict | None = None, deferred_skill: dict | None = None,
                   skill_package_staging: dict | None = None,
                   agent_productization_transition: dict | None = None,
                   runtime_only_release: dict | None = None, dual_source_release: dict | None = None) -> dict:
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
    if agent_productization_transition is not None:
        manifest["agent_productization_transition"] = agent_productization_transition
    if runtime_only_release is not None:
        manifest["runtime_only_release"] = runtime_only_release
    if dual_source_release is not None:
        from scripts.release_dual_source import APP_SOURCE, APP_TREE
        if result['source_commit']!=APP_SOURCE or git('rev-parse',APP_SOURCE+'^{tree}')!=APP_TREE:
            raise ValueError('dual_source_build_application_identity')
        if (git('rev-parse','HEAD'),git('rev-parse','HEAD^{tree}')) != (
                dual_source_release.get('tooling_source'),dual_source_release.get('tooling_tree')):
            raise ValueError('dual_source_build_tooling_identity')
        if git('status','--porcelain','--untracked-files=no'):
            raise ValueError('dual_source_build_dirty_tooling')
        manifest['dual_source_release']=dual_source_release
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
    parser.add_argument("--agent-productization-transition", type=Path)
    parser.add_argument("--runtime-only-release", type=Path)
    parser.add_argument("--dual-source-schema016", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if bool(args.release_id) != bool(args.manifest_output) or ((args.binding_transition or args.forward_migrations or args.deferred_skill or args.skill_package_staging or args.agent_productization_transition or args.runtime_only_release or args.dual_source_schema016) and not args.manifest_output):
        parser.error("--release-id 与 --manifest-output 必须同时提供。")
    commit = validate_commit(args.commit)
    if args.dual_source_schema016:
        from scripts.release_dual_source import APP_SOURCE,APP_TREE
        if commit!=APP_SOURCE or git('rev-parse',commit+'^{tree}')!=APP_TREE:
            parser.error('dual_source_build_application_identity')
        if any((args.binding_transition,args.forward_migrations,args.deferred_skill,args.skill_package_staging,
                args.agent_productization_transition,args.runtime_only_release)):
            parser.error('schema016_code_only_release')
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
            productization = json.loads(args.agent_productization_transition.read_text(encoding="utf-8")) if args.agent_productization_transition else None
            runtime_only = json.loads(args.runtime_only_release.read_text(encoding="utf-8")) if args.runtime_only_release else None
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Binding transition declaration is unreadable.") from exc
        pair=None
        if args.dual_source_schema016:
            from scripts.release_schema016 import PLAN,declaration
            pair=declaration(git('rev-parse','HEAD'),git('rev-parse','HEAD^{tree}')); migrations=PLAN
        write_manifest(result, args.release_id, args.manifest_output, transition,
                       forward_migrations=migrations, deferred_skill=deferred,
                       skill_package_staging=staging,
                       agent_productization_transition=productization, runtime_only_release=runtime_only,dual_source_release=pair)
        result["manifest"] = str(args.manifest_output)
    print(json.dumps({"status": "built", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
