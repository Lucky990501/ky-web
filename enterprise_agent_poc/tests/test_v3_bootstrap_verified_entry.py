"""Process-isolated import attacks; privileged metadata is explicitly doubled.

No Root execution, technical/Owner decision, installation or release occurs.
The subprocess tests execute actual Python imports and canaries, not a mocked
import engine. The ordinary-UID fixture reader is NEVER a production CLI flag.
"""
import json
from pathlib import Path
import py_compile
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import v3_bootstrap_verified_entry as entry


@pytest.fixture
def sources(tmp_path):
    root = tmp_path / "candidate"
    original = Path(entry.__file__).parents[1]
    originals = {}
    for relative in (*entry.CODE_FILES, "scripts/v3_bootstrap_verified_entry.py"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        originals[relative] = (original / relative).read_bytes()
        target.write_bytes(originals[relative])
    manifest = {
        "schema_version": 1, "scope": entry.VERSION, "source_commit": "a" * 40,
        "source_tree": "b" * 40, "parent_source_commit": entry.PARENT,
        "tooling_root": str(root), "git_repository": str(tmp_path / "fixture-repository"),
        "code_pins": {name: entry.digest(originals[name]) for name in entry.CODE_FILES},
        "entry_sha256": entry.digest(originals["scripts/v3_bootstrap_verified_entry.py"]),
    }
    return root, manifest, originals


def fixture_reader(monkeypatch):
    """Fictional ownership only; filesystem bytes/path checks remain actual."""
    def trusted(path):
        entry.require(path.is_absolute() and path.resolve(strict=True) == path,
                      "ENTRY_PATH_NOT_CANONICAL")
        entry.require(not path.lstat().st_mode & 0o022, "ENTRY_UNTRUSTED_ROOT_PATH")
    monkeypatch.setattr(entry, "trusted_path", trusted)
    def read(path, limit=2_000_000):
        trusted(path)
        raw = path.read_bytes()
        entry.require(len(raw) <= limit, "ENTRY_UNTRUSTED_FILE")
        return raw
    monkeypatch.setattr(entry, "protected_bytes", read)


def pin_manifest(sources, tmp_path):
    _, manifest, _ = sources
    path = tmp_path / "independently-approved-byte-manifest.json"
    raw = json.dumps(manifest).encode()
    path.write_bytes(raw)
    return path, entry.digest(raw)


def test_nine_pins_preserved_and_source_closure(sources, monkeypatch):
    root, manifest, originals = sources
    fixture_reader(monkeypatch)
    codes = entry.verified_sources(manifest, originals.__getitem__)
    assert set(codes) == set(entry.MODULES)
    assert len(codes) == 7 and len(entry.CODE_FILES) == 9
    assert "app.settings" not in codes
    assert all(code.co_filename.startswith(str(root)) for code in codes.values())


@pytest.mark.parametrize("attack", ["initializer", "nested_initializer", "bytecode", "legacy_bytecode"])
def test_extra_code_rejected_before_any_candidate_execution(sources, monkeypatch, attack):
    root, manifest, originals = sources
    fixture_reader(monkeypatch)
    target = {
        "initializer": "scripts/__init__.py", "nested_initializer": "scripts/extra/__init__.py",
        "bytecode": "scripts/__pycache__/runtime_policy_rollout.cpython-311.pyc",
        "legacy_bytecode": "app/settings.pyc",
    }[attack]
    path = root / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"UNPINNED_EXECUTION_CANARY")
    with pytest.raises(entry.EntryBlocked, match="UNPINNED_EXECUTABLE"):
        entry.verified_sources(manifest, originals.__getitem__)


@pytest.mark.parametrize("relative", entry.CODE_FILES)
def test_each_pin_rejects_source_drift_before_compile(sources, monkeypatch, relative):
    root, manifest, originals = sources
    fixture_reader(monkeypatch)
    (root / relative).write_bytes(b"raise RuntimeError('CANDIDATE_SIDE_EFFECT')\n")
    with pytest.raises(entry.EntryBlocked, match="NINE_PIN_OR_GIT_DRIFT"):
        entry.verified_sources(manifest, originals.__getitem__)


def test_entry_bytes_require_separate_approved_pin(sources, monkeypatch):
    _, manifest, originals = sources
    fixture_reader(monkeypatch)
    manifest["entry_sha256"] = "0" * 64
    with pytest.raises(entry.EntryBlocked, match="ENTRY_CODE_IDENTITY_DRIFT"):
        entry.verified_sources(manifest, originals.__getitem__)


@pytest.mark.parametrize("mutation", ["hash", "parent", "schema_bool", "extra_pin", "missing_pin", "extra_field"])
def test_pin_manifest_cannot_expand_contract(sources, monkeypatch, tmp_path, mutation):
    fixture_reader(monkeypatch)
    _, manifest, _ = sources
    if mutation == "parent":
        manifest["parent_source_commit"] = "1" * 40
    elif mutation == "schema_bool":
        manifest["schema_version"] = True
    elif mutation == "extra_pin":
        manifest["code_pins"]["scripts/__init__.py"] = "1" * 64
    elif mutation == "missing_pin":
        manifest["code_pins"].pop(entry.CODE_FILES[0])
    elif mutation == "extra_field":
        manifest["approved"] = True
    path, checksum = pin_manifest(sources, tmp_path)
    with pytest.raises(entry.EntryBlocked):
        entry.read_manifest(path, "0" * 64 if mutation == "hash" else checksum)


def test_independent_manifest_exact_bytes(sources, monkeypatch, tmp_path):
    fixture_reader(monkeypatch)
    path, checksum = pin_manifest(sources, tmp_path)
    assert entry.read_manifest(path, checksum) == sources[1]
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(entry.EntryBlocked, match="DUPLICATE"):
        entry.read_manifest(path, entry.digest(path.read_bytes()))


@pytest.mark.parametrize("location", ["candidate", "ancestor"])
@pytest.mark.parametrize("bad", ["owner", "group_write", "other_write", "symlink"])
def test_candidate_and_ancestor_permissions_rejected(monkeypatch, location, bad):
    path = Path("/approved/candidate/source.py")
    target = path if location == "candidate" else path.parent
    def info(item):
        mode = stat.S_IFREG | 0o644 if item == path else stat.S_IFDIR | 0o755
        owner = 0
        if item == target:
            owner = 1000 if bad == "owner" else 0
            mode |= 0o020 if bad == "group_write" else 0o002 if bad == "other_write" else 0
            if bad == "symlink":
                mode = stat.S_IFLNK | 0o777
        return SimpleNamespace(st_uid=owner, st_mode=mode, st_nlink=1)
    monkeypatch.setattr(Path, "resolve", lambda item, **kwargs: item)
    monkeypatch.setattr(Path, "lstat", info)
    with pytest.raises(entry.EntryBlocked, match="UNTRUSTED_ROOT_PATH"):
        entry.trusted_path(path)


@pytest.mark.parametrize("attack", ["package_initializer", "unchecked_bytecode"])
def test_original_04_direct_script_probe_has_no_candidate_side_effects(sources, tmp_path, attack):
    root, _, _ = sources
    canary = tmp_path / "must-not-exist"
    code = "from pathlib import Path\nPath(" + repr(str(canary)) + ").write_text('EXECUTED')\nraise RuntimeError('UNPINNED_EXECUTED')\n"
    if attack == "package_initializer":
        (root / "scripts/__init__.py").write_text(code)
    else:
        probe = tmp_path / "bytecode-probe.py"
        probe.write_text(code)
        target = root / "scripts/runtime_policy_rollout.py"
        py_compile.compile(str(probe), cfile=__import__("importlib.util").util.cache_from_source(str(target)),
            dfile=str(target), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH, doraise=True)
    result = subprocess.run([sys.executable, "-I", "-B", "-S", str(root / entry.CODE_FILES[0]), "--help"],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "VERIFIED_SOURCE_ONLY_ENTRY_REQUIRED" in result.stderr
    assert "UNPINNED_EXECUTED" not in result.stderr and not canary.exists()


PROCESS_HARNESS = r'''
import importlib.util, json, pathlib, sys
entry_path, root, raw_manifest, mode, canary = sys.argv[1:]
spec = importlib.util.spec_from_file_location("trusted_test_entry", entry_path)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
manifest = json.loads(raw_manifest)
root = pathlib.Path(root)
# Ordinary-UID metadata double, never reachable from the formal CLI.
def trust(path):
    entry.require(path.is_absolute() and path.resolve(strict=True) == path, "ENTRY_PATH_NOT_CANONICAL")
    entry.require(not path.lstat().st_mode & 0o022, "ENTRY_UNTRUSTED_ROOT_PATH")
entry.trusted_path = trust
entry.protected_bytes = lambda path, limit=2000000: path.read_bytes()
originals = {name: (root / name).read_bytes() for name in (*entry.CODE_FILES, "scripts/v3_bootstrap_verified_entry.py")}
try:
    codes = entry.verified_sources(manifest, originals.__getitem__)
    if mode == "changed_after_capture":
        (root / "scripts/runtime_policy_rollout.py").write_text("raise RuntimeError('CHANGED_SOURCE_EXECUTED')")
    roots = tuple(pathlib.Path(p) for p in sys.path if p and pathlib.Path(p).is_dir())
    loader = entry.SourceOnly(root, codes, roots)
    sys.meta_path[:] = [loader]
    tool = __import__("scripts.v3_trusted_record_bootstrap", fromlist=["main"])
    if mode == "unpinned_import":
        __import__("scripts.unapproved")
    elif mode == "app_import":
        __import__("app.settings")
    elif mode == "fallback_import":
        __import__("rollback_preflight")
    elif mode == "changed_import_path":
        loader.find_spec("fractions", [str(root)])
    else:
        # Pinned dependencies insert candidate ROOT into sys.path. The finder
        # ignores that insertion and cannot execute candidate fractions.py.
        sys.path.insert(0, str(root))
        __import__("fractions")
        print("VERIFIED_SOURCE_ONLY_CLOSURE_PASS:" + str(len(codes)))
except entry.EntryBlocked as exc:
    print(str(exc))
    sys.exit(2)
'''


@pytest.mark.parametrize("mode, expected", [
    ("clean", "VERIFIED_SOURCE_ONLY_CLOSURE_PASS:7"),
    ("changed_after_capture", "VERIFIED_SOURCE_ONLY_CLOSURE_PASS:7"),
    ("unpinned_import", "ENTRY_UNPINNED_REPOSITORY_IMPORT"),
    ("app_import", "ENTRY_UNPINNED_REPOSITORY_IMPORT"),
    ("fallback_import", "ENTRY_UNPINNED_REPOSITORY_IMPORT"),
    ("changed_import_path", "ENTRY_IMPORT_PATH_UNTRUSTED"),
])
def test_actual_source_only_import_engine_in_fresh_process(sources, tmp_path, mode, expected):
    root, manifest, _ = sources
    canary = tmp_path / "candidate-side-effect"
    malicious = "from pathlib import Path\nPath(" + repr(str(canary)) + ").write_text('EXECUTED')\nraise RuntimeError('UNPINNED_EXECUTED')\n"
    (root / "fractions.py").write_text(malicious)
    (root / "scripts/unapproved.py").write_text(malicious)
    (root / "app/__init__.py").write_text(malicious)
    result = subprocess.run([sys.executable, "-I", "-B", "-S", "-c", PROCESS_HARNESS,
                            str(root / "scripts/v3_bootstrap_verified_entry.py"), str(root),
                            json.dumps(manifest), mode, str(canary)], capture_output=True, text=True)
    assert expected in result.stdout, result.stderr
    assert result.returncode == (0 if mode in {"clean", "changed_after_capture"} else 2)
    assert not canary.exists() and "UNPINNED_EXECUTED" not in result.stderr


def test_direct_entry_without_isolated_flags_refuses_before_path_imports(sources, tmp_path):
    root, _, _ = sources
    canary = tmp_path / "argparse-import-side-effect"
    (root / "scripts/argparse.py").write_text("from pathlib import Path\nPath(" + repr(str(canary)) + ").touch()")
    result = subprocess.run([sys.executable, "-B", "-S", str(root / "scripts/v3_bootstrap_verified_entry.py"), "--help"],
                            capture_output=True, text=True)
    assert result.returncode != 0 and "ENTRY_SYSTEM_PYTHON_I_B_S_REQUIRED" in result.stderr
    assert not canary.exists()


@pytest.mark.parametrize("flag", ["--producer-source", "--producer-tree", "--git-repository"])
def test_verified_entry_does_not_allow_identity_swap(sources, monkeypatch, flag):
    _, manifest, _ = sources
    monkeypatch.setattr(entry.sys, "modules", {name: module for name, module in sys.modules.items()
                        if name not in {"scripts", "app"} and not name.startswith(("scripts.", "app."))})
    args = ["seal-policy", "--producer-source", manifest["source_commit"],
            "--producer-tree", manifest["source_tree"], "--git-repository", manifest["git_repository"]]
    args[args.index(flag) + 1] = "UNAPPROVED"
    with pytest.raises(entry.EntryBlocked, match="ARGUMENT_DRIFT"):
        entry.execute_verified(manifest, {}, (), args)


def test_no_cli_uid_or_import_path_override():
    result = subprocess.run([sys.executable, "-I", "-B", "-S", entry.__file__, "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert "--allow" not in result.stdout and "--isolated" not in result.stdout
    assert "--independently-approved-pin-sha256" in result.stdout
