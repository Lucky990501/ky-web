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


def stage_and_apply(registry: SkillRegistry, transitions: list[dict], packages: dict[tuple[str, str], dict], bundle_root: Path, *, rollback: bool) -> dict:
    # Additive staging is intentionally outside active-binding mutation. It
    # publishes immutable package bytes only; it never changes an Agent binding.
    if not rollback:
        for transition in transitions:
            for slug, version in transition["to"].items():
                with registry._store.connection() as conn:
                    row = conn.execute("SELECT v.status,v.checksum FROM skill_versions v JOIN skills s ON s.id=v.skill_id WHERE s.slug=? AND v.version=?", (slug, version)).fetchone()
                entry = packages[(slug, version)]
                if row is None:
                    registry._upsert_import(slug, version, slug, "Release-staged immutable bundled Skill", (bundle_root / entry["artifact_path"]).read_bytes(), None, published=True)
                elif row["status"] not in {"published", "deprecated"} or row["checksum"] != entry["artifact_sha256"]:
                    raise TransitionBlocked("TO_SKILL_PACKAGE_IDENTITY_MISMATCH")
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
    return {"status": "binding_transition_rolled_back" if rollback else "binding_transition_applied", "results": result}


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
        if "binding_transition" not in candidate:
            print(json.dumps({"status": "NO_DECLARED_BINDING_TRANSITION"}))
            return 0
        transitions = declaration(candidate, predecessor, sha256(args.predecessor_manifest.read_bytes()))
        packages = candidate_packages(args.bundle_root)
        database_url = os.environ["ENTERPRISE_POC_DATABASE_URL"]
        registry = SkillRegistry(POCStore(database_url), args.data_dir / "skill-registry", args.bundle_root)
        if not args.apply and not args.rollback:
            result = preflight(registry, transitions, packages)
        else:
            result = stage_and_apply(registry, transitions, packages, args.bundle_root, rollback=args.rollback)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (TransitionBlocked, SkillRegistryError, KeyError, OSError, ValueError):
        print(json.dumps({"status": "BLOCKED", "check": "release_scoped_binding_transition"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
