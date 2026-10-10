"""Formal source archive coverage; no Registry mutation or external calls."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

from scripts import build_release


def commit(repo):
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "resource fixture"], check=True)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


@pytest.fixture
def resource_repo(bundle_git_repo, monkeypatch):
    source = Path(__file__).resolve().parents[1]
    target = bundle_git_repo / "enterprise_agent_poc"
    for directory in ("integrations", "skill_sources"):
        shutil.copytree(source / directory, target / directory)
    (target / "scripts").mkdir()
    for name in ("verify_wechat_skill.py", "lock_wechat_python_runtime.py", "wechat_revision_python_runtime.py"):
        shutil.copyfile(source / "scripts" / name, target / "scripts" / name)
    monkeypatch.setattr(build_release, "REPO", bundle_git_repo)
    return bundle_git_repo


def test_formal_archive_contains_exact_resources_and_dependency_closure(resource_repo, tmp_path):
    revision = commit(resource_repo)
    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    result = build_release.build(revision, first)
    build_release.build(revision, second)
    assert first.read_bytes() == second.read_bytes()
    resources = result["skill_resources"]
    assert resources["source_file_count"] == 13
    assert resources["dependency_package_count"] == 12
    assert resources["runtime_activation"] == "NOT_AUTHORIZED"
    assert resources["artifact_sha256"] == "4a140c878ae7057583089a4410cd1a23c5c95664988e6dd9700f75ed51d3b18c"
    with tarfile.open(first) as archive:
        for name, digest in resources["resource_sha256"].items():
            member = archive.getmember(name)
            assert member.isfile()
            raw = archive.extractfile(member).read()
            assert hashlib.sha256(raw).hexdigest() == digest
            assert raw == (resource_repo / name).read_bytes()
    manifest = build_release.write_manifest(result, "resource-fixture", tmp_path / "manifest.json")
    assert set(resources["resource_sha256"]) <= set(manifest["selected_files"])


@pytest.mark.parametrize("relative", [
    "integrations/wechat-python311-linux.v1.lock.json",
    "integrations/artifacts/wechat-html-draft-1.0.0.zip",
    "skill_sources/wechat-html-draft/1.0.0/scripts/wechat_draft.py",
    "scripts/wechat_revision_python_runtime.py",
])
def test_incomplete_resource_inventory_blocks(resource_repo, relative):
    (resource_repo / "enterprise_agent_poc" / relative).unlink()
    with pytest.raises(RuntimeError, match="微信 Skill 发布"):
        build_release.preflight(commit(resource_repo))


@pytest.mark.parametrize("relative", [
    "integrations/artifacts/wechat-html-draft-1.0.0.zip",
    "integrations/wechat-python311-linux.v1.requirements.txt",
    "skill_sources/wechat-html-draft/1.0.0/SKILL.md",
])
def test_changed_resource_bytes_block(resource_repo, relative):
    path = resource_repo / "enterprise_agent_poc" / relative
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(RuntimeError, match="微信 Skill 发布"):
        build_release.preflight(commit(resource_repo))


@pytest.mark.parametrize("relative", ["integrations/.env", "skill_sources/wechat-html-draft/1.0.0/private.key"])
def test_new_inventory_keeps_existing_secret_gate(resource_repo, relative):
    path = resource_repo / "enterprise_agent_poc" / relative
    path.write_bytes(b"not-a-real-secret")
    with pytest.raises(RuntimeError, match="禁止文件"):
        build_release.preflight(commit(resource_repo))
