"""Fail-closed, release-scoped Registry binding transitions.

The declaration is embedded in the immutable candidate Release manifest.  This
tool never infers a desired binding from a checkout or the current Registry.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bundled_skills import sha256, validate_bundle
from app.skill_registry import SkillRegistry, SkillRegistryError
from app.store import POCStore

HASH = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")


class TransitionBlocked(ValueError):
    pass


def require(value: bool, reason: str) -> None:
    if not value:
        raise TransitionBlocked(reason)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransitionBlocked("release_manifest_unreadable") from exc
    require(isinstance(value, dict), "release_manifest_invalid")
    return value


def bindings(value: object) -> dict[str, str]:
    require(isinstance(value, dict) and value and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()), "transition_binding_shape")
    return dict(sorted(value.items()))


def declaration(candidate: dict, predecessor: dict, predecessor_manifest_sha: str) -> list[dict]:
    value = candidate.get("binding_transition")
    require(isinstance(value, dict) and set(value) == {"schema_version", "exact_predecessor", "transitions"}, "binding_transition_missing_or_invalid")
    require(value["schema_version"] == 1 and isinstance(value["transitions"], list) and value["transitions"], "binding_transition_invalid")
    exact = value["exact_predecessor"]
    require(isinstance(exact, dict) and set(exact) == {"release_id", "source_commit", "archive_sha256", "manifest_sha256"}, "exact_predecessor_invalid")
    raw = predecessor.get("release_id")
    require(all(isinstance(exact[k], str) for k in exact) and HASH.fullmatch(exact["archive_sha256"]) and HASH.fullmatch(exact["manifest_sha256"]) and COMMIT.fullmatch(exact["source_commit"]), "exact_predecessor_invalid")
    require(exact["release_id"] == raw and exact["source_commit"] == predecessor.get("source_commit") and exact["archive_sha256"] == predecessor.get("archive_sha256") and exact["manifest_sha256"] == predecessor_manifest_sha, "EXACT_PREDECESSOR_MISMATCH")
    result = []
    ids = set()
    for item in value["transitions"]:
        require(isinstance(item, dict) and set(item) == {"agent_id", "from_bindings", "to_bindings", "required_skill_identities"}, "binding_transition_invalid")
        agent = item["agent_id"]
        require(isinstance(agent, str) and agent and agent not in ids, "binding_transition_invalid")
        ids.add(agent)
        required = item["required_skill_identities"]
        require(isinstance(required, list) and required, "binding_transition_invalid")
        require(all(isinstance(x, dict) and set(x) == {"slug", "version", "source_sha256", "artifact_sha256"} and isinstance(x["slug"], str) and isinstance(x["version"], str) and HASH.fullmatch(x["source_sha256"]) and HASH.fullmatch(x["artifact_sha256"]) for x in required), "binding_transition_invalid")
        result.append({"agent_id": agent, "from": bindings(item["from_bindings"]), "to": bindings(item["to_bindings"]), "required": required})
    return result


def candidate_packages(bundle_root: Path) -> dict[tuple[str, str], dict]:
    entries = validate_bundle(bundle_root)
    return {(entry["skill_slug"], entry["version"]): entry for entry in entries}


def package_only_declaration(candidate: dict, packages: dict[tuple[str, str], dict], bundle_root: Path) -> tuple[dict, dict]:
    """A single explicit immutable package, with no implicit binding or Agent action."""
    from scripts.release_manifest import validate_skill_package_staging

    value = candidate.get("skill_package_staging")
    validate_skill_package_staging(value)
    require("binding_transition" not in candidate, "package_only_binding_transition_forbidden")
    pinned = value["package"]
    deferred = candidate.get("deferred_skill")
    require(isinstance(deferred, dict) and deferred.get("registry_action") == "package_only_stage_in_phase_a"
            and {key: deferred.get(key) for key in pinned} == pinned, "package_only_agent_absence_declaration")
    require(sha256((bundle_root / "manifest.json").read_bytes()) == pinned["bundled_manifest_sha256"],
            "SKILL_PACKAGE_IDENTITY_CONFLICT")
    entry = packages.get((pinned["slug"], pinned["version"]))
    require(entry is not None and entry["artifact_sha256"] == pinned["artifact_sha256"]
            and entry["source_identity"]["files"] == [{"path": "SKILL.md", "sha256": pinned["source_sha256"],
                                                        "git_mode": "100644"}],
            "SKILL_PACKAGE_IDENTITY_CONFLICT")
    return pinned, entry


def package_only_state(registry: SkillRegistry, pinned: dict, *, required: bool = False) -> str:
    """Read-only ABSENT/EXACT check; conflict cannot be treated as an absent package."""
    with registry._read_connection() as conn:
        rows = conn.execute(
            "SELECT s.slug,v.id,v.skill_id,v.version,v.status,v.checksum,"
            "p.storage_path,p.sha256 AS package_sha256,p.size_bytes "
            "FROM skill_versions v JOIN skills s ON s.id=v.skill_id "
            "LEFT JOIN skill_packages p ON p.skill_version_id=v.id "
            "WHERE s.slug=? AND v.version=?", (pinned["slug"], pinned["version"]),
        ).fetchall()
        require(len(rows) <= 1, "SKILL_PACKAGE_IDENTITY_CONFLICT")
        require(not conn.execute(
            "SELECT 1 FROM agent_skill_bindings b JOIN skills s ON s.id=b.skill_id "
            "WHERE s.slug=? LIMIT 1", (pinned["slug"],)
        ).fetchone(), "PACKAGE_ONLY_BINDING_FORBIDDEN")
    if not rows:
        require(not required, "DECLARED_SKILL_PACKAGE_MISSING")
        return "ABSENT"
    row = rows[0]
    require(row["status"] == "published" and row["checksum"] == pinned["artifact_sha256"]
            and row["storage_path"] is not None, "SKILL_PACKAGE_IDENTITY_CONFLICT")
    try:
        registry._verify_package(row, expected=pinned["artifact_sha256"])
    except (SkillRegistryError, OSError) as exc:
        raise TransitionBlocked("SKILL_PACKAGE_IDENTITY_CONFLICT") from exc
    return "EXACT"


def package_only_preflight(registry: SkillRegistry, pinned: dict, packages: dict[tuple[str, str], dict]) -> dict:
    """No Registry writes. Every other bundled package must already be installed."""
    state = package_only_state(registry, pinned)
    declared_key = (pinned["slug"], pinned["version"])
    with registry._read_connection() as conn:
        require(not conn.execute("SELECT 1 FROM agent_templates WHERE slug=?", (pinned["slug"],)).fetchone(),
                "PHASE_A_AGENT_VISIBILITY_BLOCK")
        for key, entry in packages.items():
            if key == declared_key:
                continue
            row = conn.execute(
                "SELECT s.slug,v.id,v.skill_id,v.version,v.status,v.checksum,"
                "p.storage_path,p.sha256 AS package_sha256,p.size_bytes "
                "FROM skill_versions v JOIN skills s ON s.id=v.skill_id "
                "LEFT JOIN skill_packages p ON p.skill_version_id=v.id "
                "WHERE s.slug=? AND v.version=?", key,
            ).fetchone()
            require(row is not None and row["status"] in {"published", "deprecated"},
                    "UNDECLARED_CANDIDATE_PACKAGE_MISSING")
            registry._verify_package(row, expected=entry["artifact_sha256"])
    return {"status": "DECLARED_PACKAGE_ONLY_READY", "registry_pre_state": state,
            "package": {"slug": pinned["slug"], "version": pinned["version"]},
            "binding_transition": "NONE"}


def verify_candidate_packages(transitions: list[dict], packages: dict[tuple[str, str], dict]) -> None:
    for transition in transitions:
        required = {(x["slug"], x["version"]): x for x in transition["required"]}
        require(set(required) == set(transition["to"].items()), "transition_required_packages_mismatch")
        for key, identity in required.items():
            entry = packages.get(key)
            require(entry is not None and entry["artifact_sha256"] == identity["artifact_sha256"], "TO_SKILL_PACKAGE_IDENTITY_MISMATCH")
            source = entry["source_identity"]["files"]
            require(len(source) == 1 and source[0]["path"] == "SKILL.md" and source[0]["sha256"] == identity["source_sha256"], "TO_SKILL_PACKAGE_IDENTITY_MISMATCH")


def registry_bindings(conn, agent_id: str) -> dict[str, str]:
    rows = conn.execute("SELECT s.slug,v.version FROM agent_skill_bindings b JOIN skills s ON s.id=b.skill_id JOIN skill_versions v ON v.id=b.skill_version_id WHERE b.agent_id=? ORDER BY s.slug", (agent_id,)).fetchall()
    return {row["slug"]: row["version"] for row in rows}


def registry_manifest(conn, agent_id: str) -> dict[str, str]:
    row = conn.execute("SELECT skill_manifest FROM agent_templates WHERE id=?", (agent_id,)).fetchone()
    require(row is not None, "transition_agent_missing")
    try:
        return dict(sorted(json.loads(row["skill_manifest"]).items()))
    except (TypeError, ValueError, AttributeError) as exc:
        raise TransitionBlocked("registry_agent_manifest_invalid") from exc


def preflight(registry: SkillRegistry, transitions: list[dict], packages: dict[tuple[str, str], dict]) -> dict:
    verify_candidate_packages(transitions, packages)
    with registry._read_connection() as conn:
        # Verify every currently registered immutable package/cache without
        # requiring candidate-only TO packages to have been pre-staged.
        rows = conn.execute("SELECT v.id,v.skill_id,v.version,v.status,v.checksum,s.slug,p.storage_path,p.sha256 AS package_sha256,p.size_bytes FROM skill_versions v JOIN skills s ON s.id=v.skill_id JOIN skill_packages p ON p.skill_version_id=v.id").fetchall()
        for row in rows:
            registry._verify_package(row)
        for transition in transitions:
            active = registry_bindings(conn, transition["agent_id"])
            require(active == transition["from"], "CURRENT_BINDINGS_NOT_DECLARED_FROM")
            require(registry_manifest(conn, transition["agent_id"]) == active, "REGISTRY_BINDING_MANIFEST_DRIFT")
    return {"status": "DECLARED_BINDING_TRANSITION_READY", "transitions": [{"agent_id": t["agent_id"], "from_bindings": t["from"], "to_bindings": t["to"]} for t in transitions]}


def stage_candidate_packages(
    registry: SkillRegistry,
    packages: dict[tuple[str, str], dict],
    bundle_root: Path,
) -> dict:
    """Add missing immutable Candidate packages, then run the startup consumer."""
    staged, reused = [], []
    for key, entry in packages.items():
        slug, version = key
        with registry._store.connection() as conn:
            row = conn.execute(
                "SELECT v.id,v.skill_id,v.version,v.status,v.checksum,s.slug,"
                "p.storage_path,p.sha256 AS package_sha256,p.size_bytes "
                "FROM skill_versions v JOIN skills s ON s.id=v.skill_id "
                "JOIN skill_packages p ON p.skill_version_id=v.id "
                "WHERE s.slug=? AND v.version=?",
                (slug, version),
            ).fetchone()
        if row is None:
            content = (bundle_root / entry["artifact_path"]).read_bytes()
            registry._upsert_import(
                slug,
                version,
                slug,
                "Release-staged immutable bundled Skill",
                content,
                None,
                published=True,
            )
            staged.append({"slug": slug, "version": version})
        else:
            require(
                row["status"] in {"published", "deprecated"}
                and row["checksum"] == entry["artifact_sha256"],
                "CANDIDATE_SKILL_PACKAGE_IDENTITY_MISMATCH",
            )
            registry._verify_package(row, expected=entry["artifact_sha256"])
            reused.append({"slug": slug, "version": version})

        with registry._store.connection() as conn:
            checked = conn.execute(
                "SELECT v.id,v.skill_id,v.version,v.status,v.checksum,s.slug,"
                "p.storage_path,p.sha256 AS package_sha256,p.size_bytes "
                "FROM skill_versions v JOIN skills s ON s.id=v.skill_id "
                "JOIN skill_packages p ON p.skill_version_id=v.id "
                "WHERE s.slug=? AND v.version=?",
                (slug, version),
            ).fetchone()
        require(checked is not None, "CANDIDATE_SKILL_PACKAGE_STAGING_FAILED")
        registry._verify_package(checked, expected=entry["artifact_sha256"])

    # This is the same fail-closed consumer invoked by Candidate API startup.
    # It must pass while bindings are still FROM and before release-current moves.
    registry.initialize()
    return {"status": "candidate_packages_ready", "staged": staged, "reused": reused}


def stage_and_apply(registry: SkillRegistry, transitions: list[dict], packages: dict[tuple[str, str], dict], bundle_root: Path, *, rollback: bool) -> dict:
    package_staging = None
    if not rollback:
        verify_candidate_packages(transitions, packages)
        package_staging = stage_candidate_packages(registry, packages, bundle_root)
    with registry._store.connection() as conn:
        result = []
        for transition in transitions:
            expected, target = (transition["to"], transition["from"]) if rollback else (transition["from"], transition["to"])
            active = registry_bindings(conn, transition["agent_id"])
            if active == target:
                require(registry_manifest(conn, transition["agent_id"]) == target, "REGISTRY_BINDING_MANIFEST_DRIFT")
                result.append({"agent_id": transition["agent_id"], "result": "already_rolled_back" if rollback else "already_applied"})
                continue
            require(active == expected and registry_manifest(conn, transition["agent_id"]) == expected, "UNKNOWN_BINDING_STATE")
            versions = {}
            for slug, version in target.items():
                row = conn.execute("SELECT s.id AS skill_id,v.id AS version_id FROM skills s JOIN skill_versions v ON v.skill_id=s.id WHERE s.slug=? AND v.version=? AND v.status='published'", (slug, version)).fetchone()
                require(row is not None, "TO_SKILL_PACKAGE_MISSING")
                versions[slug] = row
            conn.execute("DELETE FROM agent_skill_bindings WHERE agent_id=?", (transition["agent_id"],))
            for slug, row in versions.items():
                conn.execute("INSERT INTO agent_skill_bindings(agent_id,skill_id,skill_version_id) VALUES (?,?,?)", (transition["agent_id"], row["skill_id"], row["version_id"]))
            manifest = dict(sorted(target.items()))
            conn.execute("UPDATE agent_templates SET skill_manifest=? WHERE id=?", (json.dumps(manifest, sort_keys=True), transition["agent_id"]))
            result.append({"agent_id": transition["agent_id"], "result": "rolled_back" if rollback else "applied"})
    return {
        "status": "binding_transition_rolled_back" if rollback else "binding_transition_applied",
        "package_staging": package_staging,
        "results": result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--predecessor-manifest", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    try:
        require(not (args.apply and args.rollback), "transition_mode_invalid")
        candidate, predecessor = load_json(args.candidate_manifest), load_json(args.predecessor_manifest)
        if "binding_transition" not in candidate and "skill_package_staging" not in candidate:
            print(json.dumps({"status": "NO_DECLARED_BINDING_TRANSITION"}))
            return 0
        packages = candidate_packages(args.bundle_root)
        database_url = os.environ["ENTERPRISE_POC_DATABASE_URL"]
        registry = SkillRegistry(POCStore(database_url), args.data_dir / "skill-registry", args.bundle_root)
        if "skill_package_staging" in candidate:
            pinned, entry = package_only_declaration(candidate, packages, args.bundle_root)
            if args.rollback:
                state = package_only_state(registry, pinned)
                result = {"status": "package_only_rollback_guard_passed", "package_state": state,
                          "rollback_strategy": "forward_safe_dormant", "binding_transition": "NONE"}
            else:
                result = package_only_preflight(registry, pinned, packages)
                if args.apply:
                    staged = stage_candidate_packages(registry, {(pinned["slug"], pinned["version"]): entry},
                                                      args.bundle_root)
                    require(package_only_state(registry, pinned, required=True) == "EXACT",
                            "DECLARED_SKILL_PACKAGE_MISSING")
                    result = {"status": "package_only_staged", "package_staging": staged,
                              "rollback_strategy": "forward_safe_dormant", "binding_transition": "NONE"}
        else:
            transitions = declaration(candidate, predecessor, sha256(args.predecessor_manifest.read_bytes()))
            if not args.apply and not args.rollback:
                result = preflight(registry, transitions, packages)
            else:
                result = stage_and_apply(registry, transitions, packages, args.bundle_root, rollback=args.rollback)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (TransitionBlocked, SkillRegistryError, KeyError, OSError, ValueError) as exc:
        check = str(exc) if isinstance(exc, TransitionBlocked) and str(exc) == "SKILL_PACKAGE_IDENTITY_CONFLICT" else "release_scoped_binding_transition"
        print(json.dumps({"status": "BLOCKED", "check": check}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
