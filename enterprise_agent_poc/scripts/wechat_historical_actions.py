"""Finite historical result classification anchored to existing aggregate proof.

No new Witness, active grant, permission registry, writer, or execution path.
Two observed types only. Native verification requires ORIGINAL bytes as well
as the read-only 06 inventory; metadata replay is never full Native status.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from scripts import exact_test_admin_lifecycle as a
from scripts import wechat_runtime_test_lifecycle_guard as g
from scripts import wechat_historical_prepare as h

AUDIT_SHA = '1fd7e443b4ada1861ea1f025a22dca7d1f7b7cb7796b829964542008ff5f9500'
AUDIT_PATH = a.ROOT/'historical-action.audit.v1.json'
PREPARE = 'HISTORICAL_CONTROLLED_PREPARE_COMPLETED'
DENIED = 'HISTORICAL_CREATE_DRAFT_PERMISSION_DENIED'
EVENTS = {'wechat.action.permission', 'skill.execution', 'skill.controlled_action', 'skill.controlled_action_failed'}


def acceptance_pairs(value):
    """Same nested association semantics as 06; no Task IDs in code."""
    found = []
    if isinstance(value, dict):
        if isinstance(value.get('task_id'), str) and isinstance(value.get('run_id'), str):
            found.append(value)
        for key, item in value.items():
            if key not in {'health', 'services', 'module_map', 'files', 'fixed_row_hashes',
                           'prepared', 'artifacts', 'code_sha256', 'environment'}:
                found.extend(acceptance_pairs(item))
    elif isinstance(value, list):
        for item in value: found.extend(acceptance_pairs(item))
    return found


def read_original(path, sha, *, uid=0, gid=0, mode='0o444'):
    # Optional independently approved BYTE-IDENTICAL staging, not redacted
    # reconstruction or chmod of old root-only directories. Bad staging fails.
    stage = a.ROOT/'historical-action-originals'/(sha+'.json')
    if stage.exists() or stage.is_symlink():
        return h.observed_file(dict(path=str(stage), sha256=sha, uid=0, gid=0, mode='0o444'))
    return h.observed_file(dict(path=str(path), sha256=sha, uid=uid, gid=gid, mode=mode))


class HistoricalActions:
    def __init__(self, raw):
        g.need(hashlib.sha256(raw).hexdigest() == AUDIT_SHA, 'HISTORICAL_ACTION_AUDIT_PIN')
        self.packet = json.loads(raw); self._contract()

    @classmethod
    def load(cls):
        from app.test_tenant_seeding import native_json
        packet, sha = native_json(AUDIT_PATH)
        g.need(sha == AUDIT_SHA, 'HISTORICAL_ACTION_AUDIT_PIN')
        instance = object.__new__(cls); instance.packet = packet; instance._contract()
        return instance

    def _contract(self):
        f = self.packet; proposal = f['proposed_classification_contract']
        g.need(f['status'] == 'WECHAT_HISTORICAL_ACTION_CLASSIFICATION_AUDITED'
            and f['source'] == a.BASE_SOURCE and f['tree'] == a.BASE_TREE
            and proposal['grants_execution_authority'] is False and proposal['publish_eligible'] is False
            and proposal['no_per_task_witness'] is True and f['no_new_witnesses'] == 0
            and len(proposal['static_conflicts_identified_together']) == 5
            and len(f['records']) == 4 and f['unclassified_task_ids'] == []
            and f['orphan_run_ids'] == f['unmapped_context_ids'] == f['unassociated_audit_event_ids'] == []
            and len(f['existing_acceptance_anchor_confirmation']['existing_acceptance_anchors']) == 2,
            'HISTORICAL_ACTION_AUDIT_CONTRACT')

    def record(self, context, task):
        matches = [r for r in self.packet['records'] if
            (r['context']['id'], r['task']['id'], r['run']['run_id'])
            == (context['id'], task['id'], task.get('run_id'))]
        g.need(len(matches) == 1, 'HISTORICAL_ACTION_UNANCHORED_ASSOCIATION')
        return matches[0]

    def anchor(self, task, run):
        anchors = self.packet['existing_acceptance_anchor_confirmation']['existing_acceptance_anchors']
        matches = [r for r in anchors if any((p['task_id'], p['run_id']) == (task['id'], run['run_id'])
            for p in r['task_run_identities'])]
        g.need(len(matches) == 1, 'HISTORICAL_ACTION_AGGREGATE_PROVENANCE')
        return matches[0]

    def metadata(self, data, context, task):
        """Semantic replay of all four 06 objects, not original-byte proof."""
        f = self.packet; r = self.record(context, task); exact = f['scope']
        h.same_metadata(context, r['context'], 'HISTORICAL_ACTION_CONTEXT')
        h.same_metadata(task, r['task'], 'HISTORICAL_ACTION_TASK')
        g.need(task['tenant_id'] == context['tenant_id'] == exact['tenant_id']
            and task['agent_id'] == context['agent_id'] == exact['agent_id']
            and context['agent_template_version_id'] == exact['revision_id'], 'HISTORICAL_ACTION_SCOPE')
        maps = [m for m in data['task_agent_contexts'] if m['task_id'] == task['id'] or m['context_id'] == context['id']]
        g.need(maps == [dict(task_id=task['id'], context_id=context['id'])], 'HISTORICAL_ACTION_MAPPING')
        g.need(len([t for t in data['tasks'] if t.get('run_id') == task['run_id']]) == 1, 'HISTORICAL_ACTION_RUN_REUSE')
        run = g.one(data, 'run_traces', 'run_id', task['run_id'])
        h.same_metadata(run, r['run'], 'HISTORICAL_ACTION_RUN')
        payload = g.obj(run['payload']); h.same_metadata(payload, r['run_metadata'], 'HISTORICAL_ACTION_RUN_METADATA')
        self.anchor(task, run)
        policy = g.obj(context['tool_policy_snapshot']); q = policy.get('qualification_receipt')
        g.need(type(q) is dict and g.digest(q) == r['qualification_receipt_sha256']
            and hashlib.sha256(json.dumps(q, sort_keys=True).encode()).hexdigest() == r['qualification_identity'],
            'HISTORICAL_ACTION_QUALIFICATION_HASH')
        from app import skill_only_test_qualification as qualification
        g.need(policy.get('eligibility_mode') == qualification.MODE and policy.get('runtime_test') is False
            and policy.get('model_execution_disabled') is True and policy.get('required_tools') == []
            and set(policy.get('scopes', [])) == qualification.SCOPES
            and len(policy.get('bindings', [])) == len(qualification.TOOLS)
            and {b['tool_capability_id'] for b in policy.get('bindings', [])} == qualification.TOOLS
            and all(b['invocation_requirement'] == 'optional' for b in policy['bindings']), 'HISTORICAL_ACTION_POLICY')
        g.need(q['environment'] == 'test' and q['action'] == 'PREPARE'
            and q['tenant_id'] == task['tenant_id'] and q['caller_id'] == task['user_id']
            and q['agent_id'] == task['agent_id'] and q['agent_revision_id'] == exact['revision_id']
            and q['revision'] == exact['skill_revision_id'] and q['skill_key'] == 'wechat-html-draft'
            and q['artifact_sha256'] == qualification.ARTIFACT and q['runtime_lock_sha256'] == qualification.LOCK
            and q['configuration_fingerprint'] == context['configuration_fingerprint']
            and q['source_commit'] in f['historical_source_code_evidence'], 'HISTORICAL_ACTION_QUALIFIED_IDENTITY')
        refs = policy.get('skill_refs', [])
        g.need(len(refs) == 1 and (refs[0]['id'], refs[0]['skill_id'], refs[0]['slug'], refs[0]['checksum'], refs[0]['version'])
            == (exact['skill_revision_id'], exact['skill_id'], 'wechat-html-draft', qualification.ARTIFACT, '1.0.0'),
            'HISTORICAL_ACTION_SKILL_REVISION')
        bodies = {}
        related = [e for e in data['execution_events'] if e['event_type'] in EVENTS
            and g.obj(e['payload']).get('task_id') == task['id']]
        g.need({e['id'] for e in related} == {e['id'] for e in r['audits']}
            and len(related) == len(r['audits']), 'HISTORICAL_ACTION_AUDIT_SET')
        for expected in r['audits']:
            event = g.one(data, 'execution_events', 'id', expected['id'])
            h.same_metadata(event, {k: expected[k] for k in ('id', 'event_type', 'conversation_id', 'created_at')},
                'HISTORICAL_ACTION_AUDIT_ROW')
            body = g.obj(event['payload']); h.same_metadata(body, expected['metadata'], 'HISTORICAL_ACTION_AUDIT_METADATA')
            g.need(event['event_type'] not in bodies, 'HISTORICAL_ACTION_DUPLICATE_AUDIT')
            bodies[event['event_type']] = body
        p, e, c = (bodies[k] for k in ('wechat.action.permission', 'skill.execution', 'skill.controlled_action'))
        action = q['request_action']
        g.need(p['action'] == e['action'] == c['action'] == action == r['request_action']
            and q['approval_identity'] == c['approval_identity'] == r['approval_identity']
            and q['source_commit'] == c['controlled_source'] == c['dispatch_source']
            and q['source_tree'] == c['controlled_tree'] == c['dispatch_tree']
            and e['dispatch_source'] == {'source_commit': q['source_commit'], 'source_tree': q['source_tree']}
            and c['caller_id'] == task['user_id'] and c['CONTROLLED_TEST_ACTION'] is True
            and p['agent_revision_id'] == exact['revision_id'] and p['skill_revision_id'] == exact['skill_revision_id'],
            'HISTORICAL_ACTION_AUTHORIZATION_JOIN')
        g.need(all(type(q[k]) is int and q[k] == c[k] == payload[k] == 0
            for k in ('provider_calls', 'image_calls', 'wechat_calls')),
            'HISTORICAL_ACTION_EXTERNAL_CALLS')
        g.need(payload['execution_kind'] == 'controlled_skill_action' and payload['execution_context_id'] == context['id']
            and all(payload.get(k) is None for k in ('model_provider', 'model', 'reasoning_effort', 'codex_thread_id')),
            'HISTORICAL_ACTION_MODEL_EXECUTION')
        if action == 'PREPARE':
            g.need(q['negative_probe'] is False and task['status'] == run['status'] == e['status'] == c['status'] == 'completed'
                and type(e['exit_status']) is int and e['exit_status'] == 0 and isinstance(e.get('runtime_identity'), str)
                and e['runtime_identity'] == c.get('runtime_identity') and payload['runtime_completed'] is True
                and p['execution_status'] == 'PERMISSION_RESOLVED_NOT_EXECUTED' and p['network_targets'] == []
                and 'skill.controlled_action_failed' not in bodies, 'HISTORICAL_ACTION_PREPARE_COMPLETION')
            classification = PREPARE
        elif action == 'CREATE_DRAFT':
            failed = bodies.get('skill.controlled_action_failed')
            g.need(type(failed) is dict and q['negative_probe'] is True
                and task['status'] == run['status'] == e['status'] == c['status'] == 'failed'
                and p['execution_status'] == 'BLOCKED_NOT_EXECUTED' and p['network_targets'] == []
                and p.get('draft_media_id') is None and e.get('failure_phase') == 'action_permission'
                and payload['runtime_completed'] is False, 'HISTORICAL_ACTION_NOT_PERMISSION_DENIAL')
            g.need(all(e.get(k) is None for k in ('runtime_identity', 'workspace_identity', 'exit_status'))
                and e.get('artifact_refs', []) == [] and c.get('runtime_identity') is None
                and failed.get('runtime_identity') is None, 'HISTORICAL_ACTION_EXECUTION_OCCURRED')
            g.need(task['error_code'] == payload['error'] == e['error_code'] == failed['error_code']
                == 'SKILL_ACTION_NOT_ALLOWED', 'HISTORICAL_ACTION_DENIAL_ERROR')
            classification = DENIED
        else: raise g.Blocked('HISTORICAL_ACTION_UNKNOWN_TYPE')
        g.need(r['historical_approval_temporal_validity'] == 'NOT_PROVEN', 'HISTORICAL_ACTION_FALSE_TEMPORAL_PROOF')
        return dict(classification=classification, task_id=task['id'], run_id=run['run_id'],
            historical_approval_temporal_validity='NOT_PROVEN', execution_authorized=False, publish_eligible=False)

    def verify(self, data, context, task):
        """Original full-byte native verification; no metadata fallback."""
        result = self.metadata(data, context, task); r = self.record(context, task)
        run = g.one(data, 'run_traces', 'run_id', task['run_id']); policy = g.obj(context['tool_policy_snapshot'])
        for name, row in (('task', task), ('run', run), ('context', context), ('tool_policy', policy)):
            g.need(g.digest(row) == r['original_hashes'][name], 'HISTORICAL_ACTION_ORIGINAL_HASH:'+name)
        bodies = {}
        for spec in r['audits']:
            event = g.one(data, 'execution_events', 'id', spec['id']); body = g.obj(event['payload'])
            g.need(isinstance(event['payload'], str)
                and hashlib.sha256(event['payload'].encode()).hexdigest() == spec['raw_payload_sha256']
                and g.digest(body) == spec['canonical_payload_sha256'], 'HISTORICAL_ACTION_ORIGINAL_AUDIT_HASH')
            bodies[event['event_type']] = body
        p, e, c = (bodies[k] for k in ('wechat.action.permission', 'skill.execution', 'skill.controlled_action'))
        payload = g.obj(run['payload']); q = policy['qualification_receipt']
        g.need(payload['controlled_action_audit'] == c, 'HISTORICAL_ACTION_RUN_AUDIT')
        approval = read_original(r['approval_matching_immutable_paths'][0], r['approval_identity'])
        from app.controlled_skill_action import validate_approval, TASK_MARKER
        from app.skill_only_test_qualification import Authority
        from app.agent_productization import canonical
        validate_approval(approval)
        g.need(Authority(canonical(approval), r['approval_identity'], q['request_action']).receipt() == q,
            'HISTORICAL_ACTION_ARCHIVED_APPROVAL')
        anchor_spec = self.anchor(task, run); anchor = read_original(anchor_spec['path'], anchor_spec['sha256'])
        pairs = [v for v in acceptance_pairs(anchor) if (v['task_id'], v['run_id']) == (task['id'], run['run_id'])]
        g.need(bool(pairs) and any(v.get('status', v.get('task_status')) == task['status']
            and v.get('run_status', run['status']) == run['status'] for v in pairs),
            'HISTORICAL_ACTION_ORIGINAL_ACCEPTANCE')
        evidence = self.packet['historical_source_code_evidence'][q['source_commit']]
        g.need(evidence['create_draft_registration_disabled'] is True
            and evidence['permission_before_runtime_and_child'] is True, 'HISTORICAL_ACTION_SOURCE_SEMANTICS')
        for relative, expected in evidence['code_sha256'].items():
            raw = subprocess.run(['git', '--no-optional-locks', '-C', str(a.PROJECT.parent), 'show',
                q['source_commit']+':enterprise_agent_poc/'+relative], capture_output=True, check=True, timeout=10).stdout
            g.need(hashlib.sha256(raw).hexdigest() == expected, 'HISTORICAL_ACTION_SOURCE_CODE_PIN')
        marker = '请排版公众号文章' if q['request_action'] == 'PREPARE' else '请创建公众号草稿'
        g.need(task['input_text'] == marker+TASK_MARKER, 'HISTORICAL_ACTION_FORMAL_MARKER')
        names = (context['tenant_id'], context['agent_id'], context['runtime_profile_id'], task['id'], e['id'])
        g.need(all(re.fullmatch('[A-Za-z0-9_-]{1,128}', name) for name in names), 'HISTORICAL_ACTION_WORKSPACE_IDENTITY')
        workspace = Path('/opt/enterprise-agent-workbench-test/data/application/runtime')/names[0]/names[1]/names[2]/'workspace/tasks'/names[3]/names[4]
        g.need(os.name == 'posix', 'HISTORICAL_ACTION_NATIVE_FILES_REQUIRED')
        for path in (workspace, *workspace.parents):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                g.need(not stat.S_ISLNK(info.st_mode) and info.st_uid in (0, 1000)
                    and not info.st_mode & 0o022, 'HISTORICAL_ACTION_WORKSPACE_TRUST')
        output = payload['skill_action_result']
        g.need(output == c['result'] and output['receipt_ref'] == 'skill-receipt:'+e['id'], 'HISTORICAL_ACTION_RESULT_JOIN')
        if result['classification'] == PREPARE:
            disk = read_original(workspace/'receipt.json', r['disk_receipt']['sha256'], uid=1000, gid=1000, mode='0o600')
            g.need(disk == e and e['artifact_refs'] == output['artifact_refs'] and output['status'] == 'completed'
                and output['action'] == 'PREPARE', 'HISTORICAL_ACTION_SUCCESS_RECEIPT')
        else:
            failed = bodies['skill.controlled_action_failed']
            g.need(not workspace.exists() and not workspace.is_symlink(), 'HISTORICAL_ACTION_DENIED_WORKSPACE')
            g.need(failed['result'] == output and output['status'] == 'failed' and output['action'] == 'CREATE_DRAFT'
                and output['error_code'] == output['summary'] == 'SKILL_ACTION_NOT_ALLOWED'
                and output['artifact_refs'] == [] and output['verification'] == {}
                and failed['receipt_ref'] == payload['controlled_receipt_ref'] == 'controlled-action-receipt:'+run['run_id']
                and p.get('draft_media_id') is None, 'HISTORICAL_ACTION_DENIAL_RESULT')
            for key in ('task_id', 'run_id', 'caller_id', 'tenant_id', 'agent_id', 'revision',
                        'approval_identity', 'qualification_identity'):
                g.need(failed[key] == c[key], 'HISTORICAL_ACTION_FAILURE_PROPAGATION')
        return result
