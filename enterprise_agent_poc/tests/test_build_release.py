import json
import hashlib
import subprocess
import tarfile

from scripts import build_release


def _archive_file(archive_path, member):
    with tarfile.open(archive_path) as archive:
        extracted = archive.extractfile(member)
        assert extracted is not None
        return extracted.read()


def _commit(repo, message):
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def test_release_archive_is_reproducible_and_source_backed(tmp_path, bundle_git_repo, monkeypatch):
    monkeypatch.setattr(build_release, "REPO", bundle_git_repo)
    commit = build_release.validate_commit("HEAD")
    first_path = tmp_path / "first.tar.gz"
    second_path = tmp_path / "second.tar.gz"

    first = build_release.build(commit, first_path)
    second = build_release.build(commit, second_path)

    assert first_path.read_bytes() == second_path.read_bytes()
    assert first["archive_sha256"] == second["archive_sha256"]
    assert first["source_commit"] == commit
    assert first["selected_files"] == second["selected_files"]
    assert first["files"] == len(first["selected_files"])
    assert first["build_platform"]
    assert first["static_assets"] == second["static_assets"]

    packaged_index = _archive_file(first_path, build_release.STATIC_INDEX)
    for identity in first["static_assets"].values():
        packaged_asset = _archive_file(first_path, identity["path"])
        assert hashlib.sha256(packaged_asset).hexdigest() == identity["sha256"]
        assert identity["version"] == identity["sha256"][:16]
        assert f'?v={identity["version"]}'.encode() in packaged_index
    assert not any(token in packaged_index for token in build_release.STATIC_VERSION_PLACEHOLDERS.values())

    with tarfile.open(first_path) as archive:
        names = archive.getnames()
    assert "enterprise_agent_poc/app/main.py" in names
    assert "enterprise_agent_poc/skill_packages/manifest.json" in names
    assert "enterprise_agent_poc/skill_packages/poster-design/1.0.0.zip" in names
    assert not any(name.endswith((".env", ".pem", ".key")) for name in names)

    manifest_path = tmp_path / "release-manifest.json"
    manifest = build_release.write_manifest(first, "test-release", manifest_path)
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    assert manifest["source_commit"] == commit
    assert manifest["archive_sha256"] == first["archive_sha256"]
    assert manifest["selected_file_count"] == len(manifest["selected_files"])


def test_asset_version_changes_only_for_changed_asset(tmp_path, bundle_git_repo, monkeypatch):
    monkeypatch.setattr(build_release, "REPO", bundle_git_repo)
    base = build_release.build(build_release.validate_commit("HEAD"), tmp_path / "base.tar.gz")

    javascript = bundle_git_repo / build_release.STATIC_ASSETS["javascript"]
    javascript.write_bytes(javascript.read_bytes().replace(b"fixture", b"fixturf", 1))
    js_commit = _commit(bundle_git_repo, "change javascript")
    changed_js = build_release.build(js_commit, tmp_path / "changed-js.tar.gz")
    assert changed_js["static_assets"]["javascript"]["version"] != base["static_assets"]["javascript"]["version"]
    assert changed_js["static_assets"]["stylesheet"]["version"] == base["static_assets"]["stylesheet"]["version"]

    stylesheet = bundle_git_repo / build_release.STATIC_ASSETS["stylesheet"]
    stylesheet.write_bytes(stylesheet.read_bytes().replace(b"block", b"clock", 1))
    css_commit = _commit(bundle_git_repo, "change stylesheet")
    changed_css = build_release.build(css_commit, tmp_path / "changed-css.tar.gz")
    assert changed_css["static_assets"]["javascript"]["version"] == changed_js["static_assets"]["javascript"]["version"]
    assert changed_css["static_assets"]["stylesheet"]["version"] != changed_js["static_assets"]["stylesheet"]["version"]


def test_build_fails_closed_when_required_asset_is_missing(tmp_path, bundle_git_repo, monkeypatch):
    monkeypatch.setattr(build_release, "REPO", bundle_git_repo)
    (bundle_git_repo / build_release.STATIC_ASSETS["javascript"]).unlink()
    commit = _commit(bundle_git_repo, "remove required asset")
    try:
        build_release.build(commit, tmp_path / "missing.tar.gz")
    except RuntimeError as exc:
        assert build_release.STATIC_ASSETS["javascript"] in str(exc)
    else:
        raise AssertionError("missing release asset must block the build")


def test_build_fails_closed_for_missing_placeholder(tmp_path, bundle_git_repo, monkeypatch):
    monkeypatch.setattr(build_release, "REPO", bundle_git_repo)
    index = bundle_git_repo / build_release.STATIC_INDEX
    index.write_bytes(index.read_bytes().replace(build_release.STATIC_VERSION_PLACEHOLDERS["javascript"], b"manual-version-01"))
    commit = _commit(bundle_git_repo, "remove javascript placeholder")
    try:
        build_release.build(commit, tmp_path / "missing-placeholder.tar.gz")
    except RuntimeError as exc:
        assert "占位符" in str(exc)
    else:
        raise AssertionError("missing placeholder must block the build")
