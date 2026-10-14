"""Single-request transport; disconnect => query SAME operation, never retry.

Only communicates with the separately approved bounded broker. It cannot
establish authority, sign Owner events, modify policy or run arbitrary SSH.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys

from lightweight_release_v1 import CONTROL, canonical, operation, owner_body, require, sha

BROKER = str(CONTROL / "tooling/enterprise_agent_poc/scripts/lightweight_release_linux_v1.py")


def call_ssh(alias, verb, payload=None, op=None):
    require(alias and all(c.isalnum() or c in "-_." for c in alias), "approved_connection_alias")
    require(verb in {"submit", "status"}, "bounded_client_verb")
    args = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2", alias, "lightweight-release-v1"]
    envelope = {"action": verb, "operation_id": operation(op)}
    if verb == "submit":
        envelope["payload"] = json.loads(payload)
    wire = canonical(envelope)
    try:
        result = subprocess.run(args, input=wire, capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "TRANSPORT_UNKNOWN_QUERY_SAME_OPERATION_ONLY", "operation_id": op}
    if result.returncode != 0:
        return {"status": "TRANSPORT_OR_REMOTE_REJECTED_QUERY_ONLY", "ssh_exit": result.returncode,
                "operation_id": op}
    try:
        receipt = json.loads(result.stdout)
        require(receipt["operation_id"] == op if verb == "submit" else "status" in receipt, "remote_receipt")
        require(receipt.get("status") != "BLOCKED", "remote_rejected")
        return receipt
    except (ValueError, KeyError, RuntimeError):
        return {"status": "REMOTE_CONFIRMATION_MISSING_QUERY_ONLY", "operation_id": op}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("verb", choices=("prepare-owner-text", "submit", "status"))
    p.add_argument("--plan", type=Path)
    p.add_argument("--archive", type=Path)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--source-bundle", type=Path)
    p.add_argument("--reports", type=Path)
    p.add_argument("--ssh-alias")
    p.add_argument("--operation-id")
    args = p.parse_args()
    if args.verb == "status":
        receipt = call_ssh(args.ssh_alias, "status", op=operation(args.operation_id))
    else:
        plan = json.loads(args.plan.read_bytes())
        op = operation(plan["operation_id"])
        if args.verb == "prepare-owner-text":
            print(owner_body(plan))
            return 0
        reports = {}
        for item in plan["evidence"].values():
            raw = (args.reports / f"{item['sha256']}.json").read_bytes()
            require(sha(raw) == item["sha256"], "local_report_hash")
            reports[item["sha256"]] = base64.b64encode(raw).decode()
        archive, manifest = args.archive.read_bytes(), args.manifest.read_bytes()
        bundle = args.source_bundle.read_bytes()
        require(sha(archive) == plan["candidate"]["archive_sha256"]
                and sha(manifest) == plan["candidate"]["raw_manifest_sha256"]
                and sha(bundle) == plan["source_bundle_sha256"], "local_artifact_hash")
        payload = canonical({"plan": plan, "archive": base64.b64encode(archive).decode(),
                             "manifest": base64.b64encode(manifest).decode(),
                             "source_bundle": base64.b64encode(bundle).decode(), "reports": reports})
        receipt = call_ssh(args.ssh_alias, "submit", payload=payload, op=op)
    print(json.dumps(receipt))
    return 0 if receipt["status"] in {"SUBMITTED_QUERY_SAME_OPERATION_ONLY", "COMMITTED", "RESTORED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
