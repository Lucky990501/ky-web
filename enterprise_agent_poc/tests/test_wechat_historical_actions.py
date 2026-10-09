"""All four actual 06 historical objects; redacted semantic replay, not live PASS."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_skill_dispatch  # Offline Settings/DOCX stub before application imports.
from scripts import wechat_historical_actions as h
from scripts import wechat_runtime_native_successor as n
from scripts import wechat_runtime_test_lifecycle_guard as g


class ActionsTests(unittest.TestCase):
    def setUp(self):
        self.history = h.HistoricalActions(self.raw)
        self.f = self.history.packet
        self.data = {k: [] for k in ('tasks', 'run_traces', 'agent_execution_contexts',
            'task_agent_contexts', 'conversation_agent_contexts', 'execution_events')}
        for r in self.f['records']:
            c = next(e['metadata'] for e in r['audits'] if e['event_type'] == 'skill.controlled_action')
            # Shared actual Source contract + provided identities reconstruct
            # only the full qualification object. Its ORIGINAL observed SHA
            # is independently matched for every record, not replaced.
            q = dict(self.old_fixture['qualification_receipt'], source_commit=c['controlled_source'],
                source_tree=c['controlled_tree'], authority_id=c['authority_id'],
                approval_identity=r['approval_identity'], request_action=r['request_action'],
                negative_probe=r['request_action'] == 'CREATE_DRAFT')
            self.assertEqual(g.digest(q), r['qualification_receipt_sha256'])
            policy = copy.deepcopy(self.old_fixture['tool_policy_metadata']); policy['qualification_receipt'] = q
            ctx = dict(r['context'], tool_policy_snapshot=json.dumps(policy))
            task = copy.deepcopy(r['task']); run = dict(r['run'], payload=json.dumps(r['run_metadata']))
            self.data['tasks'].append(task); self.data['run_traces'].append(run)
            self.data['agent_execution_contexts'].append(ctx)
            self.data['task_agent_contexts'].append(dict(task_id=task['id'], context_id=ctx['id']))
            self.data['conversation_agent_contexts'].append(dict(conversation_id=task['conversation_id'], context_id=ctx['id']))
            for e in r['audits']:
                self.data['execution_events'].append({**{k: e[k] for k in ('id', 'event_type', 'conversation_id', 'created_at')},
                    'payload': json.dumps(e['metadata'])})

    def selected(self, index):
        return self.data['agent_execution_contexts'][index], self.data['tasks'][index]

    def result(self, index): return self.history.metadata(self.data, *self.selected(index))

    def event(self, index, kind):
        task = self.data['tasks'][index]
        return next(e for e in self.data['execution_events'] if e['event_type'] == kind
            and json.loads(e['payload'])['task_id'] == task['id'])

    def change_event(self, index, kind, **changes):
        e = self.event(index, kind); body = json.loads(e['payload']); body.update(changes); e['payload'] = json.dumps(body)

    def reject(self, index):
        with self.assertRaises(g.Blocked): self.result(index)

    def test_01_dca_prepare(self): self.assertEqual(self.result(0)['classification'], h.PREPARE)
    def test_02_dca_permission_denied(self): self.assertEqual(self.result(1)['classification'], h.DENIED)
    def test_03_d8a_prepare(self): self.assertEqual(self.result(2)['classification'], h.PREPARE)
    def test_04_d8a_permission_denied(self): self.assertEqual(self.result(3)['classification'], h.DENIED)

    def test_05_all_four_non_authorizing_and_temporal_unknown(self):
        for index in range(4):
            value = self.result(index)
            self.assertFalse(value['execution_authorized']); self.assertFalse(value['publish_eligible'])
            self.assertEqual(value['historical_approval_temporal_validity'], 'NOT_PROVEN')
        self.assertEqual(len(self.f['proposed_classification_contract']['static_conflicts_identified_together']), 5)

    def test_06_wrong_tenant(self): self.data['tasks'][1]['tenant_id'] = 'wrong'; self.reject(1)
    def test_07_wrong_agent(self): self.data['agent_execution_contexts'][1]['agent_id'] = 'wrong'; self.reject(1)
    def test_08_wrong_agent_revision(self): self.data['agent_execution_contexts'][1]['agent_template_version_id'] = 'wrong'; self.reject(1)
    def test_09_wrong_owner(self): self.data['tasks'][1]['user_id'] = 'wrong'; self.reject(1)

    def test_10_unknown_skill_action(self):
        self.change_event(1, 'skill.controlled_action', action='PUBLISH_ARTICLE'); self.reject(1)

    def test_11_forged_qualification_and_receipt(self):
        ctx, _ = self.selected(1); policy = json.loads(ctx['tool_policy_snapshot'])
        policy['qualification_receipt']['approval_identity'] = '0'*64
        ctx['tool_policy_snapshot'] = json.dumps(policy); self.reject(1)

    def test_12_missing_audit(self):
        self.data['execution_events'].remove(self.event(1, 'wechat.action.permission')); self.reject(1)

    def test_13_duplicate_audit(self):
        extra = copy.deepcopy(self.event(1, 'skill.execution')); extra['id'] = 1000
        self.data['execution_events'].append(extra); self.reject(1)

    def test_14_new_task_not_aggregate_history(self):
        self.data['tasks'][1]['id'] = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'; self.reject(1)

    def test_15_running_task_not_history(self): self.data['tasks'][1]['status'] = 'running'; self.reject(1)

    def test_16_extra_mapping(self):
        self.data['task_agent_contexts'].append(dict(task_id='new', context_id=self.selected(1)[0]['id'])); self.reject(1)

    def test_17_reused_run(self):
        extra = copy.deepcopy(self.data['tasks'][1]); extra['id'] = 'another'; self.data['tasks'].append(extra); self.reject(1)

    def test_18_denied_with_runtime_identity(self):
        self.change_event(1, 'skill.execution', runtime_identity='a'*64); self.reject(1)

    def test_19_denied_with_exit_status(self):
        self.change_event(1, 'skill.execution', exit_status=0); self.reject(1)

    def test_20_denied_with_workspace_identity(self):
        self.change_event(1, 'skill.execution', workspace_identity='a'*64); self.reject(1)

    def test_21_denied_with_artifacts(self):
        self.change_event(1, 'skill.execution', artifact_refs=[{'ref': 'unapproved'}]); self.reject(1)

    def test_22_denied_with_network_target(self):
        self.change_event(1, 'wechat.action.permission', network_targets=['api.weixin.qq.com']); self.reject(1)

    def test_23_denied_with_media_id(self):
        self.change_event(1, 'wechat.action.permission', draft_media_id='unapproved'); self.reject(1)

    def test_24_failed_after_actual_execution_is_not_permission_denied(self):
        self.change_event(1, 'skill.execution', failure_phase='execution'); self.reject(1)

    def test_25_denied_wrong_error(self):
        self.change_event(1, 'skill.execution', error_code='SKILL_EXECUTION_FAILED'); self.reject(1)

    def test_26_prepare_and_draft_not_interchangeable(self):
        self.change_event(0, 'skill.execution', action='CREATE_DRAFT'); self.reject(0)

    def test_27_successful_draft_not_denial(self):
        self.change_event(1, 'skill.execution', status='completed'); self.reject(1)

    def test_28_wrong_skill_revision(self):
        self.change_event(1, 'skill.execution', revision='wrong'); self.reject(1)

    def test_29_redacted_rows_do_not_pass_native_original_hashes(self):
        for index in range(4):
            with self.subTest(index=index), self.assertRaisesRegex(g.Blocked, 'HISTORICAL_ACTION_ORIGINAL_HASH'):
                self.history.verify(self.data, *self.selected(index))

    def test_30_new_execution_still_requires_current_authority(self):
        from app import controlled_skill_action as c, skill_only_test_qualification as q
        with patch.object(c, 'load_approval', side_effect=PermissionError('Current authority revoked')):
            with self.assertRaises(PermissionError): q.check_context(None, self.selected(1)[0], environment='test')

    def test_31_create_draft_registration_unchanged(self):
        self.assertIn("('CREATE_DRAFT','wechat:draft:create',False)",
            (Path(n.__file__).parents[1]/'app/skill_dispatch_config.py').read_text(encoding='utf-8'))

    def test_32_no_mutation_or_authority_from_metadata(self):
        before = copy.deepcopy(self.data)
        for index in range(4): self.result(index)
        self.assertEqual(before, self.data)

    def test_33_audit_bytes_pin(self):
        with self.assertRaisesRegex(g.Blocked, 'AUDIT_PIN'): h.HistoricalActions(self.raw+b' ')

    def native_classification_replay(self):
        # Full original hashes and native authority/PG must be validated by 06.
        # Explicit prerequisite doubles here test ALL four routing paths and
        # unchanged delegated history together, never claim full status PASS.
        scope = dict(self.f['scope'], principal_id=self.f['records'][0]['task']['user_id'], original_role='member',
            fingerprint=self.f['revision']['configuration_fingerprint'], principal_sha256='redacted-actor')
        actor = dict(id=scope['principal_id'], role='member')
        data = dict(self.data, users=[actor], agent_template_tests=[], platform_admins=[], tenant_agent_instances=[])
        runtime_scope = dict(tenant_id=scope['tenant_id'], actor_id=scope['principal_id'], agent_id=scope['agent_id'], revision_id=scope['revision_id'])
        def parent(view):
            for key in self.data: self.assertEqual(view[key], self.data[key])
            return {'status': 'EXPLICIT_PARENT_EMULATION'}
        with (patch.object(n, 'validate_ledger', return_value=([], {}, [], 'absent')),
              patch.object(n.a, 'principal_hash', return_value='redacted-actor'),
              patch.object(g, 'validate_records', return_value={}),
              patch.object(n, 'validate_productization', return_value={'tenant_agent_instances': {'status': 'configured'}}),
              patch.object(h.HistoricalActions, 'verify', h.HistoricalActions.metadata)):
            return n.validate_snapshot(data, scope=scope, runtime_scope=runtime_scope, anchors={}, parent_pins={},
                ordinary_principals=[scope['principal_id']], predecessor_validate=parent, historical_prepare=self.history)

    def test_34_all_four_native_classification_routes_preserve_parent_history(self):
        result = self.native_classification_replay()
        self.assertEqual(len(result['historical_actions']), 4)
        self.assertEqual(len(result['historical_prepares']), 2)
        self.assertEqual(result['passed_runtime_tests'], 0)
        self.assertEqual(result['db_mutations'], 0)

    def test_35_unpublished_ordinary_chat_rejects(self):
        ctx = self.selected(1)[0]; policy = json.loads(ctx['tool_policy_snapshot']); del policy['eligibility_mode']
        ctx['tool_policy_snapshot'] = json.dumps(policy)
        with self.assertRaisesRegex(g.Blocked, 'NATIVE_ORDINARY_CHAT_AUTHORIZATION'): self.native_classification_replay()

    def test_36_no_extra_task_witness_paths(self):
        self.assertEqual(len(self.f['existing_acceptance_anchor_confirmation']['existing_acceptance_anchors']), 2)
        self.assertEqual(self.f['no_new_witnesses'], 0)
        for r in self.f['records']: self.history.anchor(r['task'], r['run'])


def suite(raw, old_fixture):
    ActionsTests.raw, ActionsTests.old_fixture = raw, json.loads(old_fixture)
    return unittest.defaultTestLoader.loadTestsFromTestCase(ActionsTests)
