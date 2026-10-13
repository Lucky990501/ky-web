"""Source-only V3 entry. Independently verify this file BEFORE executing it.

An authorized operator must use trusted system sha256sum and an independent
approved digest, then system Python -I -B -S. This is a byte verifier, never an
Owner issuer. The manifest digest is obtained independently, not from itself.
No repository code, package initializer or bytecode is imported before all
nine source/data pins, Root paths and exact Git identity have been verified.
"""
from __future__ import annotations

if __name__ == "__main__":
    _interpreter = __import__("sys")  # Built-in, available before path imports.
    if not (_interpreter.flags.isolated and _interpreter.flags.no_site
            and _interpreter.flags.ignore_environment and _interpreter.dont_write_bytecode):
        raise SystemExit("BLOCKED: ENTRY_SYSTEM_PYTHON_I_B_S_REQUIRED")

import argparse
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

VERSION = "V3_VERIFIED_SOURCE_ONLY_ENTRY_V2"
PARENT = "7ca9be5ecf912f95d363b93ca11a9b0b82ea4fb2"
CODE_FILES = (
    "scripts/v3_trusted_record_bootstrap.py", "scripts/runtime_policy_rollout.py",
    "scripts/schema015_successor_binding.py", "scripts/rollback_preflight.py",
    "scripts/verify_runtime_config.py", "scripts/release_manifest.py", "scripts/release_runtime_recovery.py",
    "app/settings.py", "deploy/rollback_compatibility.json",
)
MODULES = {"scripts." + Path(name).stem: name for name in CODE_FILES if name.startswith("scripts/")}
HASH = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")


class EntryBlocked(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise EntryBlocked(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pairs(items):
    result = {}
    for name, value in items:
        require(name not in result, "ENTRY_DUPLICATE_JSON_KEY")
        result[name] = value
    return result


def trusted_path(path):
    require(path.is_absolute() and path.resolve(strict=True) == path, "ENTRY_PATH_NOT_CANONICAL")
    for current in (path, *path.parents):
        info = current.lstat()
        require(info.st_uid == 0 and not info.st_mode & 0o022
                and (stat.S_ISDIR(info.st_mode) or current == path
                     and stat.S_ISREG(info.st_mode) and info.st_nlink == 1), "ENTRY_UNTRUSTED_ROOT_PATH")


def protected_bytes(path, limit=2_000_000):
    trusted_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and info.st_size <= limit, "ENTRY_UNTRUSTED_FILE")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(descriptor)
        require(len(raw) <= limit and (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "ENTRY_FILE_CHANGED")
        current = path.lstat()
        require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), "ENTRY_FILE_REPLACED")
        return raw
    finally:
        os.close(descriptor)


def system_import_roots():
    require(sys.flags.isolated and sys.flags.no_site and sys.flags.ignore_environment
            and sys.dont_write_bytecode and os.geteuid() == 0, "ENTRY_ISOLATED_SYSTEM_ROOT_REQUIRED")
    executable = Path(sys.executable).resolve(strict=True)
    trusted_path(executable)
    prefix = Path(sys.base_prefix).resolve(strict=True)
    trusted_path(prefix)
    roots = []
    for item in sys.path:
        require(bool(item), "ENTRY_IMPORT_PATH_UNTRUSTED")
        path = Path(item)
        require(path.is_absolute() and path.is_relative_to(prefix), "ENTRY_IMPORT_PATH_UNTRUSTED")
        if not path.exists():
            # CPython includes a usually absent stdlib zip. It is never a
            # fallback import root in the restricted finder below.
            trusted_path(path.parent)
            continue
        trusted_path(path)
        require(path.is_dir(), "ENTRY_IMPORT_PATH_UNTRUSTED")
        roots.append(path)
    require(bool(roots), "ENTRY_SYSTEM_STDLIB_UNAVAILABLE")
    for name, module in tuple(sys.modules.items()):
        if name == "__main__":
            continue
        location = getattr(module, "__file__", None)
        if location:
            resolved = Path(location).resolve(strict=True)
            require(any(resolved.is_relative_to(root) for root in roots), "ENTRY_PRELOADED_MODULE_UNTRUSTED")
            trusted_path(resolved)
    return tuple(roots)


def read_manifest(path, expected_sha):
    require(isinstance(expected_sha, str) and HASH.fullmatch(expected_sha), "ENTRY_INDEPENDENT_PIN_REQUIRED")
    raw = protected_bytes(path, 262144)
    require(digest(raw) == expected_sha, "ENTRY_MANIFEST_HASH_DRIFT")
    value = json.loads(raw, object_pairs_hook=pairs)
    require(isinstance(value, dict) and set(value) == {
        "schema_version", "scope", "source_commit", "source_tree", "parent_source_commit",
        "tooling_root", "git_repository", "code_pins", "entry_sha256", "application_exact",
        "binding_sha256"}, "ENTRY_MANIFEST_FIELDS")
    require(type(value["schema_version"]) is int and value["schema_version"] == 2
            and value["scope"] == VERSION and value["parent_source_commit"] == PARENT
            and all(isinstance(value[name], str) and COMMIT.fullmatch(value[name])
                    for name in ("source_commit", "source_tree")), "ENTRY_SOURCE_IDENTITY")
    application = value["application_exact"]
    require(isinstance(application, dict) and set(application) == {
        "release_id", "source_commit", "source_tree", "parent_source_commit",
        "archive_sha256", "raw_manifest_sha256", "manifest_sha256"}, "ENTRY_APPLICATION_FIELDS")
    require(application["source_commit"] == value["source_commit"]
            and application["source_tree"] == value["source_tree"]
            and application["parent_source_commit"] == PARENT
            and value["source_commit"] != PARENT, "ENTRY_APPLICATION_SOURCE_BINDING")
    require(isinstance(application["release_id"], str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", application["release_id"])
            and application["release_id"] != "20261012-7dcde57-p0-controlled-v3",
            "ENTRY_NEW_APPLICATION_RELEASE_REQUIRED")
    require(all(isinstance(application[key], str) and HASH.fullmatch(application[key])
                for key in ("archive_sha256", "raw_manifest_sha256", "manifest_sha256"))
            and isinstance(value["binding_sha256"], str) and HASH.fullmatch(value["binding_sha256"]),
            "ENTRY_APPLICATION_ARTIFACT_PINS")
    require(isinstance(value["code_pins"], dict) and set(value["code_pins"]) == set(CODE_FILES)
            and all(isinstance(pin, str) and HASH.fullmatch(pin) for pin in value["code_pins"].values())
            and isinstance(value["entry_sha256"], str) and HASH.fullmatch(value["entry_sha256"]),
            "ENTRY_EXACT_NINE_PINS_REQUIRED")
    require(all(isinstance(value[name], str) for name in ("tooling_root", "git_repository")),
            "ENTRY_APPROVED_PATH_REQUIRED")
    return value


def git_blob_reader(manifest):
    repository = Path(manifest["git_repository"])
    trusted_path(repository)
    trusted_path(Path("/usr/bin/git"))

    def git(*args):
        return subprocess.check_output([
            "/usr/bin/git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
            "-C", str(repository), *args], env={"PATH": "/usr/bin:/bin", "LC_ALL": "C",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
            stderr=subprocess.DEVNULL, timeout=20)

    source = manifest["source_commit"]
    require(git("rev-parse", source + "^{commit}").decode().strip() == source
            and git("rev-parse", source + "^{tree}").decode().strip() == manifest["source_tree"]
            and git("show", "-s", "--format=%P", source).decode().strip() == PARENT,
            "ENTRY_GIT_SOURCE_TREE_PARENT_DRIFT")

    def blob(relative):
        path = "enterprise_agent_poc/" + relative
        item = git("ls-tree", source, "--", path).decode().split()
        require(len(item) == 4 and item[0] in {"100644", "100755"}
                and item[1] == "blob" and item[3] == path, "ENTRY_GIT_BLOB_IDENTITY")
        return git("cat-file", "blob", item[2])

    return blob


def verified_sources(manifest, blob):
    root = Path(manifest["tooling_root"])
    trusted_path(root)
    own = Path(__file__).absolute()
    require(digest(protected_bytes(own)) == manifest["entry_sha256"]
            and blob("scripts/v3_bootstrap_verified_entry.py") == protected_bytes(own), "ENTRY_CODE_IDENTITY_DRIFT")
    # Reject the original two exploit forms, even though the source-only
    # loader below would never execute them. Do not import app/__init__.py.
    for directory in (root / "scripts", root / "app"):
        trusted_path(directory)
        for path in directory.rglob("*"):
            require(not path.is_symlink(), "ENTRY_CANDIDATE_SYMLINK")
            require(path.suffix not in {".pyc", ".pyo"}
                    and not (directory.name == "scripts" and path.name == "__init__.py"),
                    "ENTRY_UNPINNED_EXECUTABLE_OBJECT")
    captured = {}
    for relative in CODE_FILES:
        raw = protected_bytes(root / relative)
        require(digest(raw) == manifest["code_pins"][relative] and raw == blob(relative),
                "ENTRY_NINE_PIN_OR_GIT_DRIFT")
        captured[relative] = raw
    # Compile every executable source before executing any repository byte.
    return {name: compile(captured[relative], str(root / relative), "exec", dont_inherit=True)
            for name, relative in MODULES.items()}


class SourceOnly(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Only captured repository sources; independent system stdlib remains usable."""
    def __init__(self, root, codes, system_roots):
        self.root, self.codes, self.system_roots = root, codes, system_roots

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "scripts" or fullname in self.codes:
            return importlib.util.spec_from_loader(fullname, self, is_package=fullname == "scripts")
        if fullname.split(".")[0] in {"scripts", "app"} or fullname in {
                Path(relative).stem for relative in CODE_FILES}:
            raise EntryBlocked("ENTRY_UNPINNED_REPOSITORY_IMPORT")
        for finder in (importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter):
            spec = finder.find_spec(fullname, path, target)
            if spec is not None:
                return spec
        search = tuple(str(root) for root in self.system_roots) if path is None else tuple(path)
        require(all(Path(item).is_absolute() and any(Path(item).is_relative_to(root)
                    for root in self.system_roots) for item in search), "ENTRY_IMPORT_PATH_UNTRUSTED")
        spec = importlib.machinery.PathFinder.find_spec(fullname, search, target)
        if spec is None:
            # Preserve stdlib optional-platform imports (e.g. winreg on Linux)
            # while this sole finder provides NO candidate-directory fallback.
            raise ModuleNotFoundError("ENTRY_SYSTEM_MODULE_UNAVAILABLE", name=fullname)
        require(spec.origin not in (None, "namespace"), "ENTRY_UNPINNED_IMPORT")
        origin = Path(spec.origin).absolute()
        require(any(origin.is_relative_to(root) for root in self.system_roots), "ENTRY_IMPORT_PATH_UNTRUSTED")
        trusted_path(origin)
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        if module.__name__ == "scripts":
            module.__path__ = []  # Synthetic namespace; no filesystem initializer.
            return
        module.__file__ = str(self.root / MODULES[module.__name__])
        exec(self.codes[module.__name__], module.__dict__)


def execute_verified(manifest, codes, roots, arguments):
    require(not any(name == "scripts" or name.startswith(("scripts.", "app."))
                    or name == "app" for name in sys.modules), "ENTRY_REPOSITORY_ALREADY_IMPORTED")
    require(arguments and arguments[0] in {
        "inspect-artifact", "seal-policy", "seal-release-records", "revoke-policy"},
        "ENTRY_ACTION_REQUIRED")
    if arguments[0] != "inspect-artifact":
        for flag, expected in (("--producer-source", manifest["source_commit"]),
                               ("--producer-tree", manifest["source_tree"])):
            require(arguments.count(flag) == 1 and arguments.index(flag) + 1 < len(arguments)
                    and arguments[arguments.index(flag) + 1] == expected, "ENTRY_PRODUCER_ARGUMENT_DRIFT")
    require(arguments.count("--git-repository") == 1
            and arguments.index("--git-repository") + 1 < len(arguments)
            and arguments[arguments.index("--git-repository") + 1] == manifest["git_repository"],
            "ENTRY_GIT_ARGUMENT_DRIFT")
    sys.meta_path[:] = [SourceOnly(Path(manifest["tooling_root"]), codes, roots)]
    # Shared pinned modules insert ROOT into sys.path. This finder never uses
    # that list; all repository execution remains the captured allowlist.
    module = __import__("scripts.v3_trusted_record_bootstrap", fromlist=["main"])
    require(module.CODE_FILES == CODE_FILES, "ENTRY_CODE_CONTRACT_DRIFT")
    # Context is independently pinned data, never candidate self-authorization.
    # This runs only after the entire repository execution closure is captured.
    module.configure_candidate(manifest["application_exact"], manifest["binding_sha256"])
    return module.main(arguments)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise EntryBlocked("ENTRY_ARGUMENTS_INVALID")


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--pin-manifest", type=Path, required=True)
    parser.add_argument("--independently-approved-pin-sha256", required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    candidate_entered = False
    try:
        args = parser.parse_args(argv)
        roots = system_import_roots()
        manifest = read_manifest(args.pin_manifest, args.independently_approved_pin_sha256)
        codes = verified_sources(manifest, git_blob_reader(manifest))
        arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
        candidate_entered = True
        return execute_verified(manifest, codes, roots, arguments)
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "check": str(exc) if isinstance(exc, EntryBlocked)
                          else "ENTRY_VERIFICATION_FAILED", "candidate_code_execution":
                          "POSSIBLE_AFTER_ALL_BYTE_GUARDS" if candidate_entered else "NOT_STARTED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
