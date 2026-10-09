"""Non-authorizing recognition of the exact existing 06 historical evidence.

No active approval lookup, execution, DB writer or new permission registry.
The immutable readonly evidence packet is not a lease or Runtime quality PASS.
Full native rows/files must still match the ORIGINAL identities observed by 06.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat

from scripts import exact_test_admin_lifecycle as a
from scripts import wechat_runtime_test_lifecycle_guard as g

MODE = 'SKILL_ONLY_TEST_QUALIFIED'
FIXTURE_SHA = '9b1eaa6ce86c9b0221eb2da36ce3566fad97e964f0887092717bc0c2159f2daf'
FIXTURE_PATH = a.ROOT/'historical-prepare.fixture.v1.json'


def instant(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    g.need(stamp.tzinfo is not None, 'HISTORICAL_PREPARE_TIME_REPRESENTATION')
    return stamp.astimezone(timezone.utc)


def same_metadata(actual, expected, code):
    g.need(type(actual) is dict and set(expected) <= set(actual), code)
    for key, value in expected.items():
        if key in {'created_at', 'started_at', 'completed_at'}:
            g.need(instant(actual[key]) == instant(value), code)
        else:
            g.need(g.digest(actual[key]) == g.digest(value), code)


def observed_file(record):
    """Fixed packet-selected nonsensitive receipt/approval, never request paths."""
    path = Path(record['path'])
    for item in (path, *path.parents):
        info = item.lstat()
        g.need(not stat.S_ISLNK(info.st_mode) and info.st_uid in (0, 1000)
            and not info.st_mode & 0o022, 'HISTORICAL_PREPARE_FILE_TRUST')
    info = path.lstat()
    g.need(stat.S_ISREG(info.st_mode) and (info.st_uid, info.st_gid, oct(stat.S_IMODE(info.st_mode)))
        == (record['uid'], record['gid'], record['mode']) and info.st_size <= 131072,
        'HISTORICAL_PREPARE_FILE_IDENTITY')
    g.need(os.name == 'posix', 'HISTORICAL_PREPARE_NATIVE_FILE_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        g.need((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino), 'HISTORICAL_PREPARE_FILE_RACE')
        raw = stream.read(131073)
    g.need(len(raw) <= 131072, 'HISTORICAL_PREPARE_FILE_LIMIT')
    g.need(hashlib.sha256(raw).hexdigest() == record['sha256'], 'HISTORICAL_PREPARE_FILE_HASH')
    return json.loads(raw)


class HistoricalPrepare:
    def __init__(self, raw):
        g.need(hashlib.sha256(raw).hexdigest() == FIXTURE_SHA, 'HISTORICAL_PREPARE_FIXTURE_PIN')
        self.packet = json.loads(raw)
        f = self.packet
        g.need(f['fixture_contract'] == 'HISTORICAL_PREPARE_NATIVE_AUTHORIZATION_READONLY_FIXTURE_V1'
            and f['evidence_only'] is True and f['grants_execution_authority'] is False
            and f['current_application'] == {'source': a.BASE_SOURCE, 'tree': a.BASE_TREE},
            'HISTORICAL_PREPARE_EVIDENCE_ONLY')

    @classmethod
    def load(cls):
        from app.test_tenant_seeding import native_json
        packet, sha = native_json(FIXTURE_PATH)
        g.need(sha == FIXTURE_SHA, 'HISTORICAL_PREPARE_FIXTURE_PIN')
        # native_json has checked ownership/no-symlink/read-only bytes. Preserve
        # its parsed object; no reserialization pretending to be original bytes.
        value = object.__new__(cls); value.packet = packet
        g.need(packet['evidence_only'] is True and packet['grants_execution_authority'] is False,
            'HISTORICAL_PREPARE_EVIDENCE_ONLY')
        return value

    def metadata(self, data, context, task):
        """Exact redacted Fixture replay; DOES NOT qualify full native hashes.

        Native verify() below additionally checks every supplied full-row/file
        original digest. Metadata replay never returns an execution capability.
        """
        f = self.packet; association = f['unique_association']; q = f['qualification_receipt']
        g.need((context['id'], task['id'], task.get('run_id')) ==
            (association['context_id'], association['task_id'], association['run_id']),
            'HISTORICAL_PREPARE_EXACT_ASSOCIATION')
        same_metadata(context, f['execution_context_metadata'], 'HISTORICAL_PREPARE_CONTEXT_IDENTITY')
        same_metadata(task, f['task'], 'HISTORICAL_PREPARE_TASK_IDENTITY')
        g.need(task['status'] == 'completed', 'HISTORICAL_PREPARE_NOT_TERMINAL')
        policy = g.obj(context['tool_policy_snapshot'])
        same_metadata(policy, f['tool_policy_metadata'], 'HISTORICAL_PREPARE_POLICY_IDENTITY')
        receipt = policy.get('qualification_receipt')
        g.need(receipt == q and g.digest(receipt) == f['qualification_receipt_canonical_sha256']
            and hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
            == f['qualification_audit_identity_sha256'], 'HISTORICAL_PREPARE_QUALIFICATION_HASH')
        g.need(q['action'] == q['request_action'] == 'PREPARE' and q['negative_probe'] is False
            and q['environment'] == 'test' and q['source_commit'] == a.BASE_SOURCE
            and q['source_tree'] == a.BASE_TREE and q['eligibility_mode'] == MODE
            and policy['runtime_test'] is False and policy['model_execution_disabled'] is True,
            'HISTORICAL_PREPARE_NOT_EXECUTION_AUTHORITY')
        g.need([r for r in data['task_agent_contexts'] if r['context_id'] == context['id']
            or r['task_id'] == task['id']] == f['task_context_mapping'], 'HISTORICAL_PREPARE_MAPPING')
        run = g.one(data, 'run_traces', 'run_id', task['run_id'])
        same_metadata(run, f['run'], 'HISTORICAL_PREPARE_RUN_IDENTITY')
        payload = g.obj(run['payload'])
        same_metadata(payload, f['run_metadata'], 'HISTORICAL_PREPARE_RUN_METADATA')
        g.need(run['status'] == 'completed' and not run.get('codex_thread_id'), 'HISTORICAL_PREPARE_MODEL_EXECUTION')
        for expected in f['audits']:
            event = g.one(data, 'execution_events', 'id', expected['id'])
            same_metadata(event, {k: expected[k] for k in ('id', 'conversation_id', 'event_type', 'created_at')},
                'HISTORICAL_PREPARE_AUDIT_IDENTITY')
            same_metadata(g.obj(event['payload']), expected['metadata'], 'HISTORICAL_PREPARE_AUDIT_METADATA')
        execution = next(r for r in f['audits'] if r['event_type'] == 'skill.execution')['metadata']
        controlled = next(r for r in f['audits'] if r['event_type'] == 'skill.controlled_action')['metadata']
        g.need(execution['status'] == controlled['status'] == 'completed' and execution['exit_status'] == 0
            and execution['id'] == f['workspace_receipt']['metadata']['id']
            and controlled['approval_identity'] == q['approval_identity'], 'HISTORICAL_PREPARE_RECEIPT_JOIN')
        # Only filesystem ordering is available. It is NEVER a lease proof.
        g.need(f['temporal_validity']['explicit_lease_window'] == 'EVIDENCE_NOT_FOUND'
            and f['temporal_validity']['independent_provision_scope']['is_authority_for_this_prepare'] is False,
            'HISTORICAL_PREPARE_TEMPORAL_EVIDENCE_BOUNDARY')
        return {'classification': 'HISTORICAL_CONTROLLED_PREPARE',
            'task_id': task['id'], 'run_id': run['run_id'],
            'historical_prepare_evidence': 'VERIFIED',
            'historical_approval_temporal_validity': 'NOT_PROVEN',
            'execution_authorized': False, 'publish_eligible': False}

    def verify(self, data, context, task):
        """Strict native path: no redacted-hash exemption or active grant."""
        result = self.metadata(data, context, task); f = self.packet
        g.need(g.digest(context) == f['execution_context_original_canonical_sha256']
            and g.digest(g.obj(context['tool_policy_snapshot'])) == f['tool_policy_original_canonical_sha256'],
            'HISTORICAL_PREPARE_ORIGINAL_CONTEXT_HASH')
        for expected in f['audits']:
            event = g.one(data, 'execution_events', 'id', expected['id'])
            g.need(isinstance(event['payload'], str)
                and hashlib.sha256(event['payload'].encode()).hexdigest() == expected['raw_payload_sha256']
                and g.digest(g.obj(event['payload'])) == expected['canonical_payload_sha256'],
                'HISTORICAL_PREPARE_ORIGINAL_AUDIT_HASH')
        run = g.one(data, 'run_traces', 'run_id', task['run_id']); payload = g.obj(run['payload'])
        expected_controlled = next(r for r in f['audits'] if r['event_type'] == 'skill.controlled_action')
        g.need(g.digest(payload.get('controlled_action_audit')) == expected_controlled['canonical_payload_sha256'],
            'HISTORICAL_PREPARE_RUN_AUDIT_JOIN')
        approval_record = f['authority_files'][0]  # Versioned immutable d8a, NOT active lookup.
        approval = observed_file(approval_record)
        from app.controlled_skill_action import validate_approval, TASK_MARKER
        validate_approval(approval)
        same_metadata(approval, approval_record['metadata'], 'HISTORICAL_PREPARE_APPROVAL_IDENTITY')
        g.need(approval['qualification'] == approval_record['qualification'], 'HISTORICAL_PREPARE_APPROVAL_QUALIFICATION')
        g.need(task.get('input_text') == '请排版公众号文章'+TASK_MARKER,
            'HISTORICAL_PREPARE_FORMAL_TASK_MARKER')
        disk = observed_file(f['workspace_receipt'])
        expected_execution = next(r for r in f['audits'] if r['event_type'] == 'skill.execution')
        g.need(g.digest(disk) == expected_execution['canonical_payload_sha256'], 'HISTORICAL_PREPARE_DISK_RECEIPT')
        return result
