from dataclasses import replace
import os
from pathlib import Path
import tempfile

import pytest

from app.settings import Settings
from scripts.stage2_isolation import IsolationError,REQUIRED,assert_isolated


@pytest.fixture
def isolated(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="ky-web-stage2-gate-") as directory:
        root = Path(directory)
        (root/'stage2-isolated.marker').write_text('ky-web-stage2-isolated-v1')
        monkeypatch.setenv('APP_ENV','test')
        monkeypatch.setenv('ENTERPRISE_POC_DATABASE_URL',f'sqlite:///{root}/poc.db')
        monkeypatch.setenv('ENTERPRISE_POC_DATA_DIR',str(root/'data'))
        monkeypatch.setenv('ENTERPRISE_POC_OBJECT_STORAGE_DIR',str(root/'objects'))
        monkeypatch.setenv('ENTERPRISE_POC_OBJECT_STORAGE_PROVIDER','local')
        monkeypatch.setenv('ENTERPRISE_POC_TASK_QUEUE','local')
        monkeypatch.setenv('ENTERPRISE_POC_MCP_URL','http://127.0.0.1:18091/mcp')
        yield root,Settings.from_env()


def test_actual_settings_pass_before_any_database_exists(isolated):
    root,settings = isolated
    flags,snapshot = assert_isolated(settings,root)
    assert all(flags[k] for k in ('database_is_isolated','data_dir_is_isolated','object_storage_is_isolated'))
    assert not settings.database_path.exists()
    assert assert_isolated(settings,root,snapshot)[0] == flags


@pytest.mark.parametrize('name',REQUIRED)
def test_missing_variable_blocks_before_initialization(isolated,monkeypatch,name):
    root,settings = isolated
    monkeypatch.delenv(name)
    with pytest.raises(IsolationError,match='missing'):
        assert_isolated(settings,root)
    assert not settings.database_path.exists()


@pytest.mark.parametrize('field',['database_path','data_dir','object_storage_dir'])
def test_default_or_external_path_blocks(isolated,field):
    root,settings = isolated
    forbidden = Path(__file__).resolve().parents[1]/'.runtime-data'
    for path in (forbidden,Path('/opt/enterprise-agent-workbench/shared')):
        with pytest.raises(IsolationError):
            assert_isolated(replace(settings,**{field:path}),root)
    assert not settings.database_path.exists()


def test_production_dsn_and_environment_block(isolated):
    root,settings = isolated
    with pytest.raises(IsolationError):
        assert_isolated(replace(settings,environment='production'),root)
    with pytest.raises(IsolationError):
        assert_isolated(replace(settings,database_url='postgresql://production.invalid/db',database_path=None),root)


def test_api_mcp_worker_configuration_mismatch_blocks(isolated):
    root,settings = isolated
    _,snapshot = assert_isolated(settings,root)
    with pytest.raises(IsolationError,match='mismatch'):
        assert_isolated(replace(settings,platform_mcp_url='http://127.0.0.1:9999/mcp'),root,snapshot)


def test_symlink_escape_blocks(isolated,tmp_path):
    root,settings = isolated
    (root/'data').symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(IsolationError):
        assert_isolated(settings,root)
