from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import bundled_skills as bundled
from app.agent_catalog import CATALOG
from app.product_store import ProductStore
from app.skill_registry import NativeSkillArchive, SkillRegistry, SkillRegistryError
from app.skills import SkillDeployment
from app.store import POCStore
from scripts import build_release, verify_bundled_skills


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit_fixture(repo):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture change")
    return git(repo, "rev-parse", "HEAD")


def manifest(root):
    return json.loads((root / "manifest.json").read_bytes())


def save_manifest(root, value):
    (root / "manifest.json").write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def registry(tmp_path, approved_bundle):
    store = POCStore(tmp_path / "registry.db")
    store.seed_demo_data()
    ProductStore(store).initialize()
    registry = SkillRegistry(store, tmp_path / "data", approved_bundle)
    registry.initialize()
    return registry


def snapshot(registry):
    with sqlite3.connect(registry._store.database_path) as connection:
        tables = ["skills", "skill_versions", "skill_packages", "agent_skill_bindings", "platform_admins"]
        rows = {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in tables}
    files = {p.relative_to(registry.data_root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
             for p in registry.data_root.rglob("*") if p.is_file()}
    return rows, files


def upgrade(registry, slug, agent, version="1.2.0"):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(f"{slug}/SKILL.md", f"# upgraded {slug}\n")
    item = registry.import_archive(slug, version, slug, "", content.getvalue(), "fixture-user")
    registry.publish(item["id"], "fixture-user")
    registry.bind_agent(agent, slug, version, "fixture-user")
    return item


def test_empty_registry_bootstraps_exact_historical_bytes_then_zero_writes(registry, monkeypatch):
    entries = bundled.validate_bundle(registry.bundled_root)
    for entry in entries:
        with registry._store.connection() as conn:
            row = conn.execute("SELECT p.storage_path,v.checksum FROM skill_versions v JOIN skills s ON s.id=v.skill_id JOIN skill_packages p ON p.skill_version_id=v.id WHERE s.slug=? AND v.version=?", (entry["skill_slug"], "1.0.0")).fetchone()
        assert Path(row["storage_path"]).read_bytes() == (registry.bundled_root / entry["artifact_path"]).read_bytes()
        assert row["checksum"] == entry["artifact_sha256"]
        assert b"\r\n" in (registry.published_root / entry["skill_slug"] / "1.0.0" / "SKILL.md").read_bytes()
        assert b"\r" not in (registry.bundled_root / entry["source_identity"]["path"] / "SKILL.md").read_bytes()
    assert not hasattr(SkillRegistry, "_zip_directory")
    before = snapshot(registry)
    database_before = registry._store.database_path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("restart must not build/import/extract/publish")
    monkeypatch.setattr(registry, "_upsert_import", forbidden)
    monkeypatch.setattr(registry, "publish", forbidden)
    monkeypatch.setattr(NativeSkillArchive, "extract", forbidden)
    monkeypatch.setattr(bundled, "deterministic_zip", forbidden)
    registry.initialize()
    assert registry.verify_bootstrap()["mode"] == "reuse"
    assert snapshot(registry) == before
    assert registry._store.database_path.read_bytes() == database_before
    for agent in CATALOG.values():
        assert registry.manifest_for_agent(agent.id) == agent.skill_manifest


def test_api_lifespan_restart_writes_no_registry_sql(registry, monkeypatch):
    from app import main
    from app.settings import settings
    before = snapshot(registry)
    original_connect = sqlite3.connect
    statements = []
    def trace_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(sqlite3, "connect", trace_connect)
    monkeypatch.setattr(main, "store", registry._store)
    monkeypatch.setattr(main, "product_store", ProductStore(registry._store))
    monkeypatch.setattr(main, "skill_registry", registry)
    monkeypatch.setattr(main, "settings", replace(settings, environment="development", bootstrap_demo_data=False, task_queue="redis"))
    async def restart():
        for _ in range(2):
            async with main.lifespan(main.app):
                pass
    asyncio.run(restart())
    assert snapshot(registry) == before
    for statement in statements:
        upper = statement.upper()
        if any(table.upper() in upper for table in ["skill_versions", "skill_packages", "agent_skill_bindings", "platform_admins"]):
            assert not upper.lstrip().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"))


@pytest.mark.parametrize("mutation", [b" ", b"\n# comment\n", b"\nchanged body\n"])
def test_source_byte_changes_block(approved_bundle, mutation):
    path = approved_bundle / "poster-design" / "1.0.0" / "SKILL.md"
    path.write_bytes(path.read_bytes() + mutation)
    with pytest.raises(bundled.BundledSkillError, match="source lock"):
        bundled.validate_bundle(approved_bundle)


def test_source_file_set_and_executable_bit_block(approved_bundle):
    path = approved_bundle / "poster-design" / "1.0.0" / "extra.md"
    path.write_bytes(b"extra")
    with pytest.raises(bundled.BundledSkillError, match="file set"):
        bundled.validate_bundle(approved_bundle)
    path.unlink()
    source = approved_bundle / "poster-design" / "1.0.0" / "SKILL.md"
    source.chmod(0o755)
    with pytest.raises(bundled.BundledSkillError, match="source lock"):
        bundled.validate_bundle(approved_bundle)


def test_source_and_manifest_cannot_replace_historical_approval(approved_bundle):
    data = manifest(approved_bundle)
    entry = data["skills"][0]
    source = approved_bundle / entry["source_identity"]["path"] / "SKILL.md"
    source.write_bytes(source.read_bytes() + b"real change")
    entry["source_identity"]["files"][0]["sha256"] = bundled.sha256(source.read_bytes())
    save_manifest(approved_bundle, data)
    with pytest.raises(bundled.BundledSkillError, match="historical"):
        bundled.validate_bundle(approved_bundle)


@pytest.mark.parametrize("slug", list(bundled.HISTORICAL_IDENTITIES))
def test_any_historical_artifact_byte_change_blocks(approved_bundle, slug):
    path = approved_bundle / slug / "1.0.0.zip"
    path.write_bytes(path.read_bytes() + b"\0")
    with pytest.raises(bundled.BundledSkillError, match="artifact lock"):
        bundled.validate_bundle(approved_bundle)


def test_duplicate_manifest_entry_and_json_key_block(approved_bundle):
    data = manifest(approved_bundle)
    data["skills"].append(data["skills"][0])
    with pytest.raises(bundled.BundledSkillError, match="duplicate"):
        bundled.parse_manifest(json.dumps(data).encode())
    with pytest.raises(bundled.BundledSkillError, match="duplicate"):
        bundled.parse_manifest(b'{"schema_version":1,"schema_version":1}')


def test_draft_and_partial_registry_never_auto_repaired(registry):
    with registry._store.connection() as conn:
        conn.execute("UPDATE skill_versions SET status='draft' WHERE version='1.0.0'")
    before = snapshot(registry)
    with pytest.raises(SkillRegistryError, match="draft"):
        registry.initialize()
    assert snapshot(registry) == before


def test_missing_bundled_version_blocks_existing_registry(registry):
    with registry._store.connection() as conn:
        conn.execute("DELETE FROM agent_skill_bindings WHERE skill_id=(SELECT id FROM skills WHERE slug='poster-design')")
        conn.execute("DELETE FROM skill_packages WHERE skill_version_id=(SELECT id FROM skill_versions WHERE skill_id=(SELECT id FROM skills WHERE slug='poster-design'))")
        conn.execute("DELETE FROM skill_versions WHERE skill_id=(SELECT id FROM skills WHERE slug='poster-design')")
    before = snapshot(registry)
    with pytest.raises(SkillRegistryError, match="missing bundled"):
        registry.initialize()
    assert snapshot(registry) == before


def test_deprecated_and_explicit_unbind_survive_restart(registry):
    registry.unbind_agent("copywriting-agent", "social-copywriting")
    with registry._store.connection() as conn:
        version_id = conn.execute("SELECT v.id FROM skill_versions v JOIN skills s ON s.id=v.skill_id WHERE s.slug='social-copywriting'").fetchone()["id"]
    registry.deprecate(version_id)
    before = snapshot(registry)
    registry.initialize()
    assert snapshot(registry) == before
    assert "social-copywriting" not in registry.manifest_for_agent("copywriting-agent")
    assert registry.version(version_id)["status"] == "deprecated"


def test_current_12_bindings_profile_sync_and_rollback(registry, tmp_path):
    from app.domain import RuntimeProfile
    upgrade(registry, "poster-design", "image-agent")
    upgrade(registry, "campaign-planning", "campaign-agent")
    def profiles():
        return {agent: RuntimeProfile.build(tenant_id="fixture", agent_id=agent, model_provider_id="fixture", model_id="fixture", reasoning_effort="high", skill_manifest=registry.manifest_for_agent(agent)) for agent in CATALOG}
    before = profiles()
    registry.initialize()
    assert profiles() == before
    assert registry.manifest_for_agent("image-agent") == {"poster-design": "1.2.0"}
    assert registry.manifest_for_agent("campaign-agent") == {"campaign-planning": "1.2.0", "event-copywriting": "1.0.0"}
    target = tmp_path / "skills"
    deployment = SkillDeployment(registry.published_root)
    deployment.deploy(registry.manifest_for_agent("image-agent"), target)
    assert b"upgraded" in (target / "poster-design" / "SKILL.md").read_bytes()
    registry.bind_agent("image-agent", "poster-design", "1.0.0", "fixture-user")
    deployment.deploy(registry.manifest_for_agent("image-agent"), target)
    assert (target / "poster-design" / "SKILL.md").read_bytes() == (registry.published_root / "poster-design" / "1.0.0" / "SKILL.md").read_bytes()
    registry.bind_agent("image-agent", "poster-design", "1.2.0", "fixture-user")
    registry.initialize()
    assert profiles() == before


@pytest.mark.parametrize("damage", ["bytes", "checksum", "missing", "cache", "extra_cache"])
def test_preflight_blocks_package_and_cache_damage_without_writes(registry, damage):
    with registry._store.connection() as conn:
        row = conn.execute("SELECT v.id,p.storage_path FROM skill_versions v JOIN skill_packages p ON p.skill_version_id=v.id LIMIT 1").fetchone()
        if damage == "checksum":
            conn.execute("UPDATE skill_versions SET checksum=? WHERE id=?", ("0" * 64, row["id"]))
    package = Path(row["storage_path"])
    version = registry.version(row["id"])
    cache = registry.published_root / version["slug"] / version["version"]
    if damage == "bytes":
        package.write_bytes(package.read_bytes() + b"bad")
    elif damage == "missing":
        package.unlink()
    elif damage == "cache":
        (cache / "SKILL.md").write_bytes(b"bad")
    elif damage == "extra_cache":
        (cache / "extra.txt").write_bytes(b"bad")
    before = snapshot(registry)
    with pytest.raises(SkillRegistryError):
        registry.verify_bootstrap()
    assert snapshot(registry) == before


def test_preflight_blocks_dangling_status_binding(registry):
    with registry._store.connection() as conn:
        conn.execute("UPDATE skill_versions SET status='deprecated'")
    with pytest.raises(SkillRegistryError, match="Binding"):
        registry.verify_bootstrap()


def test_preflight_connection_enforces_read_only(registry):
    with registry._read_connection() as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM skills")
    assert registry.verify_bootstrap()["status"] == "ok"


def test_registry_unknown_package_identity_blocks_even_when_internal_hashes_match(registry):
    with registry._store.connection() as conn:
        row = conn.execute("SELECT v.id,p.storage_path FROM skill_versions v JOIN skill_packages p ON p.skill_version_id=v.id LIMIT 1").fetchone()
        path = Path(row["storage_path"])
        content = path.read_bytes() + b"metadata change"
        path.write_bytes(content)
        digest = bundled.sha256(content)
        conn.execute("UPDATE skill_versions SET checksum=? WHERE id=?", (digest, row["id"]))
        conn.execute("UPDATE skill_packages SET sha256=?,size_bytes=? WHERE skill_version_id=?", (digest, len(content), row["id"]))
    with pytest.raises(SkillRegistryError, match="identity"):
        registry.verify_bootstrap()


def test_normal_upload_raw_bytes_and_published_metadata_difference_stay_strict(registry):
    upgrade(registry, "poster-design", "image-agent")
    with registry._store.connection() as conn:
        row = conn.execute("SELECT v.id,p.storage_path FROM skill_versions v JOIN skills s ON s.id=v.skill_id JOIN skill_packages p ON p.skill_version_id=v.id WHERE s.slug='poster-design' AND v.version='1.2.0'").fetchone()
    raw = Path(row["storage_path"]).read_bytes()
    with pytest.raises(SkillRegistryError, match="不可覆盖"):
        registry.import_archive("poster-design", "1.2.0", "poster", "", raw + b"extra ZIP bytes", "fixture-user")
    Path(row["storage_path"]).write_bytes(raw + b"tampered")
    with pytest.raises(SkillRegistryError, match="checksum"):
        registry.test_version(row["id"])


def test_future_zip_fixed_bytes_and_metadata_and_no_historical_rebuild():
    files = {"scripts/check.py": (b"print('ok')\n", "100755"), "SKILL.md": (b"# Future\n", "100644")}
    first = bundled.deterministic_zip("poster-design", "1.3.0", files)
    second = bundled.deterministic_zip("poster-design", "1.3.0", dict(reversed(list(files.items()))))
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == ["poster-design/SKILL.md", "poster-design/scripts/check.py"]
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.create_system == 3
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.extra == info.comment == b""
            assert info.flag_bits == 0
        assert archive.infolist()[1].external_attr >> 16 == 0o100755
    with pytest.raises(bundled.BundledSkillError, match="historical"):
        bundled.deterministic_zip("poster-design", "1.0.0", files)
    with pytest.raises(bundled.BundledSkillError, match="LF"):
        bundled.deterministic_zip("poster-design", "1.3.0", {"SKILL.md": (b"# Future\r\n", "100644")})


def add_future(root):
    data = manifest(root)
    files = {"SKILL.md": (b"# Future\n", "100644")}
    path = root / "poster-design" / "1.3.0"
    path.mkdir()
    (path / "SKILL.md").write_bytes(files["SKILL.md"][0])
    artifact = bundled.deterministic_zip("poster-design", "1.3.0", files)
    (root / "poster-design" / "1.3.0.zip").write_bytes(artifact)
    data["skills"].append({"skill_slug": "poster-design", "version": "1.3.0", "source_identity": {"path": "poster-design/1.3.0", "files": [{"path": "SKILL.md", "sha256": bundled.sha256(files["SKILL.md"][0]), "git_mode": "100644"}]}, "artifact_sha256": bundled.sha256(artifact), "artifact_path": "poster-design/1.3.0.zip", "bootstrap_default": [], "builder_policy": bundled.BUILDER_POLICY, "legacy_artifact": False})
    save_manifest(root, data)


def test_future_identity_cannot_change_source_artifact_and_manifest_together(bundle_git_repo):
    root = bundle_git_repo / "enterprise_agent_poc" / "skill_packages"
    add_future(root)
    approved = commit_fixture(bundle_git_repo)
    assert bundled.validate_git_bundle(bundle_git_repo, approved)
    data = manifest(root)
    entry = data["skills"][-1]
    changed = b"# Real business change\n"
    (root / entry["source_identity"]["path"] / "SKILL.md").write_bytes(changed)
    entry["source_identity"]["files"][0]["sha256"] = bundled.sha256(changed)
    artifact = bundled.deterministic_zip("poster-design", "1.3.0", {"SKILL.md": (changed, "100644")})
    (root / entry["artifact_path"]).write_bytes(artifact)
    entry["artifact_sha256"] = bundled.sha256(artifact)
    save_manifest(root, data)
    changed_commit = commit_fixture(bundle_git_repo)
    with pytest.raises(bundled.BundledSkillError, match="committed version identity"):
        bundled.validate_git_bundle(bundle_git_repo, changed_commit)


@pytest.mark.parametrize("autocrlf", ["false", "true", "input"])
def test_git_preflight_artifact_bytes_ignore_checkout_policy(bundle_git_repo, autocrlf):
    git(bundle_git_repo, "config", "core.autocrlf", autocrlf)
    root = bundle_git_repo / "enterprise_agent_poc" / "skill_packages"
    entries = bundled.validate_git_bundle(bundle_git_repo, "HEAD")
    for entry in entries:
        source = root / entry["source_identity"]["path"] / "SKILL.md"
        source.write_bytes(source.read_bytes().replace(b"\n", b"\r\n"))
        artifact = subprocess.check_output(["git", "-C", str(bundle_git_repo), "show", f"HEAD:enterprise_agent_poc/skill_packages/{entry['artifact_path']}"])
        assert bundled.sha256(artifact) == entry["artifact_sha256"]
    assert bundled.validate_git_bundle(bundle_git_repo, "HEAD")


def test_release_build_blocks_committed_executable_bit_change(bundle_git_repo, tmp_path, monkeypatch):
    root = bundle_git_repo / "enterprise_agent_poc" / "skill_packages"
    (root / "poster-design" / "1.0.0" / "SKILL.md").chmod(0o755)
    commit = commit_fixture(bundle_git_repo)
    monkeypatch.setattr(build_release, "REPO", bundle_git_repo)
    with pytest.raises(bundled.BundledSkillError, match="source lock"):
        build_release.build(commit, tmp_path / "blocked.tar.gz")


@pytest.mark.parametrize("name", [".env", "node_modules/junk.js", ".runtime-data/poc.db"])
def test_release_build_blocks_forbidden_files(bundle_git_repo, tmp_path, monkeypatch, name):
    path = bundle_git_repo / "enterprise_agent_poc" / "app" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    commit = commit_fixture(bundle_git_repo)
    monkeypatch.setattr(build_release, "REPO", bundle_git_repo)
    with pytest.raises(RuntimeError, match="禁止文件"):
        build_release.build(commit, tmp_path / "blocked.tar.gz")


def test_preflight_cli_sanitizes_driver_exception(monkeypatch, capsys):
    monkeypatch.setattr(SkillRegistry, "verify_bootstrap", lambda self: (_ for _ in ()).throw(RuntimeError("postgresql://private:secret-token@host/db")))
    assert verify_bundled_skills.main() == 2
    output = capsys.readouterr().out
    assert "BLOCKED" in output
    assert "secret-token" not in output and "postgresql://" not in output


def test_preflight_does_not_initialize_missing_database(tmp_path, monkeypatch):
    missing = tmp_path / "absent.db"
    monkeypatch.setenv("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{missing}")
    monkeypatch.setenv("ENTERPRISE_POC_DATA_DIR", str(tmp_path / "absent-data"))
    assert verify_bundled_skills.main() == 2
    assert not missing.exists()
    assert not (tmp_path / "absent-data").exists()


def test_bootstrap_failure_rolls_back_rows_and_next_attempt_blocks_residual_before_write(tmp_path, approved_bundle, monkeypatch):
    store = POCStore(tmp_path / "fresh.db")
    store.seed_demo_data()
    ProductStore(store).initialize()
    registry = SkillRegistry(store, tmp_path / "data", approved_bundle)
    original_extract = NativeSkillArchive.extract
    calls = []
    def fail_second(cls, *args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise SkillRegistryError("injected extraction failure")
        return original_extract(*args, **kwargs)
    monkeypatch.setattr(NativeSkillArchive, "extract", classmethod(fail_second))
    with pytest.raises(SkillRegistryError, match="injected"):
        registry.initialize()
    with store.connection() as conn:
        for table in ["skills", "skill_versions", "skill_packages", "agent_skill_bindings"]:
            assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
    before = snapshot(registry)
    with pytest.raises(SkillRegistryError, match="Unregistered"):
        registry.initialize()
    assert snapshot(registry) == before


def test_concurrent_first_install_seeds_once(tmp_path, approved_bundle):
    store = POCStore(tmp_path / "fresh.db")
    store.seed_demo_data()
    ProductStore(store).initialize()
    def initialize():
        registry = SkillRegistry(store, tmp_path / "data", approved_bundle)
        registry.initialize()
        return registry.verify_bootstrap()["status"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: initialize(), range(2))) == ["ok", "ok"]
    with store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM skill_versions").fetchone()["n"] == 5
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_skill_bindings").fetchone()["n"] == 5


def test_partial_schema_blocks_without_repair(tmp_path, approved_bundle):
    store = POCStore(tmp_path / "partial.db")
    store.seed_demo_data()
    ProductStore(store).initialize()
    with store.connection() as conn:
        conn.execute("CREATE TABLE skills (id TEXT PRIMARY KEY, slug TEXT)")
    registry = SkillRegistry(store, tmp_path / "data", approved_bundle)
    with pytest.raises(SkillRegistryError, match="Partial Skill Registry schema"):
        registry.initialize()
    assert not registry.data_root.exists()


def test_future_published_version_restart_does_not_rebuild_zip(registry, monkeypatch):
    add_future(registry.bundled_root)
    raw = (registry.bundled_root / "poster-design" / "1.3.0.zip").read_bytes()
    item = registry.import_archive("poster-design", "1.3.0", "poster", "", raw, "fixture-user")
    registry.publish(item["id"], "fixture-user")
    before = snapshot(registry)
    def forbidden(*args, **kwargs):
        raise AssertionError("deterministic builder must be offline only")
    monkeypatch.setattr(bundled, "deterministic_zip", forbidden)
    registry.initialize()
    assert snapshot(registry) == before


def test_future_offline_builder_reads_git_not_checkout(bundle_git_repo):
    root = bundle_git_repo / "enterprise_agent_poc" / "skill_packages"
    add_future(root)
    commit = commit_fixture(bundle_git_repo)
    expected = (root / "poster-design" / "1.3.0.zip").read_bytes()
    (root / "poster-design" / "1.3.0" / "SKILL.md").write_bytes(b"uncommitted checkout drift\r\n")
    assert bundled.future_artifact_from_git(bundle_git_repo, commit, "poster-design", "1.3.0") == expected


@pytest.mark.parametrize("path", [".", "../escape", "/absolute", "assets/.env", "scripts/file\n.py"])
def test_unsafe_future_paths_block(path):
    with pytest.raises(bundled.BundledSkillError):
        bundled.deterministic_zip("poster-design", "1.3.0", {"SKILL.md": (b"# Future\n", "100644"), path: (b"bad", "100644")})


def test_release_preflight_blocks_secret_material_and_symlinks(bundle_git_repo, tmp_path, monkeypatch):
    path = bundle_git_repo / "enterprise_agent_poc" / "app" / "private.txt"
    path.write_bytes(b"-----BEGIN PRIVATE KEY-----\nfixture, not a real key\n")
    commit = commit_fixture(bundle_git_repo)
    monkeypatch.setattr(build_release, "REPO", bundle_git_repo)
    with pytest.raises(RuntimeError, match="秘密材料"):
        build_release.preflight(commit)
    path.unlink()
    path.symlink_to("main.py")
    commit = commit_fixture(bundle_git_repo)
    with pytest.raises(RuntimeError, match="禁止文件"):
        build_release.preflight(commit)


def test_published_cache_executable_drift_blocks(registry):
    path = registry.published_root / "poster-design" / "1.0.0" / "SKILL.md"
    path.chmod(0o700)
    with pytest.raises(SkillRegistryError, match="executable"):
        registry.verify_bootstrap()


def test_postgres_reader_uses_read_only_session_options(tmp_path, monkeypatch):
    import psycopg
    calls = []
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, sql, params):
            calls.append((sql, params))
            return None
    def connect(url, **kwargs):
        assert kwargs["options"] == "-c default_transaction_read_only=on"
        assert kwargs["row_factory"]
        return Connection()
    monkeypatch.setattr(psycopg, "connect", connect)
    registry = SkillRegistry(POCStore("postgresql://fixture@localhost/fixture"), tmp_path / "data", tmp_path / "bundle")
    with registry._read_connection() as conn:
        conn.execute("SELECT ? AS value", (1,))
    assert calls == [("SELECT %s AS value", (1,))]


def test_preflight_cli_passes_all_checks_and_writes_nothing(tmp_path, monkeypatch, capsys):
    store = POCStore(tmp_path / "cli.db")
    store.seed_demo_data()
    ProductStore(store).initialize()
    data_dir = tmp_path / "runtime"
    registry = SkillRegistry(store, data_dir / "skill-registry", Path(__file__).resolve().parents[1] / "skill_packages")
    registry.initialize()
    before = snapshot(registry)
    monkeypatch.setenv("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{store.database_path}")
    monkeypatch.setenv("ENTERPRISE_POC_DATA_DIR", str(data_dir))
    assert verify_bundled_skills.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok" and result["mode"] == "reuse"
    assert len(result["checks"]) == 6
    assert snapshot(registry) == before


def test_shallow_history_cannot_bypass_frozen_identity(bundle_git_repo):
    (bundle_git_repo / ".git" / "shallow").write_text(git(bundle_git_repo, "rev-parse", "HEAD") + "\n", encoding="ascii")
    with pytest.raises(bundled.BundledSkillError, match="complete Git history"):
        bundled.validate_git_bundle(bundle_git_repo, "HEAD")
