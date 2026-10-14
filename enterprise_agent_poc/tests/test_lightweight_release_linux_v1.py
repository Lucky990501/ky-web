"""Linux real filesystem/flock/SIGKILL/Git fixtures, not Production Native."""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lightweight_release_v1 as m
import lightweight_release_linux_v1 as linux
from test_lightweight_release_v1 import Adapter, execution, plan_grant, package, evidence, NOW


class KernelAdapter(Adapter):
    @contextlib.contextmanager
    def lock(self):
        fd = os.open(self.root / "lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)


def test_native_flock_excludes_other_process(tmp_path):
    fd = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    code = "import fcntl,sys; f=open(sys.argv[1],'r+'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
    blocked = subprocess.run([sys.executable, "-c", code, str(tmp_path / "lock")], capture_output=True)
    assert blocked.returncode != 0
    os.close(fd)
    allowed = subprocess.run([sys.executable, "-c", code, str(tmp_path / "lock")], capture_output=True)
    assert allowed.returncode == 0


def test_real_sigkill_preserves_intent_releases_lock_and_exact_recovery(tmp_path):
    root = tmp_path / "job"
    root.mkdir()
    (root / "journal").mkdir()
    plan, grant = plan_grant()
    (root / "plan").write_bytes(m.canonical(plan))
    (root / "grant").write_bytes(m.canonical(grant))
    code = '''import json,os,signal,sys
from pathlib import Path
from test_lightweight_release_linux_v1 import KernelAdapter
import lightweight_release_v1 as m
root=Path(sys.argv[1]); plan=json.loads((root/'plan').read_bytes()); grant=json.loads((root/'grant').read_bytes())
class KillAdapter(KernelAdapter):
    def activate(self,release):
        super().activate(release)
        os.kill(os.getpid(),signal.SIGKILL)
m.Controller(KillAdapter(root)).execute(plan,grant,m.Journal(root/'journal'))
'''
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(Path(__file__).parent), str(Path(m.__file__).parent)))}
    process = subprocess.run([sys.executable, "-c", code, str(root)], env=env, capture_output=True, timeout=10)
    assert process.returncode == -signal.SIGKILL
    journal = m.Journal(root / "journal")
    a = KernelAdapter(root)
    a.active = (root / "actual-pointer.txt").read_text()
    controller = m.Controller(a)
    assert controller.status(journal)["status"] == "UNKNOWN_INCOMPLETE_QUERY_ONLY"
    controller.recover(plan, grant, journal)
    assert controller.status(journal)["status"] == "RESTORED" and a.active == "old-v1"
    assert "activate:new-v1" not in a.calls


def test_three_kernelside_fixture_operations_do_not_enable_production(tmp_path):
    for n in range(3):
        root = tmp_path / f"fixture-{n}"
        root.mkdir()
        (root / "journal").mkdir()
        a = KernelAdapter(root)
        p, g = plan_grant()
        j = m.Journal(root / "journal")
        m.Controller(a).execute(p, g, j)
        assert j.read()[-1]["phase"] == "COMMITTED"
    assert all(p.name.startswith("fixture-") for p in tmp_path.iterdir())


def test_untrusted_real_user_directory_is_not_root_authority(tmp_path):
    with pytest.raises(m.Blocked, match="root_trusted"):
        m.trusted(tmp_path, directory=True)


def test_real_nofollow_rejects_symlink_and_hardlink(tmp_path):
    target = tmp_path / "data"
    target.write_bytes(b"x")
    (tmp_path / "symbolic").symlink_to(target)
    with pytest.raises(OSError):
        m.read_nofollow(tmp_path / "symbolic")
    os.link(target, tmp_path / "hardlink")
    with pytest.raises(m.Blocked):
        m.read_nofollow(target)


def test_final_owner_event_requires_real_get_and_unique_comment(monkeypatch):
    p, g = plan_grant()
    comment = {"body": m.owner_body(p), "id": 15}
    class Reply:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _): return m.canonical([comment])
    calls = []
    def get(request, **_):
        calls.append(request)
        return Reply()
    monkeypatch.setattr(linux.urllib.request, "urlopen", get)
    assert linux.owner_event(p, g)["id"] == 15
    assert len(calls) == 1 and calls[0].get_method() == "GET"
    Reply.read = lambda self, _: m.canonical([comment, comment])
    with pytest.raises(m.Blocked, match="unique_external"):
        linux.owner_event(p, g)


def test_full_broker_input_commit_and_single_start_without_production(monkeypatch, tmp_path):
    p, g = plan_grant()
    a, raw, identity = package()
    p["candidate"] = identity
    p["source_bundle_sha256"] = m.sha(b"isolated Git stub bundle")
    reports = evidence(p)
    g["pilot_plan_hashes"] = [m.sha(m.canonical(p))]
    g["issued_at"], g["expires_at"] = NOW-20, NOW+86400
    g["tooling_root"] = str(tmp_path / "fixture-tooling")
    monkeypatch.setattr(linux.time, "time", lambda: NOW)
    monkeypatch.setattr(linux, "CONTROL", tmp_path)
    (tmp_path / "operations").mkdir()
    monkeypatch.setattr(linux, "trusted", lambda path, **_: path)  # fixture only, NOT Root proof
    monkeypatch.setattr(linux, "owner_event", lambda p, g: {})
    monkeypatch.setattr(linux, "verify_owner", lambda *args: None)
    monkeypatch.setattr(linux, "qualified_reports", lambda *args: None)  # isolated broker control-flow, not real qualification
    starts = []
    monkeypatch.setattr(linux, "run", lambda argv, **_: starts.append(argv) or b"")
    import base64
    enc = lambda data: base64.b64encode(data).decode()
    monkeypatch.setattr(linux.LinuxAdapter, "git", lambda *args: b"")
    monkeypatch.setattr(linux.LinuxAdapter, "package", lambda *args: ({}, {}, {}))
    payload = {"plan": p, "reports": {k: enc(v) for k, v in reports.items()}, "archive": enc(a), "manifest": enc(raw),
               "source_bundle": enc(b"isolated Git stub bundle")}
    receipt = linux.submit(payload, g)
    root = tmp_path / "operations" / p["operation_id"]
    assert receipt["status"] == "SUBMITTED_QUERY_SAME_OPERATION_ONLY"
    assert len(starts) == 1 and starts[0][0] == "/usr/bin/systemctl"
    assert (root / "INPUTS_COMMITTED.json").exists()
    assert len(list((root / "inputs").iterdir())) == 22  # 18 aggregates + independent restore proof + three artifacts
    with pytest.raises(FileExistsError):
        linux.submit(payload, g)
    assert len(starts) == 1


def test_git_actual_blobs_and_static_cache_injection(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = {"enterprise_agent_poc/app/static/index.html": b'/static/workbench.js?v=__JS_SHA256_V1__ /static/workbench.css?v=__CSS_SHA256_V1_',
             "enterprise_agent_poc/app/static/workbench.js": b"js fixture",
             "enterprise_agent_poc/app/static/workbench.css": b"css fixture"}
    for name, data in paths.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "enterprise_agent_poc"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=isolated-fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-m", "isolated fixture"], check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    tree = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"]).decode().strip()
    packaged = dict(paths)
    packaged[next(iter(paths))] = paths[next(iter(paths))].replace(b"__JS_SHA256_V1__", m.sha(paths[list(paths)[1]])[:16].encode()).replace(
        b"__CSS_SHA256_V1_", m.sha(paths[list(paths)[2]])[:16].encode())
    archive, raw, identity = package(packaged)
    manifest = json.loads(raw)
    manifest["source_commit"] = identity["source"] = commit
    raw = m.canonical(manifest)
    identity.update(tree=tree, raw_manifest_sha256=m.sha(raw), canonical_manifest_sha256=m.sha(raw))
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "archive.tar.gz").write_bytes(archive)
    (inputs / "manifest.json").write_bytes(raw)
    adapter = linux.LinuxAdapter({"tooling_root": str(Path(__file__).resolve().parents[2]), "trusted_git": str(repo)}, tmp_path)
    _, files, _ = adapter.package(identity, inputs)
    assert len(files) == 3
    # Real Git bundle import, not an SSH or repository fixture label.
    bundle = tmp_path / "exact.bundle"
    subprocess.run(["git", "-C", str(repo), "bundle", "create", str(bundle), "HEAD"], check=True, capture_output=True)
    adapter.git("bundle", "verify", str(bundle))
    adapter.git("fetch", "--no-tags", "--no-write-fetch-head", str(bundle), commit)
    identity["tree"] = "f" * 40
    with pytest.raises(m.Blocked, match="source_tree"):
        adapter.package(identity, inputs)


def test_legal_new_data_survives_exact_code_recovery(tmp_path):
    root = tmp_path / "job"
    root.mkdir()
    (root / "journal").mkdir()
    data = root / "business-data.json"
    data.write_bytes(m.canonical({"rows": ["original"]}))
    class DataAdapter(KernelAdapter):
        def health(self, release):
            if release["release_id"] == "new-v1":
                data.write_bytes(m.canonical({"rows": ["original", "legal-new-task"]}))
                raise m.Blocked("isolated health failure")
            super().health(release)
    a = DataAdapter(root)
    p, g = plan_grant()
    j = m.Journal(root / "journal")
    with pytest.raises(m.Blocked):
        m.Controller(a).execute(p, g, j)
    assert json.loads(data.read_bytes())["rows"] == ["original", "legal-new-task"]
    assert j.read()[-1]["phase"] == "RESTORED"


def test_actual_adapter_writes_fixed_dropins_atomic_pointer_and_exact_restore(monkeypatch, tmp_path):
    monkeypatch.setattr(linux, "BASE", tmp_path)
    monkeypatch.setattr(linux, "PYTHON", tmp_path / "venv/bin/python")
    monkeypatch.setattr(linux, "trusted", lambda path, **_: path)  # user-owned fixture, not Root attestation
    monkeypatch.setattr(linux.LinuxAdapter, "installed", lambda *args: {})
    dirs = tmp_path / "fixture-units"
    dirs.mkdir()
    for service in linux.SERVICES:
        (dirs / service).mkdir()
    monkeypatch.setattr(linux, "dropin_path", lambda service: dirs / service / "release.conf")
    calls = []
    monkeypatch.setattr(linux, "run", lambda argv, **_: calls.append(argv) or b"")
    a = linux.LinuxAdapter({"tooling_root": str(tmp_path)}, tmp_path)
    p, _ = plan_grant()
    for release in (p["candidate"], p["predecessor"]):
        a.target(release).mkdir(parents=True)
        a.activate(release)
        assert (tmp_path / "release-current").resolve() == a.target(release)
        for service in linux.SERVICES:
            text = (dirs / service / "release.conf").read_text()
            assert f"WorkingDirectory={a.target(release)}" in text and "EnvironmentFile=" in text
        assert not (tmp_path / "release-current.lightweight-v1.pending").exists()
    assert len(calls) == 4 and all(argv[0] == "/usr/bin/systemctl" for argv in calls)
    assert not any("nginx" in " ".join(argv) for argv in calls)


def test_report_labels_without_independent_protected_copy_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(linux, "BASE", tmp_path)
    p, _ = plan_grant()
    reports = evidence(p)
    with pytest.raises((FileNotFoundError, m.Blocked)):
        linux.qualified_reports(p, reports)
