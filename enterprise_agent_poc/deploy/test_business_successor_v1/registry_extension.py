"""One exact package addition and virtual compatibility view, no issuer.

Registration uses the existing formal grant/import/test/publish methods inside
ONE database transaction. The actual admin row is removed before commit: no
cookie/principal is fabricated and no administrator permission persists.
"""
import copy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import time
import io
import zipfile

SLUG = "wechat-official-account-writing"
VERSION = "1.0.1"
ZIP_SHA = "6b37d4769cad4a8e0301a2b8c01fd90ca7ab8e0229c3e8df1e46d8a9d525a03b"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode()).hexdigest()


def need(value, code):
    if not value:
        raise RuntimeError(code)


def validate_delta(before, after, proof):
    need(set(before) == set(after), "REGISTRY_TABLE_SET")
    version, package = proof["version"], proof["package"]
    need(version["version"] == VERSION and version["status"] == "published"
         and version["checksum"] == ZIP_SHA
         and package["skill_version_id"] == version["id"]
         and package["sha256"] == ZIP_SHA, "REGISTRY_EXACT_PACKAGE")
    skill = next((r for r in before["skills"] if r["slug"] == SLUG), None)
    need(skill is not None and version["skill_id"] == skill["id"], "REGISTRY_EXISTING_SKILL")
    need(not any(r["skill_id"] == skill["id"] and r["version"] == VERSION
                 for r in before["skill_versions"]), "REGISTRY_ALREADY_EXISTS_NO_RETRY")
    for table in before:
        expected = list(before[table])
        if table == "skill_versions": expected.append(version)
        if table == "skill_packages": expected.append(package)
        need(sorted(digest(r) for r in after[table]) == sorted(digest(r) for r in expected),
             "REGISTRY_UNAPPROVED_ROW_CHANGE:" + table)
    need(not after["platform_admins"] and proof["temporary_admin_removed"] is True,
         "NO_PERSISTENT_ADMIN_GRANT")
    return True


def validate_files(before, after, proof, root, content):
    """Exactly one ZIP and its original draft/published members; old bytes/modes untouched."""
    need(hashlib.sha256(content).hexdigest() == ZIP_SHA, "REGISTRY_FILE_PACKAGE_SHA")
    package = Path(proof["package"]["storage_path"])
    need(package == root / "packages" / proof["version"]["id"] / (ZIP_SHA + ".zip"),
         "REGISTRY_PACKAGE_STORAGE_SCOPE")
    expected = {str(package): ZIP_SHA}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for member in archive.infolist():
            if member.is_dir(): continue
            name = Path(member.filename)
            need(name.parts[0] == SLUG and ".." not in name.parts, "REGISTRY_MEMBER_SCOPE")
            for directory in ("drafts", "published"):
                path = root / directory / SLUG / VERSION / Path(*name.parts[1:])
                expected[str(path)] = hashlib.sha256(archive.read(member)).hexdigest()
    need(set(after) == set(before) | set(expected) and not set(before) & set(expected), "REGISTRY_NEW_FILE_SET")
    need(all(after[name] == pin for name, pin in before.items()), "REGISTRY_ORIGINAL_FILE_CHANGED")
    need(all(after[name]["sha256"] == pin for name, pin in expected.items()), "REGISTRY_NEW_FILE_BYTES")
    return True


def project_original(data, witness, baseline):
    """Remove ONLY independently witnessed package rows in a copied view.
    It is not a DB cleanup and does not assert old seals cover new data.
    """
    result = copy.deepcopy(data)
    for table, key in (("skill_versions", "version"), ("skill_packages", "package")):
        spec = witness["registration"][key]
        found = [row for row in result[table] if row["id"] == spec["id"]]
        need(found == [spec], "REGISTRY_WITNESS_ROW_DRIFT")
        result[table] = [r for r in result[table] if r["id"] != spec["id"]]
    # Original task/account view is only for delegation to historical guard.
    # Actual new data is separately checked against the versioned active guard.
    for table in baseline["rows"]:
        if table == "credit_accounts":
            result[table] = copy.deepcopy(baseline["credit_accounts"])
        else:
            result[table] = [r for r in result[table] if digest(r) in baseline["rows"][table]]
    need({t: sorted(digest(r) for r in rows) for t, rows in result.items()} == baseline["rows"],
         "ORIGINAL_VIEW_NOT_EXACT")
    return result


class TransactionStore:
    """Existing Registry methods use the SAME already-open transaction."""
    def __init__(self, original, connection):
        self.is_postgres = original.is_postgres
        self.database_url = original.database_url
        self._connection = connection

    @contextmanager
    def connection(self):
        yield self._connection  # no nested commit/rollback/close


def register(store, registry_factory, grant_existing_admin, content, user_id, authorize,
             *, before_rows, after_rows):
    """Authority callback must fresh-check real exact scope at every boundary.
    No initialize(), schema DDL, Binding change or Runtime/Provider test.
    """
    need(hashlib.sha256(content).hexdigest() == ZIP_SHA, "REGISTRY_PACKAGE_SHA")
    authorize()
    before = before_rows()
    need(before["platform_admins"] == [], "EXISTING_ADMIN_SCOPE_CONFLICT")
    with store.connection() as connection:
        if store.is_postgres:
            connection.execute("SET LOCAL statement_timeout='30s'")
            connection.execute("SET LOCAL lock_timeout='5s'")
            connection.execute("SELECT pg_advisory_xact_lock(714360105)")
        else:
            connection.execute("BEGIN IMMEDIATE")
        locked_rows = after_rows(connection)
        need(set(locked_rows) == set(before) and all(
            sorted(digest(r) for r in locked_rows[t]) == sorted(digest(r) for r in before[t])
            for t in before), "REGISTRY_PRESTATE_CHANGED_BEFORE_LOCK")
        original = next((r for r in before["users"] if r["id"] == user_id), None)
        need(original and original.get("account_status") == "enabled", "REAL_ENABLED_REGISTRAR_REQUIRED")
        actual = connection.execute("SELECT id,email,tenant_id,role FROM users WHERE id=?", (user_id,)).fetchone()
        need(actual and actual["id"] == original["id"] and actual["email"] == original["email"]
             and actual["tenant_id"] == original["tenant_id"], "REAL_REGISTRAR_DRIFT")
        scoped = TransactionStore(store, connection)
        registry = registry_factory(scoped)
        skill = connection.execute("SELECT id,name,description FROM skills WHERE slug=?", (SLUG,)).fetchone()
        need(skill is not None, "EXISTING_SKILL_REQUIRED")
        need(not connection.execute("SELECT 1 FROM skill_versions WHERE skill_id=? AND version=?",
                                    (skill["id"], VERSION)).fetchone(), "REGISTRY_ALREADY_EXISTS_NO_RETRY")
        for path in (registry.drafts_root / SLUG / VERSION, registry.published_root / SLUG / VERSION):
            need(not path.exists() and not path.is_symlink(), "REGISTRY_FILES_COLLISION")
        authorize()
        code, grant = grant_existing_admin(scoped, registry, actual["email"], True)
        need(code == 0 and grant["status"] == "granted" and grant["user_id"] == user_id
             and registry.is_platform_admin(user_id), "FORMAL_ADMIN_INITIALIZATION_FAILED")
        authorize()
        item = registry.import_archive(SLUG, VERSION, skill["name"], skill["description"], content, user_id)
        need(registry.test_version(item["id"])["status"] == "passed", "FORMAL_PACKAGE_TEST_FAILED")
        authorize()
        registry.publish(item["id"], user_id)
        connection.execute("DELETE FROM platform_admins WHERE user_id=?", (user_id,))
        need(not registry.is_platform_admin(user_id), "ADMIN_REVOKE_BEFORE_COMMIT_FAILED")
        actual_rows = after_rows(connection)
        # The sealed row collector uses to_jsonb. Do not mix its timestamp
        # strings with DB-API datetime objects in a supposedly exact Hash.
        version = next(r for r in actual_rows["skill_versions"] if r["id"] == item["id"])
        package = next(r for r in actual_rows["skill_packages"] if r["skill_version_id"] == item["id"])
        proof = {"version": version, "package": package, "registrar_user_id": user_id,
                 "admin_initialization": "EXISTING_FORMAL_ENTRY_SAME_TRANSACTION",
                 "temporary_admin_removed": True, "bindings_changed": False,
                 "runtime_test": "NOT_EXECUTED", "provider_calls": 0}
        validate_delta(before, actual_rows, proof)
        authorize()  # after slow operations, immediately before outer COMMIT
    after = after_rows(None)
    validate_delta(before, after, proof)  # actual committed readback, not a printed label
    return proof
