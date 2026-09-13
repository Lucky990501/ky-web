"""Isolated Git repositories are test fixtures, never commits in the project."""
from pathlib import Path
import os
import tempfile
import shutil
import subprocess

import pytest

# Set the real configuration names BEFORE any application module is imported.
# All default Settings objects now point to a brand-new marked local database.
_test_root = Path(tempfile.mkdtemp(prefix="ky-web-stage2-tests-"))
(_test_root / "stage2-isolated.marker").write_text("ky-web-stage2-isolated-v1")
os.environ.update(APP_ENV="test", ENTERPRISE_POC_DATABASE_URL=f"sqlite:///{_test_root}/poc.db",
    ENTERPRISE_POC_DATA_DIR=str(_test_root / "data"), ENTERPRISE_POC_OBJECT_STORAGE_DIR=str(_test_root / "objects"),
    ENTERPRISE_POC_OBJECT_STORAGE_PROVIDER="local", ENTERPRISE_POC_TASK_QUEUE="local",
    ENTERPRISE_POC_MCP_URL="http://127.0.0.1:18091/mcp", DEEPSEEK_API_KEY="unit-test-placeholder",
    GATEWAY_API_TOKEN="unit-test-placeholder")
os.environ.pop("REDIS_URL",None)
from app.settings import Settings
from scripts.stage2_isolation import assert_isolated
assert_isolated(Settings.from_env(),_test_root)


@pytest.fixture(autouse=True)
def forbid_default_or_network_database(monkeypatch):
    """Every test connection is guarded, including API/MCP/Worker initialization."""
    from app.store import POCStore
    from urllib.parse import urlparse,parse_qs
    original = POCStore.connection
    def guarded(store):
        if store.database_path is not None:
            path = store.database_path.resolve()
            forbidden = Path(__file__).resolve().parents[1] / ".runtime-data"
            assert not path.is_relative_to(forbidden), "Default local runtime DB access forbidden"
            assert path.is_relative_to(Path(tempfile.gettempdir()).resolve()) or path.is_relative_to(Path('/private/tmp')), "Test DB outside temporary roots"
        else:
            parsed = urlparse(store.database_url)
            query = parse_qs(parsed.query)
            root = Path(os.environ.get("STAGE1_POSTGRES_ROOT","/nonexistent"))
            assert not parsed.hostname and query.get('host') == [str(root / 'socket')], "Network / production DSN forbidden"
            assert root.parent == Path('/private/tmp') and (root / 'stage1-isolated.marker').read_text().strip() == 'ky-web-stage1-local-only'
        return original(store)
    monkeypatch.setattr(POCStore,"connection",guarded)


@pytest.fixture
def approved_bundle(tmp_path):
    target = tmp_path / "approved" / "skill_packages"
    shutil.copytree(Path(__file__).resolve().parents[1] / "skill_packages", target)
    return target


@pytest.fixture
def bundle_git_repo(tmp_path, approved_bundle):
    repo = tmp_path / "git-fixture"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for key, value in [("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
                       ("core.autocrlf", "false"), ("core.eol", "lf")]:
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    root = repo / "enterprise_agent_poc"
    shutil.copytree(approved_bundle, root / "skill_packages")
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "approved fixture"], check=True)
    return repo
