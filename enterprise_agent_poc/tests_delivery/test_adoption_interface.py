"""Offline contract tests only; never an installed authority or Native receipt."""
import copy
import importlib.util
from pathlib import Path
import sys
import subprocess
import tarfile
import io
import hashlib

import pytest

SPEC = importlib.util.spec_from_file_location('adoption', Path(__file__).resolve().parents[1]/'scripts/test_delivery_adoption.py')
a = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a)


def fixture():
    identity = {'source': '9e6350684dea1d54cdfd17e5daaa1574bce75579',
                'tree': 'ccd024e6de1ca8971c261383894bf69b04bcddf9',
                'parent': '801fd27a92f32ecc87e9aec1a8c253a8942d1e92'}
    primary = {'source': '894bdd2a031725a0d0df3b95d6dfc26d08f0e4ab',
               'tree': 'bb9317207372ad1a215eaeebb7a2b0151a35be4d', 'parent': 'd'*40}
    r = dict(action=a.ACTION, environment='test', host='synthetic-test-host',
        operation_id='ef2631a5-96cc-4a70-aedc-88e8ff58749b',
        permissions={'isolated_qualify': True, 'primary_switch': False, 'production': False,
                     'preview': False, 'paid_calls': 0}, profile=a.PROFILE,
        limits={'scratch_bytes': 704*1024**2, 'aggregate_bytes': 768*1024**2,
                'host_reserve_bytes': 256*1024**2, 'min_free_fraction': .2,
                'memory_high_bytes': 1280*1024**2, 'memory_max_bytes': 1536*1024**2,
                'memory_swap_bytes': 0}, tooling=identity, primary=primary,
        files={'scripts/example.py': 'a'*64}, runtime={'files': {'python': 'b'*64}},
        operator={'name': 'synthetic-isolated', 'uid': 1234, 'gid': 1234},
        artifacts={'candidate': {'source': identity['parent']}, 'predecessor': {'source': primary['source']}})
    d = dict(kind='ACTUAL_OWNER_AUTHORIZED_06_ROOT_DECISION_RECORD', technical_action=a.ACTION,
        request_sha256=a.digest(r), actual_root_uid=0, owner_references=['SYNTHETIC_OFFLINE_ONLY'],
        operator='SYNTHETIC_INSTALLER', revoked=False, uses=1, production_authority=False,
        provider_budget=0, issued_at=100, expires_at=200, host=r['host'])
    o = {k: copy.deepcopy(r[k]) for k in ('host', 'primary', 'tooling', 'files', 'runtime', 'artifacts', 'operator')}
    o.update(free=10*1024**3, total=40*1024**3)
    return r, d, o


def test_exact_accepted_but_never_native_or_execution_grant():
    r, d, o = fixture()
    result = a.validate(r, d, o, 150)
    assert result['status'] == 'MATERIALS_VERIFIED'
    assert result['native_pass'] is False and result['execution_authorized'] is False
    assert result['primary_changes'] == 0


def test_real_frozen_git_tree_and_complete_file_hashes_contract():
    r, d, o = fixture()
    repo = Path(__file__).resolve().parents[2]
    source = r['tooling']['source']
    rows = subprocess.check_output(['git', '-C', str(repo), 'show', '-s',
                                   '--format=%H%n%T%n%P', source]).decode().splitlines()
    assert rows == list(r['tooling'].values())
    raw = subprocess.check_output(['git', '-C', str(repo), 'archive', source, 'enterprise_agent_poc/'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        pins = {m.name.removeprefix('enterprise_agent_poc/'): hashlib.sha256(archive.extractfile(m).read()).hexdigest()
                for m in archive if m.isfile()}
    assert pins['scripts/test_delivery_native.py'] == '86477505fb1cecf5ececb5d307a0b49d07cf689ecaa70fe0ef2324b71f538aa0'
    assert len(pins) > 100
    r['files'] = pins; o['files'] = dict(pins); d['request_sha256'] = a.digest(r)
    assert a.validate(r, d, o, 150)['status'] == 'MATERIALS_VERIFIED'
    o['files'].pop(next(iter(pins)))
    with pytest.raises(ValueError, match='TOOLING_FILE_PINS'): a.validate(r, d, o, 150)


def test_wrong_tree_rejected():
    r, d, o = fixture(); o['tooling']['tree'] = '0'*40
    with pytest.raises(ValueError, match='TOOLING_IDENTITY'): a.validate(r, d, o, 150)


@pytest.mark.parametrize('key,value', [
    ('files', {'scripts/example.py': 'f'*64}), ('tooling', {'source': 'a'*40}),
    ('primary', {'source': 'a'*40}), ('host', 'production'),
    ('runtime', {}), ('artifacts', {}), ('operator', {'uid': 0})])
def test_wrong_actual_material_or_identity_rejected(key, value):
    r, d, o = fixture(); o[key] = value
    with pytest.raises(ValueError): a.validate(r, d, o, 150)


@pytest.mark.parametrize('key,value', [('environment', 'production'), ('profile', 'OLD_V1'),
    ('permissions', {}), ('limits', {}), ('action', 'switch_primary')])
def test_even_request_bound_approval_cannot_expand_scope(key, value):
    r, d, o = fixture(); r[key] = value; d['request_sha256'] = a.digest(r)
    with pytest.raises(ValueError): a.validate(r, d, o, 150)


@pytest.mark.parametrize('key,value', [('expires_at', 150), ('issued_at', 151),
    ('revoked', True), ('uses', 2), ('kind', 'REQUEST_ONLY'), ('owner_references', []),
    ('request_sha256', 'f'*64), ('production_authority', True), ('provider_budget', 1)])
def test_bad_authority_rejected(key, value):
    r, d, o = fixture(); d[key] = value
    with pytest.raises(ValueError): a.validate(r, d, o, 150)


def test_different_business_source_same_tooling():
    r, d, o = fixture()
    frozen = copy.deepcopy(r['tooling'])
    for source in ('801fd27a92f32ecc87e9aec1a8c253a8942d1e92', 'd9063ee43bc65e821457221ad824f5dde84fe408'):
        r['artifacts']['candidate']['source'] = source
        o['artifacts'] = copy.deepcopy(r['artifacts']); d['request_sha256'] = a.digest(r)
        assert a.validate(r, d, o, 150)['tooling'] == frozen


def test_floor_includes_reservation():
    r, d, o = fixture(); o['free'] = int(o['total']*.2) + 1024**3
    with pytest.raises(ValueError, match='DISK_RESERVE'): a.validate(r, d, o, 150)


def test_unprivileged_finalize_never_reads_or_writes(monkeypatch):
    monkeypatch.setattr(a.sys, 'platform', 'win32')
    monkeypatch.setattr(a, 'read', lambda p: pytest.fail('must reject before record access'))
    with pytest.raises(ValueError, match='EXTERNAL_ROOT_INSTALLER_REQUIRED'):
        a.finalize('not-authority', 'not-authority')


def test_no_installer_or_service_or_issuer_commands():
    source = Path(a.__file__).read_text(encoding='utf-8')
    for forbidden in ('systemctl', 'sudo ', 'shutil', 'os.chown', 'os.symlink', 'def issue('):
        assert forbidden not in source
