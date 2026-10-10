"""Internal Test orchestration, not a public endpoint or a second Skill runner.

The caller supplies a normal authenticated session, never a caller/role claim.
The native Test harness must mount a root-protected, Source-bound approval and
the existing fresh isolation guard. Neither is accepted in a Run request.
Dispatch stays in the existing separately sealed Platform MCP service.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from app.agent_execution import profile
from app.security import RuntimePrincipal
from app.skill_dispatch import CODES, SkillActionDispatcher
from app.skill_python_runtime import git_identity

CONTRACT = "TEST_CONTROLLED_SKILL_ACTION_V1"
APPROVAL_PATH = Path("/etc/enterprise-agent-integrated-test-v3.3/controlled-skill-action.v1.json")
PROJECT = Path(__file__).resolve().parents[1]
DISPATCH_SOURCE = "b0e96dfd6dfa3d3b71f5b27cb4229eb4905f6092"
DISPATCH_TREE = "aea4cfc6ae6990f08c6c7f860678bab3d85a40e4"
DISPATCH_CONTRACT_SHA = "71220fd80a27b751db8632f65fc8b2d3b8837eb6b691e59dbf49cffdedcc9c92"
NOT_ALLOWED = "CONTROLLED_SKILL_ACTION_NOT_ALLOWED"
AUTH_BLOCKED = "SKILL_CONTROLLED_ACTION_AUTH_BLOCKED"
ZERO_PROVIDER = "ZERO_PROVIDER_CONTRACT_VIOLATION"
TASK_MARKER = "\n[TEST_CONTROLLED_SKILL_ACTION_V1]"
MCP_URL = "http://127.0.0.1:18101/mcp"


class ControlledActionError(PermissionError):
    def __init__(self, code):
        self.code = code if code in CODES | {NOT_ALLOWED, AUTH_BLOCKED, ZERO_PROVIDER} else AUTH_BLOCKED
        super().__init__(self.code)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_approval(value):
    """No default tenant, actor, Source, scope, fixture or implicit revision."""
    keys = {"contract", "environment", "authority_id", "fixture_identity", "source_commit",
            "source_tree", "tenant_id", "user_id", "user_email", "actions", "mcp_url",
            "dispatch_source", "dispatch_tree", "dispatch_contract_sha256", "provider_calls",
            "image_calls", "wechat_calls"}
    try:
        qualified = isinstance(value, dict) and 'qualification' in value
        if not isinstance(value, dict) or set(value) != keys | ({'qualification'} if qualified else set()):
            raise ValueError()
        dispatch = (value['source_commit'], value['source_tree'], DISPATCH_CONTRACT_SHA) if qualified else (DISPATCH_SOURCE, DISPATCH_TREE, DISPATCH_CONTRACT_SHA)
        if (value["contract"] != CONTRACT or value["environment"] != "test"
                or (value["dispatch_source"], value["dispatch_tree"], value["dispatch_contract_sha256"])
                != dispatch
                or any(type(value[k]) is not int or value[k] != 0
                       for k in ("provider_calls", "image_calls", "wechat_calls"))):
            raise ValueError()
        for key, length in (("source_commit", 40), ("source_tree", 40), ("fixture_identity", 64)):
            if not re.fullmatch("[a-f0-9]{" + str(length) + "}", value[key]):
                raise ValueError()
        if str(uuid.UUID(value["user_id"])) != value["user_id"]:
            raise ValueError()
        if (not re.fullmatch("[a-zA-Z0-9_-]{1,128}", value["tenant_id"])
                or not re.fullmatch("[a-zA-Z0-9_-]{1,128}", value["authority_id"])
                or value["user_email"] != "integrated-productization-admin-v1@example.invalid"
                or value["mcp_url"] != MCP_URL):
            raise ValueError()
        # V1 external writes remain disabled. A negative CREATE_DRAFT probe is
        # allowed to reach the unchanged credential/permission gate, not execute.
        if value["actions"] != ["wechat-html-draft:PREPARE", "wechat-html-draft:CREATE_DRAFT"]:
            raise ValueError()
        if qualified:
            from app.skill_only_test_qualification import validate
            validate(value)
    except (ValueError, TypeError, KeyError, PermissionError):
        raise ControlledActionError(AUTH_BLOCKED) from None
    return value


def load_approval():
    """Fixed native Test control location; never read credentials or .env."""
    try:
        if os.name != "posix":
            raise ValueError()
        for path in (APPROVAL_PATH, *APPROVAL_PATH.parents):
            info = path.lstat()
            if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                    or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
                raise ValueError()
        info = APPROVAL_PATH.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & stat.S_IWUSR:
            raise ValueError()
        raw = APPROVAL_PATH.read_bytes()
        value = validate_approval(json.loads(raw))
        if git_identity(PROJECT) != {"source_commit": value["source_commit"], "source_tree": value["source_tree"]}:
            raise ValueError()
        return value, _digest(raw)
    except Exception:
        raise ControlledActionError(AUTH_BLOCKED) from None


class PlatformSkillActionClient:
    """Only the existing MCP Skill tool, fixed loopback URL, no model client."""
    async def call(self, bearer, task_scope, arguments):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(MCP_URL, headers={
            "Authorization": "Bearer " + bearer,
            "X-Runtime-Execution-Scope": task_scope,
        }) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("skill_action_execute", arguments)


@dataclass(frozen=True)
class _Ticket:
    entry: object
    task_id: str
    request_json: str = field(repr=False)
    audit_json: str
    session_token: str = field(repr=False)


class ControlledSkillActionEntry:
    def __init__(self, tester, sessions, tokens):
        self.tester, self.sessions, self.tokens = tester, sessions, tokens
        # Internal dependencies only; production uses the fixed trusted loader
        # and real MCP transport. Offline fixtures replace these boundaries.
        self._approval_loader = load_approval
        self._client = PlatformSkillActionClient()

    def _authorize(self, session_token, tenant):
        if self.tester.resolver.settings.environment != "test":
            raise ControlledActionError(NOT_ALLOWED)
        if not callable(self.tester.isolation_guard):
            raise ControlledActionError(AUTH_BLOCKED)
        try:
            # This is the existing native, versioned Test authority guard. It
            # must verify current topology/DB/fixture/Source, never a no-op.
            attestation = self.tester.isolation_guard()
            approval, approval_sha = self._approval_loader()
            validate_approval(approval)
            # Old API/Worker code cannot safely recover a marked Task. Require
            # their exact new Source as well as the separately sealed MCP. A
            # legacy guard/no-op, stale receipt or mixed worker never authorizes
            # reservation. This is Test activation attestation, not deployment.
            expected = dict(status='PASS', environment='test', contract=CONTRACT,
                fixture_identity=approval['fixture_identity'],
                api_source=approval['source_commit'], api_tree=approval['source_tree'],
                worker_source=approval['source_commit'], worker_tree=approval['source_tree'],
                mcp_source=approval['dispatch_source'], mcp_tree=approval['dispatch_tree'])
            if (not isinstance(attestation, dict) or set(attestation) != set(expected) | {'observed_at'}
                    or any(attestation[key] != value for key, value in expected.items())
                    or type(attestation['observed_at']) not in (int, float)
                    or not 0 <= time.time() - attestation['observed_at'] <= 30):
                raise ValueError()
            principal = self.sessions.verify(session_token)
            with self.tester.resolver.store.connection() as conn:
                user = conn.execute("SELECT * FROM users WHERE id=? AND tenant_id=? AND account_status='enabled'",
                                    (principal.user_id, tenant)).fetchone()
                admins = conn.execute("SELECT user_id FROM platform_admins ORDER BY user_id").fetchall()
            # Qualified execution is a narrow native grant, not a platform role.
            # The provisioning admin can revoke its grant before acceptance.
            role_ok = (len(admins) == 0 or len(admins) == 1 and admins[0]['user_id'] == approval['user_id']) if 'qualification' in approval else (len(admins) == 1 and admins[0]['user_id'] == approval['user_id'])
            if (not user or not role_ok
                    or (principal.user_id, principal.tenant_id, principal.role)
                    != (approval["user_id"], approval["tenant_id"], "member")
                    or tenant != approval["tenant_id"] or user["email"] != approval["user_email"]
                    or user["role"] != "member" or not principal.auth_version
                    or principal.auth_version != self.sessions.credential_version(user["password_hash"])):
                raise ValueError()
            return approval, approval_sha, principal
        except ControlledActionError:
            raise
        except Exception:
            raise ControlledActionError(AUTH_BLOCKED) from None

    @staticmethod
    def _request(request):
        try:
            if not isinstance(request, dict) or set(request) != {"tenant_id", "agent_id", "skill_key", "revision", "action", "input"}:
                raise ValueError()
            if (not re.fullmatch("[a-zA-Z0-9_-]{1,128}", request["tenant_id"])
                    or str(uuid.UUID(request["agent_id"])) != request["agent_id"]
                    or str(uuid.UUID(request["revision"])) != request["revision"]
                    or request["skill_key"] != "wechat-html-draft"
                    or request["action"] not in {"PREPARE", "CREATE_DRAFT"}
                    or not isinstance(request["input"], dict)):
                raise ValueError()
            # Business input is still validated by the sealed MCP adapter.
            # Paths/options/runtime/code can never become execution selectors.
            if set(request["input"]) - {"title", "digest", "html", "cover_asset", "assets"}:
                raise ValueError()
            encoded = json.dumps(request, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode()) > 12 * 1024 * 1024:
                raise ValueError()
            return encoded
        except (ValueError, TypeError, KeyError, OverflowError):
            raise ControlledActionError("SKILL_INPUT_INVALID") from None

    async def run(self, session_token, request):
        # Environment gate precedes request validation/DB/Task mutation.
        if self.tester.resolver.settings.environment != "test":
            raise ControlledActionError(NOT_ALLOWED)
        encoded = self._request(request)
        approval, approval_sha, caller = self._authorize(session_token, request["tenant_id"])
        if request["skill_key"] + ":" + request["action"] not in approval["actions"]:
            raise ControlledActionError(AUTH_BLOCKED)
        if type(self._client) is not PlatformSkillActionClient:
            raise ControlledActionError(ZERO_PROVIDER)
        proof = None
        if 'qualification' in approval:
            from app.skill_only_test_qualification import authority
            try:
                proof = authority(approval, approval_sha, request)
            except PermissionError:
                raise ControlledActionError(AUTH_BLOCKED) from None
        # Ordinary published/enabled execution requirements are NOT relaxed.
        # Never invent Codex Runtime Test evidence or create a quality-test row.
        text = "请排版公众号文章" if request["action"] == "PREPARE" else "请创建公众号草稿"
        task = self.tester.product.create_task(request["tenant_id"], caller.user_id, request["agent_id"],
                                               text + TASK_MARKER, None, _controlled_action=True,
                                               **({'_controlled_qualification': proof} if proof is not None else {}))
        audit = dict(contract=CONTRACT, environment="test", tenant_id=task["tenant_id"],
            agent_id=task["agent_id"], skill_key=request["skill_key"], revision=request["revision"],
            action=request["action"], task_id=task["id"], caller_id=caller.user_id,
            authority_id=approval["authority_id"], approval_identity=approval_sha,
            fixture_identity=approval["fixture_identity"], controlled_source=approval["source_commit"],
            controlled_tree=approval["source_tree"], dispatch_source=approval['dispatch_source'], dispatch_tree=approval['dispatch_tree'],
            CONTROLLED_TEST_ACTION=True, provider_calls=0, image_calls=0, wechat_calls=0)
        if proof is not None:
            audit.update(eligibility_mode=proof.receipt()['eligibility_mode'],
                         qualification_identity=_digest(json.dumps(proof.receipt(),sort_keys=True).encode()))
        ticket = _Ticket(self, task["id"], encoded, json.dumps(audit), session_token)
        await self.tester.tasks.execute(task, _controlled_action=ticket)
        saved = self.tester.product.task_for_worker(task["id"])
        trace = self.tester.resolver.store.run_trace(saved["run_id"], saved["tenant_id"]) if saved.get("run_id") else None
        return dict(task_id=saved["id"], run_id=saved.get("run_id"), status=saved["status"],
                    error_code=saved.get("error_code"), provider_calls=0,
                    result=trace["payload"].get("skill_action_result") if trace else None)

    def check_ticket(self, ticket, task_id, context):
        if (type(ticket) is not _Ticket or ticket.entry is not self or ticket.task_id != task_id
                or not context or self.tester.resolver.settings.environment != "test"):
            raise ControlledActionError(NOT_ALLOWED)
        request = json.loads(ticket.request_json)
        approval, approval_sha, _ = self._authorize(ticket.session_token, context["tenant_id"])
        audit = json.loads(ticket.audit_json)
        if (approval_sha != audit["approval_identity"]
                or (context["tenant_id"], context["agent_id"]) != (request["tenant_id"], request["agent_id"])):
            raise ControlledActionError(AUTH_BLOCKED)
        from app.skill_only_test_qualification import is_qualified, authority, check_context
        if ('qualification' in approval) != is_qualified(context):
            raise ControlledActionError(AUTH_BLOCKED)
        if is_qualified(context):
            if 'qualification' not in approval:
                raise ControlledActionError(AUTH_BLOCKED)
            try:
                proof = authority(approval, approval_sha, request)
                with self.tester.resolver.store.connection() as conn:
                    receipt = check_context(conn, context, environment=self.tester.resolver.settings.environment)
                if receipt != proof.receipt():
                    raise PermissionError()
            except PermissionError:
                raise ControlledActionError(AUTH_BLOCKED) from None
        # All later binding/artifact/runtime/secret checks are authoritative MCP
        # gates. A normal chat cannot select this path using text or Task fields.
        return request, audit

    async def dispatch(self, ticket, task_id, context, run_id, conversation_id):
        request, audit = self.check_ticket(ticket, task_id, context)
        if type(self._client) is not PlatformSkillActionClient:
            raise ControlledActionError(ZERO_PROVIDER)
        p = profile(context, _controlled=True)
        bearer = self.tokens.issue(RuntimePrincipal(p.tenant_id, p.agent_id, p.id, p.tool_scopes,
            int(time.time()) + 180, p.execution_context_id, p.instance_id))
        task_scope = self.tokens.issue_task_scope(p.tenant_id, task_id)
        arguments = dict(skill_key=request["skill_key"], revision=request["revision"],
                         action=request["action"], article=request["input"])
        try:
            result = await self._client.call(bearer, task_scope, arguments)
            output = result.structuredContent
            allowed = {"status", "action", "artifact_refs", "summary", "verification", "receipt_ref", "error_code"}
            if (not isinstance(output, dict) or set(output) - allowed
                    or not allowed - {"error_code"} <= set(output)
                    or output["status"] not in {"completed", "failed"}
                    or output["action"] != request["action"]
                    or result.isError and output["status"] == "completed"):
                raise ValueError()
            # Do not persist a hostile transport payload, stdout or raw error.
            json.dumps(output, allow_nan=False)
            if output["status"] == "completed" and not re.fullmatch("skill-receipt:[a-f0-9-]{36}", output["receipt_ref"] or ""):
                raise ValueError()
            with self.tester.resolver.store.connection() as conn:
                events = conn.execute("SELECT payload FROM execution_events WHERE conversation_id=? AND event_type='skill.execution' ORDER BY id DESC", (conversation_id,)).fetchall()
            receipt = next((json.loads(row["payload"]) for row in events
                            if json.loads(row["payload"]).get("task_id") == task_id
                            and json.loads(row["payload"]).get("run_id") == run_id), None)
            if (not receipt or receipt.get("status") != output["status"]
                    or receipt.get('tenant_id') != request['tenant_id']
                    or receipt.get('agent_id') != request['agent_id']
                    or receipt.get('action') != request['action']
                    or receipt.get('revision', request['revision']) != request['revision']
                    or receipt.get('artifact_refs') != output['artifact_refs']
                    or output['status'] == 'completed' and not re.fullmatch('[a-f0-9]{64}', receipt.get('runtime_identity') or '')
                    or receipt.get("dispatch_source") != {"source_commit": audit['dispatch_source'], "source_tree": audit['dispatch_tree']}
                    or output["receipt_ref"] != "skill-receipt:" + receipt["id"]):
                raise ValueError()
            if output['status'] == 'completed':
                # Revalidate the normalized contract + contained artifact hash
                # against the shared Task workspace, never trust HTTP text.
                if (request['action'] != 'PREPARE' or output['verification'].get('offline') is not True
                        or type(output['verification'].get('wechat_calls')) is not int
                        or output['verification']['wechat_calls'] != 0):
                    raise ValueError()
                relative = 'tasks/' + task_id + '/' + receipt['id']
                workspace = self.tester.resolver.settings.data_dir / 'runtime' / p.tenant_id / p.agent_id / p.id / 'workspace' / relative
                SkillActionDispatcher._result({k: output[k] for k in ('artifact_refs', 'summary', 'verification')}, workspace, relative)
            elif (output.get('error_code') not in CODES or output['summary'] != output['error_code']
                  or output['artifact_refs'] or output['verification']):
                raise ValueError()
            audit.update(run_id=run_id, runtime_identity=receipt.get("runtime_identity"),
                         result=output, status=output["status"])
            self.tester.resolver.store.log_event(conversation_id, "skill.controlled_action", audit)
            return output, audit
        except Exception:
            raise ControlledActionError("SKILL_EXECUTION_FAILED") from None


def ticket_entry(ticket):
    if type(ticket) is not _Ticket or type(ticket.entry) is not ControlledSkillActionEntry:
        raise ControlledActionError(NOT_ALLOWED)
    return ticket.entry
