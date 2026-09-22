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
OPTIONAL_FIELDS = {"binding_transition"}
HASH = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
RELEASE = re.compile(r"[A-Za-z0-9._-]+")


class ManifestContractError(ValueError):
    pass


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


def validate_manifest_contract(manifest: object) -> dict:
    require(isinstance(manifest, dict), "manifest_fields")
    fields = set(manifest)
    require(fields == BASE_FIELDS or fields == BASE_FIELDS | OPTIONAL_FIELDS, "manifest_fields")
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
    return manifest
