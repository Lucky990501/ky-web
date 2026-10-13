"""Observations of the unchanged 7dc consumer, NOT security acceptance tests.

These passing observations document a remaining BLOCKER: an independent
producer journal cannot make the frozen reader reject a consumer-valid record
after its final replace, even when persistence/readback is uncertain. They must
never be reported as closing that requirement. Root metadata/authority are
explicit doubles; files, atomic replace and injected failures are real private
POSIX operations. No Root, service, database or network operations occur.
"""
import os
import stat

import pytest

from test_v3_trusted_record_bootstrap import (
    artifact, pair, controlled, package, arguments, raw, sealed_policy, tool, binding,
)


@pytest.mark.parametrize("failure", ["directory_fsync", "record_readback"])
def test_blocker_observation_frozen_consumer_ignores_unknown_producer_state(
    package, monkeypatch, failure,
):
    """PASS means the unresolved consumer limitation was reproduced, not fixed."""
    sealed_policy(package)
    store = package["store"]
    deployment = store.path("deployment")
    original_fsync, original_read = os.fsync, store.read
    args = arguments(package, "seal-release-records")

    def fail_fsync(fd):
        if deployment.exists() and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("fixture final-record directory fsync uncertainty")
        return original_fsync(fd)

    def fail_read(path):
        if path == deployment:
            raise OSError("fixture final-record readback uncertainty")
        return original_read(path)

    with monkeypatch.context() as patch:
        if failure == "directory_fsync":
            patch.setattr(os, "fsync", fail_fsync)
        else:
            patch.setattr(store, "read", fail_read)
        with pytest.raises(tool.CommitUnknown) as result:
            tool.seal(**args)
        assert result.value.action == "seal-release-records"
        assert result.value.phase

    # Trusted independent readback in the private fixture distinguishes actual
    # visible bytes from the write call's exception; neither is a Root approval.
    observed, observed_raw = original_read(deployment)
    evidence, evidence_raw = original_read(store.path("evidence"))
    assert observed_raw == raw(package["deployment"])
    unknown = tool.operation_state(store, "seal-release-records")
    assert unknown["state"] == "PENDING_OR_UNKNOWN"
    assert unknown["intent"]["technical_grant_sha256"] == args["grant_sha"]
    previous = list(store.writes)
    with pytest.raises(tool.Blocked):
        tool.seal(**args)
    assert store.writes == previous

    result = binding._p0_qualification(
        store.qualifications, package["manifest"]["runtime_only_release"],
        tool.EXACT, observed, evidence, tool.sha(evidence_raw), "e" * 64, now=50,
    )
    assert result["status"] == "p0_controlled_qualification_verified"
    assert result["native_acceptance_claim"] is False
    assert tool.operation_state(store, "seal-release-records")["state"] == "PENDING_OR_UNKNOWN"
    # A mandatory reader-aware transition contract is still required before
    # claiming unknown-state consumption prevention or security READY.
