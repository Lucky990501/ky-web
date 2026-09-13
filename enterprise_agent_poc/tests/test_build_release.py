import json
import tarfile

from scripts import build_release


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
