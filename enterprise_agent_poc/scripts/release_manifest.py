"""Shared strict schema for immutable Release manifests."""
from __future__ import annotations

import re


BASE_FIELDS = {
    "release_id",
    "source_commit",
    "archive_sha256",
    "selected_files",
    "selected_file_count",
    "build_platform",
}
OPTIONAL_FIELDS = {"binding_transition", "forward_migrations", "deferred_skill", "skill_package_staging",
                   "agent_productization_transition", "runtime_only_release", "dual_source_release"}
HASH = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
RELEASE = re.compile(r"[A-Za-z0-9._-]+")


class ManifestContractError(ValueError):
    pass


def validate_runtime_only_release(value: object) -> None:
    require(isinstance(value, dict) and type(value.get("schema_version")) is int
            and type(value.get("pending")) is int and type(value.get("unknown")) is int
            and type(value.get("checksum_drift")) is int and value == {
        "schema_version": 1, "contract_id": "SCHEMA_015_RUNTIME_ONLY_RELEASE_V1",
        "release_mode": "RUNTIME_ONLY", "current_schema": "015", "target_schema": "015",
        "migration_action": "NONE", "migration015_state": "ALREADY_APPLIED",
        "pending": 0, "unknown": 0, "checksum_drift": 0,
        "migration015_blob": "96bba0ea64ade51670775cf6f69b806a2248342a",
        "recovery_contract": "migration-015-exact-predecessor-recovery-v1",
        "recovery_mode": "PREDECESSOR_ON_SCHEMA_015", "post_commit_runtime_rollback": "ENABLED",
    }, "runtime_only_release_contract")


def require(condition: bool, check: str) -> None:
    if not condition:
        raise ManifestContractError(check)


def _bindings(value: object) -> dict[str, str]:
    require(
        isinstance(value, dict)
        and bool(value)
        and all(isinstance(slug, str) and slug and isinstance(version, str) and version for slug, version in value.items()),
        "binding_transition",
    )
    return value


def validate_binding_transition(value: object) -> None:
    require(
        isinstance(value, dict)
        and set(value) == {"schema_version", "exact_predecessor", "transitions"}
        and type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and isinstance(value["transitions"], list)
        and bool(value["transitions"]),
        "binding_transition",
    )
    predecessor = value["exact_predecessor"]
    require(
        isinstance(predecessor, dict)
        and set(predecessor) == {"release_id", "source_commit", "archive_sha256", "manifest_sha256"}
        and isinstance(predecessor["release_id"], str)
        and RELEASE.fullmatch(predecessor["release_id"])
        and isinstance(predecessor["source_commit"], str)
        and COMMIT.fullmatch(predecessor["source_commit"])
        and isinstance(predecessor["archive_sha256"], str)
        and HASH.fullmatch(predecessor["archive_sha256"])
        and isinstance(predecessor["manifest_sha256"], str)
        and HASH.fullmatch(predecessor["manifest_sha256"]),
        "binding_transition",
    )
    agents: set[str] = set()
    for item in value["transitions"]:
        require(
            isinstance(item, dict)
            and set(item) == {"agent_id", "from_bindings", "to_bindings", "required_skill_identities"}
            and isinstance(item["agent_id"], str)
            and bool(item["agent_id"])
            and item["agent_id"] not in agents,
            "binding_transition",
        )
        agents.add(item["agent_id"])
        source = _bindings(item["from_bindings"])
        target = _bindings(item["to_bindings"])
        require(source != target, "binding_transition")
        identities = item["required_skill_identities"]
        require(isinstance(identities, list) and bool(identities), "binding_transition")
        pairs: set[tuple[str, str]] = set()
        for identity in identities:
            require(
                isinstance(identity, dict)
                and set(identity) == {"slug", "version", "source_sha256", "artifact_sha256"}
                and isinstance(identity["slug"], str)
                and bool(identity["slug"])
                and isinstance(identity["version"], str)
                and bool(identity["version"])
                and isinstance(identity["source_sha256"], str)
                and HASH.fullmatch(identity["source_sha256"])
                and isinstance(identity["artifact_sha256"], str)
                and HASH.fullmatch(identity["artifact_sha256"])
                and (identity["slug"], identity["version"]) not in pairs,
                "binding_transition",
            )
            pairs.add((identity["slug"], identity["version"]))
        require(pairs == set(target.items()), "binding_transition")


def validate_forward_migrations(value: object) -> None:
    """V1 is deliberately a single exact 012 -> 014 forward-safe plan."""
    if isinstance(value, dict) and value.get("schema_version") == 3:
        from scripts.release_schema016 import PLAN
        require(type(value['schema_version']) is int and value == PLAN, 'forward_migrations_016')
        return
    if isinstance(value, dict) and value.get("schema_version") == 2:
        require(type(value["schema_version"]) is int and value == {
            "schema_version": 2, "from_schema": "014", "target_schema": "015",
            "migrations": [{"version": "015", "filename": "015_chat_image_attachments.sql",
                "canonical_sha256": "67c4c85007d7ac1a9dc3f0e979359d6b3f9ea84206740d11b6ce05c11563f1fc"}],
            "rollback_strategy": "code_only_forward_safe",
            "recovery_contract": "migration-015-exact-predecessor-recovery-v1",
        }, "forward_migrations_015")
        return
    require(isinstance(value, dict) and set(value) == {
        "schema_version", "from_schema", "target_schema", "migrations", "rollback_strategy"
    }, "forward_migrations")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["from_schema"] == "012" and value["target_schema"] == "014"
            and value["rollback_strategy"] == "code_only_forward_safe", "forward_migrations")
    migrations = value["migrations"]
    require(isinstance(migrations, list) and len(migrations) == 2, "forward_migrations")
    for item, version, filename in zip(migrations, ("013", "014"), (
        "013_agent_revision_grounding_policy.sql", "014_agent_release_provenance.sql"
    )):
        require(isinstance(item, dict) and set(item) == {"version", "filename", "canonical_sha256"}
                and item["version"] == version and item["filename"] == filename
                and isinstance(item["canonical_sha256"], str)
                and HASH.fullmatch(item["canonical_sha256"]), "forward_migrations")


def validate_deferred_skill(value: object) -> None:
    require(isinstance(value, dict) and set(value) == {
        "slug", "version", "source_sha256", "artifact_sha256", "bundled_manifest_sha256",
        "registry_action"
    }, "deferred_skill")
    require(value["slug"] == "wechat-official-account-writing" and value["version"] == "1.0.0"
            and value["registry_action"] == "package_only_stage_in_phase_a"
            and all(isinstance(value[key], str) and HASH.fullmatch(value[key]) for key in (
                "source_sha256", "artifact_sha256", "bundled_manifest_sha256"
            )), "deferred_skill")


def validate_skill_package_staging(value: object) -> None:
    require(isinstance(value, dict) and set(value) == {"schema_version", "package", "rollback_strategy"},
            "skill_package_staging")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["rollback_strategy"] == "forward_safe_dormant", "skill_package_staging")
    package = value["package"]
    require(isinstance(package, dict) and set(package) == {
        "slug", "version", "source_sha256", "artifact_sha256", "bundled_manifest_sha256"
    } and package["slug"] == "wechat-official-account-writing" and package["version"] == "1.0.0"
            and all(isinstance(package[key], str) and HASH.fullmatch(package[key]) for key in (
                "source_sha256", "artifact_sha256", "bundled_manifest_sha256"
            )), "skill_package_staging")


def validate_agent_productization_transition(value: object) -> None:
    """Phase B is one approved ABSENT -> enabled Agent, not a wildcard plan."""
    require(isinstance(value, dict) and set(value) == {
        "schema_version", "agent_slug", "pre_state", "target_state",
        "rollback_strategy", "runtime_test", "skill_source_sha256",
        "bundled_manifest_sha256",
    }, "agent_productization_transition")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["agent_slug"] == "wechat-official-account-writing"
            and value["pre_state"] == "ABSENT"
            and value["target_state"] == "published_enabled"
            and value["rollback_strategy"] == "resolvable_absent"
            and value["skill_source_sha256"] == "f66f22e9e36e11b95301983f12a7b1550081f1e8c024bea7c955d90fc65dd1ae"
            and value["bundled_manifest_sha256"] == "3b90dcc33d4d9855c82f50597c2df186b1cefb66ee2324b1a6ad36b57e114c5f",
            "agent_productization_transition")
    test = value["runtime_test"]
    require(isinstance(test, dict) and set(test) == {
        "required", "tenant", "agent_slug", "actor", "exactly_one_validation_task",
        "skill_slug", "skill_version", "skill_package_sha256", "model_config_id",
        "credit_cost",
    } and test["required"] is True and test["exactly_one_validation_task"] is True
            and test["tenant"] == "zhiy-e-intelligence"
            and test["agent_slug"] == value["agent_slug"]
            and test["actor"] == "ba2afd04-0cfd-45fe-9771-ef8e741796ba"
            and test["skill_slug"] == value["agent_slug"]
            and test["skill_version"] == "1.0.0"
            and test["skill_package_sha256"] == "b0e54bc57e271b6b0c094f7dcfcb94d234f609b8925eb5937b105ccd6463c157"
            and test["model_config_id"] == "codex-deepseek-v4-pro-high"
            and type(test["credit_cost"]) is int and test["credit_cost"] == 5,
            "agent_productization_transition")


def validate_manifest_contract(manifest: object) -> dict:
    require(isinstance(manifest, dict), "manifest_fields")
    fields = set(manifest)
    require(BASE_FIELDS <= fields and fields <= BASE_FIELDS | OPTIONAL_FIELDS, "manifest_fields")
    require(
        isinstance(manifest["release_id"], str)
        and RELEASE.fullmatch(manifest["release_id"])
        and manifest["release_id"] not in {".", ".."}
        and isinstance(manifest["source_commit"], str)
        and COMMIT.fullmatch(manifest["source_commit"])
        and isinstance(manifest["archive_sha256"], str)
        and HASH.fullmatch(manifest["archive_sha256"])
        and isinstance(manifest["selected_files"], list)
        and bool(manifest["selected_files"])
        and all(isinstance(name, str) and bool(name) for name in manifest["selected_files"])
        and len(set(manifest["selected_files"])) == len(manifest["selected_files"])
        and type(manifest["selected_file_count"]) is int
        and manifest["selected_file_count"] == len(manifest["selected_files"])
        and isinstance(manifest["build_platform"], str)
        and bool(manifest["build_platform"]),
        "manifest_identity",
    )
    if "binding_transition" in manifest:
        validate_binding_transition(manifest["binding_transition"])
    if "runtime_only_release" in manifest:
        validate_runtime_only_release(manifest["runtime_only_release"])
        require(not ((OPTIONAL_FIELDS - {"runtime_only_release"}) & fields),
                "runtime_only_release_code_only_scope")
    if "forward_migrations" in manifest:
        validate_forward_migrations(manifest["forward_migrations"])
        if manifest['forward_migrations'].get('schema_version') == 3:
            require('dual_source_release' in manifest, 'schema016_dual_source_required')
    if 'dual_source_release' in manifest:
        from scripts.release_schema016 import declaration, PLAN
        from scripts.release_dual_source import APP_SOURCE
        pair=manifest['dual_source_release']
        require(isinstance(pair,dict) and pair==declaration(pair.get('tooling_source'),pair.get('tooling_tree'))
            and manifest['source_commit']==APP_SOURCE and manifest.get('forward_migrations')==PLAN
            and not ((OPTIONAL_FIELDS-{'dual_source_release','forward_migrations'}) & fields), 'dual_source_manifest_scope')
    if "deferred_skill" in manifest:
        validate_deferred_skill(manifest["deferred_skill"])
        require("skill_package_staging" in manifest, "skill_package_staging")
    if "skill_package_staging" in manifest:
        validate_skill_package_staging(manifest["skill_package_staging"])
        require("deferred_skill" in manifest and "binding_transition" not in manifest,
                "skill_package_staging")
        require(manifest["skill_package_staging"]["package"] == {
            key: manifest["deferred_skill"][key] for key in (
                "slug", "version", "source_sha256", "artifact_sha256", "bundled_manifest_sha256"
            )}, "skill_package_staging")
    if "agent_productization_transition" in manifest:
        validate_agent_productization_transition(manifest["agent_productization_transition"])
        require(not ({"forward_migrations", "binding_transition", "skill_package_staging", "deferred_skill"}
                     & fields), "agent_productization_transition")
    return manifest
