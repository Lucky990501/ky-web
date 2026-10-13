"""Typed approval identity regressions. All actors/decisions are fictional.

Actual private files and the frozen consumer contract are exercised; no Root,
Owner authentication, approval issuance, installation or release is performed.
"""
from contextlib import contextmanager

import pytest

from test_v3_trusted_record_bootstrap import (
    artifact, pair, controlled, package, arguments, sealed_policy, raw, tool, binding,
)


def test_original_04_technical_audit_cannot_reuse_existing_owner_policy(package):
    # Original 04 failing probe preserved in .v3-bootstrap-59d-independent-qa-v1.
    sealed_policy(package)
    args = arguments(package, "seal-release-records", audit_id=package["policy"]["audit_id"])
    previous = list(package["store"].writes)
    with pytest.raises(tool.Blocked, match="SEPARATE|CONSUMED"):
        tool.seal(**args)
    assert package["store"].writes == previous


@pytest.mark.parametrize("reused", ["owner", "deployment", "prior_technical"])
def test_new_release_technical_identity_is_distinct_from_all_decisions(package, reused):
    sealed_policy(package)
    prior = arguments(package)["grant"]["audit_id"]
    audit_id = {"owner": package["policy"]["audit_id"],
                "deployment": package["deployment"]["audit_id"], "prior_technical": prior}[reused]
    previous = list(package["store"].writes)
    with pytest.raises(tool.Blocked, match="SEPARATE|CONSUMED"):
        tool.seal(**arguments(package, "seal-release-records", audit_id=audit_id))
    assert package["store"].writes == previous
    assert not package["store"].path("deployment").exists()


def test_deployment_identity_cannot_reuse_prior_technical_identity(package):
    sealed_policy(package)
    package["deployment"]["audit_id"] = arguments(package)["grant"]["audit_id"]
    with pytest.raises(tool.Blocked, match="DECISION_TYPES_MUST_BE_SEPARATE"):
        tool.seal(**arguments(package, "seal-release-records"))
    assert not package["store"].path("deployment").exists()


@pytest.mark.parametrize("field,value", [
    ("scope", "P0_EXACT_PAIR_POLICY_CHANGE"),
    ("authority", "PROJECT_OWNER_POLICY_AUTHORITY"),
    ("policy_audit_id", "FORGED_LINK"),
    ("policy_approval_sha256", "0" * 64),
    ("audit_id", "policy-owner-actual-001"),
])
def test_typed_deployment_and_exact_owner_relation_cannot_be_forged(package, field, value):
    sealed_policy(package)
    package["deployment"][field] = package["policy"]["audit_id"] if field == "audit_id" else value
    with pytest.raises((tool.Blocked, binding.BindingBlocked)):
        tool.seal(**arguments(package, "seal-release-records"))
    assert not package["store"].path("deployment").exists()


@pytest.mark.parametrize("when", ["under_lock", "after_tty"])
def test_protected_policy_rechecked_at_publication_boundaries(package, monkeypatch, when):
    sealed_policy(package)
    args = arguments(package, "seal-release-records")
    # The original request passed preflight. Emulate independently changed
    # protected bytes before the locked check, or during human TTY latency.
    def change_policy(*_):
        changed = dict(package["policy"], audit_id=args["grant"]["audit_id"])
        package["store"].path("policy").write_bytes(raw(changed))
    if when == "after_tty":
        args["attest"] = change_policy
    else:
        original = package["store"].lock
        @contextmanager
        def changed_lock():
            with original():
                change_policy()
                yield
        monkeypatch.setattr(package["store"], "lock", changed_lock)
    with pytest.raises((tool.Blocked, binding.BindingBlocked)):
        tool.seal(**args)
    assert not package["store"].path("deployment").exists()


@pytest.mark.parametrize("fault", ["wrong_type", "wrong_filename", "wrong_source"])
def test_prior_technical_snapshot_identity_cannot_be_relabelled(package, fault):
    sealed_policy(package)
    prior = arguments(package)["grant"]
    path = package["store"].audit / (prior["audit_id"] + ".technical-decision.json")
    if fault == "wrong_type":
        prior["authority"] = "PROJECT_OWNER_POLICY_AUTHORITY"
    elif fault == "wrong_filename":
        prior["audit_id"] = "DIFFERENT_SNAPSHOT_ID"
    else:
        prior["candidate"] = dict(tool.EXACT, source_commit="0" * 40)
    path.write_bytes(raw(prior))
    with pytest.raises(tool.Blocked, match="TECHNICAL_HISTORY_IDENTITY_DRIFT"):
        tool.seal(**arguments(package, "seal-release-records"))
    assert not package["store"].path("deployment").exists()


def test_independent_typed_decisions_preserve_raw_bytes_and_references(package):
    sealed_policy(package)
    store = package["store"]
    original_policy = store.path("policy").read_bytes()
    tool.seal(**arguments(package, "seal-release-records"))
    assert store.path("policy").read_bytes() == original_policy
    assert store.path("deployment").read_bytes() == raw(package["deployment"])
    deployment = store.read(store.path("deployment"))[0]
    assert deployment["policy_audit_id"] == package["policy"]["audit_id"]
    assert deployment["policy_approval_sha256"] == tool.sha(original_policy)
    ids = {package["policy"]["audit_id"], deployment["audit_id"],
           arguments(package, "seal-release-records")["grant"]["audit_id"]}
    assert len(ids) == 3


def test_revocation_technical_identity_cannot_reuse_prior_deployment(package):
    sealed_policy(package)
    tool.seal(**arguments(package, "seal-release-records"))
    previous = list(package["store"].writes)
    with pytest.raises(tool.Blocked, match="DECISIONS_MUST_BE_SEPARATE"):
        tool.seal(**arguments(package, "revoke-policy", audit_id=package["deployment"]["audit_id"]))
    assert package["store"].writes == previous


def test_exact_revocation_identity_resume_is_not_new_approval(package):
    sealed_policy(package)
    args = arguments(package, "revoke-policy")
    path = package["store"].audit / (args["grant"]["audit_id"] + ".technical-decision.json")
    package["store"].create(path, args["grant_raw"])
    # Only the identity helper accepts exact continuation. Persistent recovery
    # state must separately prove that this is the same pending operation.
    tool.approval_identity_separation(package["store"], args["action"], args["grant"],
                                     args["grant_raw"], args["records"], args["record_raw"], 50)
    changed = arguments(package, "revoke-policy", expires_at=99)
    with pytest.raises(tool.Blocked, match="ALREADY_CONSUMED"):
        tool.approval_identity_separation(package["store"], changed["action"], changed["grant"],
                                         changed["grant_raw"], changed["records"], changed["record_raw"], 50)


def test_root_tty_assertion_does_not_replace_owner_decision(package):
    args = arguments(package)
    args["records"]["policy"]["authority"] = "PRODUCTION_ROOT_MAINTENANCE_AUTHORITY"
    args["record_raw"]["policy"] = raw(args["records"]["policy"])
    args["grant"]["input_sha256"]["policy"] = tool.sha(args["record_raw"]["policy"])
    args["grant_raw"] = raw(args["grant"])
    args["grant_sha"] = tool.sha(args["grant_raw"])
    with pytest.raises(tool.Blocked, match="INDEPENDENT_OWNER_DECISION_REQUIRED"):
        tool.seal(**args)
    assert package["store"].writes == []
