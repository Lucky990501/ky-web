"""Only exact global-lock / Root evidence chain tests; NOT Native acceptance.

Stat ownership fixtures are explicit synthetic inputs. flock, O_NOFOLLOW,
inode replacement and fd closure execute against private ordinary-user files.
No system directory, live lock or remote machine is modified by these tests.
"""
import ast
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import types

import pytest
from test_successor import module, authority_fixture

HERE = Path(__file__).parent
SHARED_LOCK = Path('/run/lock/enterprise-agent-test-successor-v1.lock')


@pytest.fixture
def c():
    return module('v3_exact_path_entry', HERE / 'entry.py')


def metadata(mode, uid=0, gid=0, nlink=1, inode=7):
    return types.SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid,
                                 st_nlink=nlink, st_dev=29, st_ino=inode)


@pytest.fixture
def lock_metadata(monkeypatch):
    facts = {SHARED_LOCK: metadata(stat.S_IFREG | 0o644),
             SHARED_LOCK.parent: metadata(stat.S_IFDIR | 0o1777),
             Path('/run'): metadata(stat.S_IFDIR | 0o755),
             Path('/'): metadata(stat.S_IFDIR | 0o755)}
    monkeypatch.setattr(Path, 'lstat', lambda p: facts[p])
    return facts


def test_exact_root_sticky_global_lock_accepted(c, lock_metadata):
    assert c.LOCK == SHARED_LOCK
    c.native(SHARED_LOCK, readonly=False)


@pytest.mark.parametrize('mode,uid,gid', [
    (0o777, 0, 0), (0o1775, 0, 0), (0o2777, 0, 0),
    (0o1777, 1000, 1000), (0o1777, 0, 1000),
])
def test_nonexact_sticky_parent_refused(c, lock_metadata, mode, uid, gid):
    lock_metadata[SHARED_LOCK.parent] = metadata(stat.S_IFDIR | mode, uid, gid)
    with pytest.raises(c.Blocked): c.native(SHARED_LOCK, readonly=False)


@pytest.mark.parametrize('info', [
    metadata(stat.S_IFREG | 0o644, uid=1000),
    metadata(stat.S_IFREG | 0o644, gid=1000),
    metadata(stat.S_IFREG | 0o664),
    metadata(stat.S_IFREG | 0o644, nlink=2),
    metadata(stat.S_IFLNK | 0o777),
    metadata(stat.S_IFIFO | 0o644),
])
def test_fake_mutable_or_linked_lock_refused(c, lock_metadata, info):
    lock_metadata[SHARED_LOCK] = info
    with pytest.raises(c.Blocked): c.native(SHARED_LOCK, readonly=False)


def test_sticky_exception_is_not_generic(c, lock_metadata):
    other = SHARED_LOCK.parent / 'candidate-code-or-evidence.py'
    lock_metadata[other] = metadata(stat.S_IFREG | 0o444)
    with pytest.raises(c.Blocked, match='ROOT_ANCESTOR_TRUST'): c.native(other)
    lock_metadata[Path('/run')] = metadata(stat.S_IFDIR | 0o1777)
    with pytest.raises(c.Blocked, match='ROOT_ANCESTOR_TRUST'):
        c.native(SHARED_LOCK, readonly=False)


def test_symlinked_lock_parent_refused(c, lock_metadata):
    lock_metadata[SHARED_LOCK.parent] = metadata(stat.S_IFLNK | 0o777)
    with pytest.raises(c.Blocked, match='ROOT_ANCESTOR_TRUST'):
        c.native(SHARED_LOCK, readonly=False)


def chain(path, *, file_mode=0o440):
    facts = {p: metadata(stat.S_IFDIR | 0o755) for p in path.parents}
    facts[path] = metadata(stat.S_IFREG | file_mode, gid=1000)
    return facts


def test_new_root_operation_chain_accepts_but_old_user_parent_refused(c, monkeypatch):
    assert c.OP == c.CONTROL / 'operation'
    target = c.OP / 'prepare.json'
    facts = chain(target)
    facts[c.OP] = metadata(stat.S_IFDIR | 0o750, gid=1000)
    old = c.ROOT / 'release-evidence/business-894-successor-v2/prepare.json'
    facts.update(chain(old))
    facts[c.ROOT / 'release-evidence'] = metadata(stat.S_IFDIR | 0o750, uid=1000, gid=1000)
    monkeypatch.setattr(Path, 'lstat', lambda p: facts[p])
    c.native(target)
    with pytest.raises(c.Blocked, match='ROOT_ANCESTOR_TRUST'): c.native(old)


@pytest.mark.parametrize('bad', ['user-owner', 'writable', 'symlink'])
def test_new_operation_is_not_implicitly_trusted(c, monkeypatch, bad):
    target = c.OP / 'registry-witness.v2.json'; facts = chain(target)
    facts[c.OP] = {'user-owner': metadata(stat.S_IFDIR | 0o750, uid=1000, gid=1000),
                  'writable': metadata(stat.S_IFDIR | 0o775),
                  'symlink': metadata(stat.S_IFLNK | 0o777)}[bad]
    monkeypatch.setattr(Path, 'lstat', lambda p: facts[p])
    with pytest.raises(c.Blocked, match='ROOT_ANCESTOR_TRUST'): c.native(target)


@pytest.fixture
def private_lock(c, monkeypatch, tmp_path):
    path = tmp_path / 'private-fixture.lock'; path.write_bytes(b'fixture')
    monkeypatch.setattr(c, 'LOCK', path)
    monkeypatch.setattr(c, 'native', lambda *a, **kw: None)  # no real Root proof
    return path


def test_actual_flock_same_inode_contention_and_cloexec(c, private_lock, monkeypatch):
    real_open = os.open; descriptors = []
    def opening(*a, **kw):
        fd = real_open(*a, **kw); descriptors.append(fd); return fd
    monkeypatch.setattr(c.os, 'open', opening)
    inode = private_lock.stat().st_ino
    with c.locked():
        assert private_lock.stat().st_ino == inode
        assert fcntl.fcntl(descriptors[0], fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        with pytest.raises(BlockingIOError):
            with c.locked(): pytest.fail('second simultaneous release entered')
    for fd in descriptors:
        with pytest.raises(OSError): os.fstat(fd)
    with c.locked(): pass


@pytest.mark.parametrize('when', ['before-open', 'after-open', 'after-flock', 'mode', 'hardlink'])
def test_opened_or_named_lock_replacement_refused_and_fd_closed(c, private_lock, monkeypatch, tmp_path, when):
    other = tmp_path / 'different.lock'; other.write_bytes(b'other')
    real_open, real_flock = os.open, fcntl.flock
    descriptors = []
    def replace():
        private_lock.rename(tmp_path / 'original-preserved.lock')
        private_lock.write_bytes(b'new-inode')
    def opening(*a, **kw):
        fd = real_open(other if when == 'before-open' else a[0], *a[1:], **kw)
        descriptors.append(fd)
        if when == 'after-open': replace()
        if when == 'mode': private_lock.chmod(0o666)
        if when == 'hardlink': os.link(private_lock, tmp_path / 'extra-link')
        return fd
    def flocking(fd, flags):
        real_flock(fd, flags)
        if when == 'after-flock': replace()
    monkeypatch.setattr(c.os, 'open', opening)
    monkeypatch.setattr(c.fcntl, 'flock', flocking)
    with pytest.raises(c.Blocked, match='GLOBAL_LOCK_OPENED_INODE_CHANGED'):
        with c.locked(): pytest.fail('replaced lock accepted')
    for fd in descriptors:
        with pytest.raises(OSError): os.fstat(fd)


def test_actual_no_follow_rejects_symlink(c, private_lock, tmp_path):
    c.LOCK = tmp_path / 'symlink.lock'; c.LOCK.symlink_to(private_lock)
    with pytest.raises(OSError):
        with c.locked(): pytest.fail('symlink accepted by open')


def test_missing_global_lock_is_not_created(c, private_lock, tmp_path):
    c.LOCK = tmp_path / 'absent.lock'
    with pytest.raises(FileNotFoundError):
        with c.locked(): pytest.fail('missing lock created')
    assert not c.LOCK.exists()


@pytest.mark.parametrize('field,wrong', [
    ('operation_path', '/opt/enterprise-agent-workbench-test/release-evidence/business-894-successor-v2'),
    ('operation_path', None), ('lock_path', '/run/lock/a-different-release.lock'),
])
def test_old_or_wrong_machine_scope_path_binding_refused(c, authority_fixture, field, wrong):
    value, write, _ = authority_fixture; value[field] = wrong; write()
    with pytest.raises(c.Blocked, match='EXACT_PROTECTED_OPERATION_AND_SHARED_LOCK_BINDING'):
        c.authority('install', clock=lambda: 2)


def test_all_existing105_test_functions_preserved():
    # Windows worktree gitdir references require an exported blob fixture under
    # WSL; on a normal Test checkout read the same original Git object directly.
    originals = {
        'test_successor.py': '9e62ff9660e0bee6524c1d5d9331ab237c15a9c7ff14e79aefd3656f6ddbdb6a',
        'test_prestate_trust_v2.py': 'bce9fe169ea5b4162c97d128fb4ccae6abe82bf8b24634ef59ae2dfc09563759',
    }
    for name, expected in originals.items():
        fixture = os.environ.get('SOURCE_0FCC_TEST_BYTES_DIR')
        if fixture: old = (Path(fixture) / name).read_bytes()
        else:
            result = subprocess.run(['git', '-C', str(HERE.parents[2]), 'show',
                '0fcc3c5ff5c039dd30d7c80ef57c28992c376f42:enterprise_agent_poc/deploy/test_business_successor_v1/' + name],
                capture_output=True, check=True)
            old = result.stdout
        assert hashlib.sha256(old).hexdigest() == expected
        def tests(source):
            return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name.startswith('test_')}
        assert tests(old) == tests((HERE / name).read_bytes())
