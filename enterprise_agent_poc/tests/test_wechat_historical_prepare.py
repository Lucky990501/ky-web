"""Replay the ACTUAL 06 redacted failing object, never invent missing originals.

Metadata/classification PASS is distinct from strict full Native status PASS.
Full original context/policy/audit hashes cannot be rebuilt from this packet.
"""
import ast
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_skill_dispatch  # Offline Settings/DOCX stub; no .env/credential load.
from scripts import wechat_historical_prepare as h
from scripts import wechat_runtime_native_successor as n
from scripts import wechat_runtime_test_lifecycle_guard as g


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.history = h.HistoricalPrepare(self.raw_fixture)
        self.f = self.history.packet
        self.context = copy.deepcopy(self.f['execution_context_metadata'])
        self.policy = copy.deepcopy(self.f['tool_policy_metadata'])
        self.policy['qualification_receipt'] = copy.deepcopy(self.f['qualification_receipt'])
        self.context['tool_policy_snapshot'] = json.dumps(self.policy, ensure_ascii=False)
        self.task = copy.deepcopy(self.f['task'])
        self.run = dict(self.f['run'], payload=json.dumps(self.f['run_metadata']))
        self.events = [{**{k: r[k] for k in ('id', 'event_type', 'conversation_id', 'created_at')},
                        'payload': json.dumps(r['metadata'])} for r in self.f['audits']]
        self.data = dict(agent_execution_contexts=[self.context], tasks=[self.task], run_traces=[self.run],
            task_agent_contexts=copy.deepcopy(self.f['task_context_mapping']), execution_events=self.events,
            conversation_agent_contexts=[dict(conversation_id=self.task['conversation_id'], context_id=self.context['id'])])

    def metadata(self): return self.history.metadata(self.data, self.context, self.task)

    def test_01_exact_06_object_classified_without_execution_authority(self):
        result = self.metadata()
        self.assertEqual(result['task_id'], 'ded92a51-7d08-4812-afcd-f35ac3781cf6')
        self.assertEqual(result['run_id'], '094eb7e8-f8d4-4bd6-bde5-4c81a421e1fa')
        self.assertEqual(result['classification'], 'HISTORICAL_CONTROLLED_PREPARE')
        self.assertEqual(result['historical_prepare_evidence'], 'VERIFIED')
        self.assertEqual(result['historical_approval_temporal_validity'], 'NOT_PROVEN')
        self.assertFalse(result['execution_authorized']); self.assertFalse(result['publish_eligible'])

    def test_02_new_prepare_same_old_approval_not_admitted(self):
        self.task['id'] = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
        with self.assertRaisesRegex(g.Blocked, 'EXACT_ASSOCIATION'): self.metadata()

    def test_03_qualified_running_task_not_history(self):
        self.task['status'] = 'running'
        with self.assertRaises(g.Blocked): self.metadata()

    def test_04_wrong_tenant_rejected(self):
        self.task['tenant_id'] = 'tenant-b'
        with self.assertRaisesRegex(g.Blocked, 'TASK_IDENTITY'): self.metadata()

    def test_05_wrong_agent_rejected(self):
        self.context['agent_id'] = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
        with self.assertRaisesRegex(g.Blocked, 'CONTEXT_IDENTITY'): self.metadata()

    def test_06_wrong_agent_revision_rejected(self):
        self.context['agent_template_version_id'] = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
        with self.assertRaisesRegex(g.Blocked, 'CONTEXT_IDENTITY'): self.metadata()

    def test_07_wrong_skill_receipt_rejected(self):
        self.policy['qualification_receipt']['revision'] = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
        self.context['tool_policy_snapshot'] = json.dumps(self.policy)
        with self.assertRaisesRegex(g.Blocked, 'QUALIFICATION_HASH'): self.metadata()

    def test_08_forged_approval_hash_rejected(self):
        self.policy['qualification_receipt']['approval_identity'] = '0'*64
        self.context['tool_policy_snapshot'] = json.dumps(self.policy)
        with self.assertRaisesRegex(g.Blocked, 'QUALIFICATION_HASH'): self.metadata()

    def test_09_forged_execution_receipt_rejected(self):
        event = next(r for r in self.events if r['event_type'] == 'skill.execution')
        payload = json.loads(event['payload']); payload['id'] = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
        event['payload'] = json.dumps(payload)
        with self.assertRaisesRegex(g.Blocked, 'AUDIT_METADATA'): self.metadata()

    def test_10_cross_user_rejected(self):
        self.task['user_id'] = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
        with self.assertRaisesRegex(g.Blocked, 'TASK_IDENTITY'): self.metadata()

    def test_11_extra_mapping_cannot_reuse_history(self):
        self.data['task_agent_contexts'].append(dict(task_id='new', context_id=self.context['id']))
        with self.assertRaisesRegex(g.Blocked, 'MAPPING'): self.metadata()

    def test_12_missing_audit_rejected(self):
        self.events.pop()
        with self.assertRaises(g.Blocked): self.metadata()

    def test_13_revoked_provision_scope_is_not_prepare_lease(self):
        scope = self.f['temporal_validity']['independent_provision_scope']
        self.assertEqual(scope['status'], 'REVOKED')
        self.assertFalse(scope['is_authority_for_this_prepare'])
        self.assertEqual(self.metadata()['historical_approval_temporal_validity'], 'NOT_PROVEN')

    def test_14_active_authority_still_required_after_revocation(self):
        from app import controlled_skill_action as c, skill_only_test_qualification as q
        with patch.object(c, 'load_approval', side_effect=PermissionError('Scope revoked')):
            with self.assertRaises(PermissionError): q.check_context(None, self.context, environment='test')

    def test_15_prepare_label_alone_not_sufficient(self):
        del self.policy['qualification_receipt']
        self.context['tool_policy_snapshot'] = json.dumps(self.policy)
        with self.assertRaisesRegex(g.Blocked, 'QUALIFICATION_HASH'): self.metadata()

    def test_16_redacted_packet_cannot_fake_full_native_original_hash(self):
        # Do NOT patch the hash or pretend sanitized rows are original rows.
        with self.assertRaisesRegex(g.Blocked, 'ORIGINAL_CONTEXT_HASH'):
            self.history.verify(self.data, self.context, self.task)

    def test_17_fixture_bytes_immutable_and_tamper_rejected(self):
        with self.assertRaisesRegex(g.Blocked, 'FIXTURE_PIN'): h.HistoricalPrepare(self.raw_fixture+b' ')

    def test_18_create_draft_stays_disabled(self):
        path = Path(n.__file__).parents[1]/'app/skill_dispatch_config.py'
        code = path.read_text(encoding='utf-8')
        self.assertIn("('CREATE_DRAFT','wechat:draft:create',False)", code)
        self.policy['qualification_receipt']['request_action'] = 'CREATE_DRAFT'
        self.context['tool_policy_snapshot'] = json.dumps(self.policy)
        with self.assertRaises(g.Blocked): self.metadata()

    def classification_entry(self, entry):
        # Only the exact 06 context/Task/Run/policy/audit metadata is replayed.
        # Other prerequisite branches have inherited independent regressions;
        # these explicit emulations are NOT full Native status qualification.
        q = self.f['qualification_receipt']
        actor = dict(id=q['caller_id'], role='member')
        data = dict(self.data, users=[actor], agent_template_tests=[], platform_admins=[], tenant_agent_instances=[])
        scope = dict(tenant_id=q['tenant_id'], principal_id=q['caller_id'], agent_id=q['agent_id'],
            revision_id=q['agent_revision_id'], fingerprint=q['configuration_fingerprint'],
            original_role='member', principal_sha256='redacted-original-principal')
        runtime_scope = dict(tenant_id=scope['tenant_id'], actor_id=scope['principal_id'],
            agent_id=scope['agent_id'], revision_id=scope['revision_id'])
        parent_views = []
        def parent(view):
            self.assertEqual(view['agent_execution_contexts'], data['agent_execution_contexts'])
            self.assertEqual(view['task_agent_contexts'], data['task_agent_contexts'])
            self.assertEqual(view['execution_events'], data['execution_events'])
            parent_views.append(copy.deepcopy(view)); return {'status': 'READONLY_PARENT_EMULATION'}
        with (patch.object(n, 'validate_ledger', return_value=([], {}, [], 'absent')),
             patch.object(n.a, 'principal_hash', return_value='redacted-original-principal'),
             patch.object(g, 'validate_records', return_value={}),
             patch.object(n, 'validate_productization', return_value={'tenant_agent_instances': {'status': 'configured'}}),
             patch.object(h.HistoricalPrepare, 'verify', h.HistoricalPrepare.metadata)):
            result = entry(data, scope=scope, runtime_scope=runtime_scope, anchors={}, parent_pins={},
                predecessor_validate=parent, ordinary_principals=[scope['principal_id']],
                historical_prepare=self.history) if entry is n.validate_snapshot else entry(data,
                scope=scope, runtime_scope=runtime_scope, anchors={}, parent_pins={}, predecessor_validate=parent,
                ordinary_principals=[scope['principal_id']])
        self.assertEqual(len(parent_views), 1)
        return result

    def test_19_frozen_309_exact_classification_failure_reproduced(self):
        root = Path(n.__file__).parents[2]
        raw = subprocess.check_output(['git', 'show',
            '30981091b098b582b4471a894c0f1d191fd7fc65:enterprise_agent_poc/scripts/wechat_runtime_native_successor.py'], cwd=root)
        node = next(r for r in ast.parse(raw).body if isinstance(r, ast.FunctionDef) and r.name == 'validate_snapshot')
        globals_ = dict(n.__dict__)
        # globals for the frozen AST use the same explicit prerequisite doubles.
        globals_['validate_ledger'] = lambda *args, **kw: ([], {}, [], 'absent')
        globals_['validate_productization'] = lambda *args, **kw: {'tenant_agent_instances': {'status': 'configured'}}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'frozen-309-native-classification', 'exec'), globals_)
        with self.assertRaisesRegex(g.Blocked, 'NATIVE_ORDINARY_CHAT_AUTHORIZATION'):
            self.classification_entry(globals_['validate_snapshot'])

    def test_20_new_entry_metadata_classification_preserves_parent_view_readonly(self):
        before = copy.deepcopy(self.data)
        result = self.classification_entry(n.validate_snapshot)
        self.assertEqual(result['db_mutations'], 0)
        self.assertEqual(result['passed_runtime_tests'], 0)
        self.assertFalse(result['historical_prepares'][0]['execution_authorized'])
        self.assertEqual(self.data, before)

    def test_21_ordinary_unpublished_chat_still_rejected(self):
        del self.policy['eligibility_mode']; self.context['tool_policy_snapshot'] = json.dumps(self.policy)
        with self.assertRaisesRegex(g.Blocked, 'NATIVE_ORDINARY_CHAT_AUTHORIZATION'):
            self.classification_entry(n.validate_snapshot)


def suite(raw):
    HistoryTests.raw_fixture = raw
    return unittest.defaultTestLoader.loadTestsFromTestCase(HistoryTests)
