"""UID1000/Linux metadata fixtures; not Linux Native execution evidence."""
import io
import json
import stat
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest
from test_adoption_interface import a


@pytest.fixture
def fs(monkeypatch):
    nodes = {}
    class P(PurePosixPath):
        def lstat(self):
            n = nodes[str(self)]
            return SimpleNamespace(st_uid=n.get('uid', 0), st_gid=n.get('gid', n.get('uid', 0)),
                st_mode=n['mode'], st_nlink=1, st_size=len(n.get('raw', b'')))
        stat = lstat
        def is_symlink(self): return stat.S_ISLNK(self.lstat().st_mode)
        def is_file(self): return stat.S_ISREG(self.lstat().st_mode)
        def is_dir(self): return stat.S_ISDIR(self.lstat().st_mode)
        def resolve(self, strict=False):
            n = nodes[str(self)]
            return P(n['target']) if 'target' in n else self
        def read_bytes(self): return nodes[str(self)]['raw']
        def read_text(self): return self.read_bytes().decode()
        def open(self, mode): return io.BytesIO(self.read_bytes())
    def directory(p, uid=0, mode=0o755):
        nodes[str(p)] = {'uid': uid, 'mode': stat.S_IFDIR|mode}
    def file(p, raw, uid=0, mode=0o444):
        nodes[str(p)] = {'uid': uid, 'mode': stat.S_IFREG|mode, 'raw': raw}
    base = P('/opt/enterprise-agent-workbench-test')
    root = base/'releases/current-894'; gd = base/'shared/source-identities/current-894'
    for p in {base, *root.parents, *gd.parents, P('/etc'), P('/etc/proof')}:
        directory(p)
    directory(root, 1000, 0o555); directory(gd, 1000, 0o555)
    nodes[str(base/'current')] = {'uid': 0, 'mode': stat.S_IFLNK|0o777, 'target': str(root)}
    file(root/'.git', ('gitdir: '+str(gd)+'\n').encode(), 1000)
    source = '894bdd2a031725a0d0df3b95d6dfc26d08f0e4ab'
    file(gd/'HEAD', (source+'\n').encode(), 1000)
    file(root/'sample.py', b'unchanged source', 1000)
    monkeypatch.setattr(a, 'Path', P)
    monkeypatch.setattr(a.os, 'listxattr', lambda p: [], raising=False)
    monkeypatch.setattr(a, 'running_cwd', lambda unit: str(root/'enterprise_agent_poc'))
    ident = {'source': source, 'tree': 'b'*40, 'parent': 'c'*40}
    prepare = {'source': source, 'tree': ident['tree'], 'git_parent': ident['parent'],
               'files': {n: a.sha(root/n) for n in ('.git', 'sample.py')}}
    file('/etc/proof/prepare.json', json.dumps(prepare).encode())
    nodes['/etc/proof/prepare.json'].update(gid=1000, mode=stat.S_IFREG|0o440)
    binding = {'source': source, 'tree': ident['tree'], 'prepare_sha256': a.sha('/etc/proof/prepare.json')}
    file('/etc/proof/binding.json', json.dumps(binding).encode())
    spec = dict(binding_path='/etc/proof/binding.json', prepare_path='/etc/proof/prepare.json',
        binding_sha256=a.sha('/etc/proof/binding.json'), prepare_sha256=a.sha('/etc/proof/prepare.json'),
        identity=ident, pointer=str(base/'current'), release=str(root), gitdir=str(gd), owner=[1000,1000],
        units=['enterprise-agent-test-'+r+'.service' for r in ('api','mcp','worker')],
        ancestors={str(p): {'uid':0,'gid':0,'mode':0o755,'acl':{}} for p in {*root.parents,*gd.parents}})
    return nodes, spec, root, gd, file


def test_historical_nonroot_release_passes_without_executing_git(fs, monkeypatch):
    nodes, spec, root, gd, file = fs
    monkeypatch.setattr(a.subprocess, 'check_output', lambda *x, **y: pytest.fail('no Root Git'))
    assert a.observe_primary(spec)['source'] == spec['identity']['source']


@pytest.mark.parametrize('case', ['gitdir','source','tree','record','record_owner','file','cwd','pointer','head','ancestor'])
def test_observation_tamper_rejected(fs, monkeypatch, case):
    nodes, spec, root, gd, file = fs
    if case == 'gitdir': spec['gitdir'] = str(root)
    if case in ('source','tree'): spec['identity'][case] = '0'*40
    if case == 'record': nodes[spec['binding_path']]['raw'] += b' '
    if case == 'record_owner': nodes[spec['binding_path']]['uid'] = 1000
    if case == 'file': nodes[str(root/'sample.py')]['raw'] = b'tampered'
    if case == 'cwd': monkeypatch.setattr(a, 'running_cwd', lambda unit: '/wrong')
    if case == 'pointer': nodes[spec['pointer']]['target'] = str(gd)
    if case == 'head': nodes[str(gd/'HEAD')]['raw'] = b'0'*40
    if case == 'ancestor': spec['ancestors'].pop('/opt')
    with pytest.raises((ValueError, KeyError)): a.observe_primary(spec)


def test_new_execution_material_still_requires_root(fs):
    _, _, root, _, _ = fs
    with pytest.raises(ValueError, match='ROOT_PATH_TRUST'): a.trust(root)


def test_runtime_unverified_symlink_still_rejected(fs):
    nodes, _, root, _, _ = fs
    nodes['/etc/proof/python'] = {'uid':0,'mode':stat.S_IFLNK|0o777,'target':str(root/'sample.py')}
    with pytest.raises(ValueError, match='CANONICAL_ROOT_PATH'): a.trust('/etc/proof/python')


def test_historical_group_write_is_not_readonly_evidence(fs):
    nodes, spec, _, _, _ = fs
    nodes[spec['prepare_path']]['mode'] = stat.S_IFREG|0o460
    with pytest.raises(ValueError, match='HISTORICAL_RECORD_OWNER'): a.observe_primary(spec)


def test_driver_observer_uses_same_sealed_path_not_root_git():
    import ast
    from pathlib import Path
    source = Path(__file__).resolve().parents[1]/'scripts/test_delivery_pipeline.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    observer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'observe')
    calls = [n.func.id for n in ast.walk(observer) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert 'observe_primary' in calls
    assert 'git' not in calls and 'identity' not in calls
    assert 'trusted' not in calls  # historical ancestors/ACLs use exact observation pins
