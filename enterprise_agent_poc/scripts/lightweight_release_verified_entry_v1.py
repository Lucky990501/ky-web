"""Ordinary source-only entry, reusing independently pinned V3 loader primitives.

One-time Root installation MUST independently verify this entry and bind the
manifest SHA into fixed sudo/systemd argv. Never authorize from candidate JSON.
No repository import/cache executes before complete Source/Git/path verification.
"""
from __future__ import annotations

if __name__ == "__main__":
    _sys = __import__("sys")
    if not (_sys.flags.isolated and _sys.flags.no_site and _sys.flags.ignore_environment
            and _sys.dont_write_bytecode):
        raise SystemExit("BLOCKED: ENTRY_SYSTEM_PYTHON_I_B_S_REQUIRED")

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import types

CONTROL = Path("/opt/enterprise-agent-workbench/shared/lightweight-release-v1")
ROOT = CONTROL / "tooling"
HOST = "iv-yekqs6ez9cnu262hhsg0"
SELF = "enterprise_agent_poc/scripts/lightweight_release_verified_entry_v1.py"
PRIMITIVES = "enterprise_agent_poc/scripts/v3_bootstrap_verified_entry.py"
HASH = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def need(value, check):
    if not value:
        raise ValueError(check)


def protected(path, limit=32*1024*1024):
    # Same full-ancestor/NOFOLLOW/single-link/read stability primitive as V3.
    need(path.is_absolute() and path.resolve(strict=True) == path, "entry_canonical_path")
    for p in (path, *path.parents):
        s = p.lstat()
        need(s.st_uid == s.st_gid == 0 and not s.st_mode & 0o022
             and (stat.S_ISDIR(s.st_mode) or p == path and stat.S_ISREG(s.st_mode) and s.st_nlink == 1),
             "entry_root_path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, "entry_regular_file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit+1)
        after = os.fstat(fd)
        need((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) ==
             (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
             and path.lstat().st_ino == before.st_ino, "entry_read_drift")
        return raw
    finally:
        os.close(fd)


def trusted_directory(path):
    need(path.is_absolute() and path.resolve(strict=True) == path, "entry_directory_canonical")
    for p in (path,*path.parents):
        s=p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == s.st_gid == 0 and not s.st_mode & 0o022, "entry_directory_trust")


def capture(manifest):
    need(manifest["scope"] == "ORDINARY_RELEASE_SOURCE_ONLY_ENTRY_V1"
         and manifest["tooling_root"] == str(ROOT)
         and all(COMMIT.fullmatch(manifest[k]) for k in ("source", "tree", "parent")), "entry_scope")
    pins = manifest["tooling_files"]
    need(SELF in pins and PRIMITIVES in pins, "entry_primitive_and_self_pins")
    actual = {str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if not p.is_dir()}
    need(actual == set(pins), "entry_complete_inventory")
    repository = CONTROL / "source.git"
    trusted_directory(ROOT)
    trusted_directory(repository)
    protected(Path("/usr/bin/git"))
    def git(*args):
        return subprocess.check_output(["/usr/bin/git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
                    "-C", str(repository), *args], env={"PATH":"/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM":"1",
                    "GIT_CONFIG_GLOBAL":"/dev/null"}, timeout=20, stderr=subprocess.DEVNULL)
    need(git("rev-parse", manifest["source"]+"^{tree}").decode().strip() == manifest["tree"]
         and git("show", "-s", "--format=%P", manifest["source"]).decode().strip() == manifest["parent"], "entry_git_identity")
    captured = {}
    for name, digest in pins.items():
        need(HASH.fullmatch(digest) and ".." not in Path(name).parts and not Path(name).is_absolute()
             and Path(name).suffix not in {".pyc", ".pyo"}, "entry_pin_name")
        raw = protected(ROOT/name)
        need(hashlib.sha256(raw).hexdigest() == digest
             and git("show", manifest["source"]+":"+name) == raw, "entry_source_byte_pin")
        record=git("ls-tree",manifest["source"],"--",name).decode().split()
        need(len(record)==4 and record[0] in {"100644","100755"} and record[1]=="blob" and record[3]==name,
             "entry_git_file_mode")
        captured[name] = raw
    need(Path(__file__).absolute() == ROOT/SELF, "entry_self_path")
    return captured


def source_closure(captured, manifest, primitives, roots):
    codes, packages, namespaces = {}, set(), {"scripts"}
    def add(name, raw, path, package=False):
        need(name not in codes, "entry_module_collision")
        codes[name] = (compile(raw,str(path),"exec",dont_inherit=True),str(path))
        if package:
            packages.add(name)
        for n in range(1,len(name.split("."))):
            namespaces.add(".".join(name.split(".")[:n]))
    for name, raw in captured.items():
        path = Path(name)
        if path.suffix != ".py" or path.parts[0] != "enterprise_agent_poc":
            continue
        parts = list(path.parts[1:]); package = parts[-1] == "__init__.py"
        if package: parts.pop()
        else: parts[-1] = path.stem
        add(".".join(parts),raw,ROOT/name,package)
        if len(parts) == 2 and parts[0] == "scripts":
            add(parts[-1],raw,ROOT/name)
    runtime_roots = tuple(Path(p) for p in manifest["runtime_roots"])
    runtime_files = manifest["runtime_files"]
    for root in runtime_roots:
        need(root.is_absolute() and root.is_relative_to(Path("/opt/enterprise-agent-workbench/venv")), "entry_runtime_root")
        primitives.trusted_path(root)
    actual_runtime={str(p) for root in runtime_roots for p in root.rglob('*')
                    if p.is_file() and p.suffix in {'.py','.so'}}
    need(actual_runtime==set(runtime_files),"entry_complete_runtime_inventory")
    for name, digest in runtime_files.items():
        path = Path(name)
        need(any(path.is_relative_to(root) for root in runtime_roots) and HASH.fullmatch(digest)
             and path.suffix in {".py", ".so"}, "entry_runtime_pin")
        raw = protected(path)
        need(hashlib.sha256(raw).hexdigest() == digest, "entry_runtime_bytes")
        if path.suffix == ".py":
            relative = next(path.relative_to(root) for root in runtime_roots if path.is_relative_to(root))
            parts = list(relative.parts); package = parts[-1] == "__init__.py"
            if package: parts.pop()
            else: parts[-1] = path.stem
            add(".".join(parts),raw,path,package)
    class Captured(primitives.SourceOnly):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in codes or fullname in namespaces:
                return importlib.util.spec_from_loader(fullname,self,is_package=fullname in packages or fullname in namespaces)
            spec = super().find_spec(fullname,path,target)
            if spec and spec.origin not in (None,"built-in","frozen"):
                origin = str(Path(spec.origin).absolute())
                if any(Path(origin).is_relative_to(root) for root in runtime_roots):
                    need(origin in runtime_files, "entry_unpinned_runtime_import")
            return spec
        def exec_module(self, module):
            if module.__name__ in codes:
                code,path = codes[module.__name__]
                module.__file__ = path
                if module.__name__ in packages:
                    module.__path__ = [str(Path(path).parent)]
                exec(code,module.__dict__)
            else:
                module.__path__ = []
    return Captured(ROOT/"enterprise_agent_poc",{},tuple(roots)+runtime_roots)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--pin-manifest", type=Path, required=True)
    p.add_argument("--independently-approved-pin-sha256", required=True)
    p.add_argument("arguments", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    need(sys.flags.isolated and sys.flags.no_site and sys.flags.ignore_environment and sys.dont_write_bytecode
         and os.getuid() == os.getgid() == 0 and socket.gethostname() == HOST, "entry_real_root_isolated_host")
    need(args.pin_manifest == CONTROL/"first-execution.json"
         and HASH.fullmatch(args.independently_approved_pin_sha256), "entry_independent_manifest")
    raw = protected(args.pin_manifest)
    need(hashlib.sha256(raw).hexdigest() == args.independently_approved_pin_sha256, "entry_manifest_pin")
    manifest = json.loads(raw)
    need(manifest["runtime_roots"] and manifest["runtime_files"], "entry_independent_runtime_closure_required")
    captured = capture(manifest)
    # Only NOW may the already existing, unchanged V3 loader primitives execute.
    primitives = types.ModuleType("verified_loader_primitives")
    primitives.__file__ = str(ROOT/PRIMITIVES)
    exec(compile(captured[PRIMITIVES],primitives.__file__,"exec",dont_inherit=True),primitives.__dict__)
    roots = primitives.system_import_roots()
    finder = source_closure(captured,manifest,primitives,roots)
    sys.meta_path[:] = [finder]
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    need(arguments and arguments[0] in {"rpc","run","status","recover-if-needed"}, "entry_fixed_action")
    sys.argv = [str(ROOT/"enterprise_agent_poc/scripts/lightweight_release_linux_v1.py"),*arguments]
    module = __import__("lightweight_release_linux_v1")
    return module.main()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('{"status":"BLOCKED","check":"FIRST_EXECUTION_VERIFICATION_FAILED"}')
        raise SystemExit(2)
