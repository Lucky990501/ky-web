"""Actual pinned 7dc readers reject the successor state contract.

Historical source bytes come from an independently extracted Git archive and
are checked before execution. Only the protected-record reader is a fixture
boundary: no Root identity, genuine approval, installation or release exists.
Legacy records below are synthetic compatibility controls, never issued ones.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
from types import ModuleType

import pytest

from test_v3_state_contract import stateful, controlled, pair


FROZEN_PINS = {
    "release_manifest.py": "de6f5afae6f0ccc0731eca686bc503ac8cd7ccff99e583024ac8f9e31bab4b06",
    "schema015_successor_binding.py": "0a027b6dbdfc944cbd5f4855cee67bc5d36e5e3eb0feda7a36f64f83c356ef0b",
}


@pytest.fixture
def frozen_readers():
    location = os.environ.get("V3_FROZEN_READER_FIXTURE")
    assert location, "Actual pinned 7dc source fixture is required, not an optional skip"
    root = Path(location) / "enterprise_agent_poc/scripts"
    result = {}
    for filename, expected in FROZEN_PINS.items():
        path = root / filename
        assert path.is_file() and not path.is_symlink()
        captured = path.read_bytes()
        assert hashlib.sha256(captured).hexdigest() == expected
        # Execute captured, verified source only; no cached pyc or package init.
        module = ModuleType("frozen_7dc_" + path.stem)
        module.__file__ = str(path)
        exec(compile(captured, str(path), "exec", dont_inherit=True), module.__dict__)
        result[path.stem] = module
    return result


def test_actual_frozen_binding_rejects_stateful_declaration(stateful, frozen_readers):
    old = frozen_readers["schema015_successor_binding"]
    with pytest.raises(old.BindingBlocked):
        old.validate(stateful["value"])


@pytest.mark.parametrize("consumer", ["validate_runtime_only_release", "validate_manifest_contract"])
def test_actual_frozen_manifest_rejects_version4(stateful, frozen_readers, consumer):
    old = frozen_readers["release_manifest"]
    value = stateful["value"] if consumer == "validate_runtime_only_release" else stateful["manifest"]
    with pytest.raises(old.ManifestContractError, match="runtime_only_release_contract"):
        getattr(old, consumer)(value)


@pytest.mark.parametrize("claimed_version", [2, 3])
def test_actual_frozen_binding_cannot_downgrade_new_state_fields(
    stateful, frozen_readers, claimed_version,
):
    old = frozen_readers["schema015_successor_binding"]
    value = copy.deepcopy(stateful["value"])
    value["schema_version"] = claimed_version
    with pytest.raises(old.BindingBlocked):
        old.validate(value)


@pytest.mark.parametrize("new_record,code", [
    ("policy", "p0_policy_approval_fields"),
    ("deployment", "p0_deployment_approval_fields"),
    ("evidence", "p0_evidence_schema_identity"),
])
def test_actual_frozen_consumer_rejects_each_new_record_format(
    stateful, frozen_readers, monkeypatch, new_record, code,
):
    old = frozen_readers["schema015_successor_binding"]
    value = copy.deepcopy(stateful["value"])
    for field in ("state_contract", "security_base_source", "security_base_tree"):
        value.pop(field)
    value.update(schema_version=3, contract_id=old.P0_CONTRACT,
                 recovery_contract=old.P0_CONTRACT, qualification_profile=old.P0_CONTRACT)
    # The positive control belongs to the actual7dc/562 historical contract,
    # never the current d906 deployment or recovery authority.
    value.update(business_base_source=old.P0_SOURCE, business_base_tree=old.P0_TREE,
                 exact_predecessor=copy.deepcopy(old.PREDECESSOR))
    value["successor"]["parent_source_commit"] = old.P0_SOURCE
    old.validate(value)  # Control proves the historical V3 path is exercised.
    candidate = copy.deepcopy(stateful["candidate"])
    candidate["parent_source_commit"] = old.P0_SOURCE
    records = {name: copy.deepcopy(stateful[name]) for name in ("policy", "deployment", "evidence")}
    for record in records.values():
        record.pop("state_contract")
        record.update(schema_version=1, candidate=candidate, predecessor=copy.deepcopy(old.PREDECESSOR),
                      binding_sha256=old.digest(value))
    historical_evidence = records["evidence"]
    historical_evidence.update(replacements=copy.deepcopy(old.P0_REPLACEMENTS),
                               uncovered_risks=copy.deepcopy(old.P0_RISKS))
    historical_evidence["checks"]["exact_562_compatibility"] = historical_evidence["checks"].pop("exact_d906_compatibility")
    # The historical control must retain its own exact PG16.6 contract,
    # rather than borrowing the successor's Production16.14 fixture.
    for name, check in records["evidence"]["checks"].items():
        check["evidence_kind"] = old.P0_CHECKS[name]
        facts = check["measurements"]
        if "predecessor_source" in facts:
            facts["predecessor_source"] = old.PREDECESSOR["source_commit"]
        if "predecessor_archive_sha256" in facts:
            facts["predecessor_archive_sha256"] = old.PREDECESSOR["archive_sha256"]
        if name in old.P0_PG_CHECKS:
            check["measurements"]["postgres_version"] = "16.6"

    def checksum(record):
        return hashlib.sha256((json.dumps(record, sort_keys=True) + "\n").encode()).hexdigest()

    def verify():
        policy_hash, evidence_hash = checksum(records["policy"]), checksum(records["evidence"])
        records["deployment"].update(policy_approval_sha256=policy_hash, evidence_sha256=evidence_hash)
        # Synthetic metadata boundary only, not an authenticated Root reader.
        monkeypatch.setattr(old, "protected_json", lambda _: (records["policy"], policy_hash))
        return old._p0_qualification(stateful["paths"], value, candidate, records["deployment"],
                                     records["evidence"], evidence_hash, "e" * 64, now=20)

    assert verify()["status"] == "p0_controlled_qualification_verified"
    records[new_record].update(schema_version=2, state_contract=stateful["value"]["state_contract"])
    with pytest.raises(old.BindingBlocked, match=code):
        verify()
