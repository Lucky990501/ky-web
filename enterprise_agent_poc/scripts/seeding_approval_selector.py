"""Bounded d8a -> 28e approval transaction used by the formal recovery entry.

No database writer, Runtime permission, fallback Loader or generic deployment
engine. Native I/O and existing release hooks live in seeding_release_binding.
Component doubles exercise this state machine; they are NOT Native acceptance.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from scripts.wechat_runtime_test_lifecycle_guard import need

VERSION = 'VERSIONED_SEEDING_APPROVAL_SELECTOR_V1'
CONTRACT = 'TEST_TENANT_AUTO_SEEDING_EXCLUSIONS_V1'
IDENTITIES = {
    'd8a': ('d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7', '6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5'),
    '28e': ('28e061a114118a27609330125c756e193d3248a6', '8ff5e4a8ed26c32ba040b4fa27cf1d380f3c7541'),
}
DATABASE = dict(address='127.0.0.1', port=55432, database='enterprise_agent_test', db_role='enterprise_agent_test')
ZERO = dict(wechat=0, provider=0, image=0)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)+'\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def approval(raw, version):
    value = json.loads(raw)
    need(set(value) == {'contract', 'authority_id', 'environment', 'source_commit', 'source_tree',
        'database', 'production_deploy_authority', 'budget', 'exclusions'}, 'SELECTOR_APPROVAL_SHAPE')
    need(value['contract'] == CONTRACT and value['environment'] == 'test'
        and value['production_deploy_authority'] is False and value['database'] == DATABASE
        and value['budget'] == ZERO and all(type(v) is int for v in value['budget'].values()),
        'SELECTOR_APPROVAL_DOMAIN')
    need((value['source_commit'], value['source_tree']) == IDENTITIES[version], 'SELECTOR_APPROVAL_SOURCE')
    # This exact release starts AFTER receipt-owned Synthetic cleanup. Provision
    # exclusions require their own formal successor; never silently drop them.
    need(value['exclusions'] == [], 'SELECTOR_ZERO_EXCLUSION_RELEASE_REQUIRED')
    need(isinstance(value['authority_id'], str) and re.fullmatch('[A-Za-z0-9_-]{1,128}',value['authority_id']),
         'SELECTOR_AUTHORITY_ID')
    return value


def issue_target(policy):
    """New declaration from separately approved release intent, NOT old.copy()."""
    return canonical(dict(contract=CONTRACT, authority_id=policy['target_authority_id'], environment='test',
        source_commit=IDENTITIES['28e'][0], source_tree=IDENTITIES['28e'][1], database=DATABASE,
        production_deploy_authority=False, budget=ZERO, exclusions=[]))


class Selector:
    """All public operations take the EXISTING formal release lock.

    backend validates independently approved policy/code/source before entry;
    hooks must not grant Admin, run tests, restore a DB or revoke WeChat config.
    """
    def __init__(self, backend):
        self.b = backend

    def load(self):
        s = self.b.state()
        if s is None:
            return None
        need(set(s) == {'contract', 'policy_sha256', 'operation_id', 'events'}, 'SELECTOR_STATE_SHAPE')
        need(s['contract'] == VERSION and s['policy_sha256'] == self.b.policy_sha
             and s['operation_id'] == self.b.policy['operation_id'], 'SELECTOR_STATE_BINDING')
        need(isinstance(s['events'], list) and 0 < len(s['events']) < 512, 'SELECTOR_STATE_HISTORY')
        previous = None
        last = None
        baseline = None
        edges = {None: {'PREPARED'}, 'PREPARED': {'QUIESCING'},
            'QUIESCING': {'QUIESCED','RECOVERING'}, 'QUIESCED': {'SWITCHING','RECOVERING'},
            'SWITCHING': {'SELECTED','RECOVERING'}, 'SELECTED': {'COMPLETE','RECOVERING'},
            'COMPLETE': {'RECOVERING'}, 'RECOVERING': {'RECOVERING','RECOVERED'}, 'RECOVERED': set()}
        for i, event in enumerate(s['events']):
            need(set(event) == {'sequence', 'previous', 'phase', 'direction', 'baseline', 'at'}
                 and type(event['sequence']) is int and event['sequence'] == i and event['previous'] == previous
                 and event['phase'] in edges.get(last, set())
                 and event['direction'] in {'d8a','28e'}, 'SELECTOR_STATE_EVENT')
            if event['phase'] in {'PREPARED','QUIESCING'}:
                need(event['baseline'] is None and event['direction']=='d8a', 'SELECTOR_STATE_BASELINE')
            else:
                need(isinstance(event['baseline'],str) and re.fullmatch('[a-f0-9]{64}',event['baseline'])
                     and (baseline is None or baseline==event['baseline']), 'SELECTOR_STATE_BASELINE')
                baseline=event['baseline']
            if event['phase'] in {'SWITCHING','SELECTED','COMPLETE'}:
                need(event['direction']=='28e','SELECTOR_STATE_DIRECTION')
            if last == 'RECOVERING':
                need(event['direction']==s['events'][i-1]['direction'],'SELECTOR_STATE_DIRECTION')
            last=event['phase']
            previous = sha(canonical(event))
        return s

    def checkpoint(self, s, phase, direction, baseline):
        if s is None:
            s = dict(contract=VERSION, policy_sha256=self.b.policy_sha,
                     operation_id=self.b.policy['operation_id'], events=[])
        events = list(s['events'])
        events.append(dict(sequence=len(events), previous=sha(canonical(events[-1])) if events else None,
            phase=phase, direction=direction, baseline=baseline, at=datetime.now(timezone.utc).isoformat()))
        s = dict(s, events=events)
        self.b.save(s)
        return s

    def versions(self):
        old = self.b.version('d8a')
        new = self.b.version('28e')
        old_value = approval(old, 'd8a'); new_value = approval(new, '28e')
        need(old_value['authority_id'] != new_value['authority_id'], 'SELECTOR_NEW_AUTHORITY_REQUIRED')
        need(sha(old) == self.b.policy['predecessor_approval_sha256'], 'SELECTOR_OLD_APPROVAL_PIN')
        need(new == issue_target(self.b.policy), 'SELECTOR_TARGET_ISSUANCE')
        return {'d8a': old, '28e': new}

    def prepare(self):
        with self.b.lock():
            self.b.validate()
            s = self.load()
            if s is not None:
                self.versions()
                return s
            need(self.b.current() == 'd8a', 'SELECTOR_EXACT_PREDECESSOR')
            old = self.b.active()
            old_value = approval(old, 'd8a')
            need(old_value['authority_id'] != self.b.policy['target_authority_id'], 'SELECTOR_NEW_AUTHORITY_REQUIRED')
            need(sha(old) == self.b.policy['predecessor_approval_sha256'], 'SELECTOR_OLD_APPROVAL_PIN')
            self.b.predecessor()  # existing full data/schema/config compatibility, no topology demand
            # Exclusive/idempotent creation. A crash between these writes only
            # leaves immutable preparations, never changes the selected file.
            self.b.archive('d8a', old)
            self.b.archive('28e', issue_target(self.b.policy))
            self.versions()
            return self.checkpoint(None, 'PREPARED', 'd8a', None)

    def ensure_known(self, versions):
        need(self.b.active() in versions.values(), 'SELECTOR_UNKNOWN_ACTIVE_APPROVAL')
        need(self.b.current() in IDENTITIES, 'SELECTOR_UNKNOWN_CURRENT')

    def select(self, version, versions):
        self.b.assert_quiesced()
        self.b.install(versions[version])
        self.b.switch(version)  # reuses pinned formal switch_pointer body
        self.b.configure(version)
        need(self.b.current() == version and self.b.active() == versions[version], 'SELECTOR_PAIR_MISMATCH')
        self.b.loader(version)  # actual fixed Loader, mandatory; no fallback

    def activate(self):
        with self.b.lock():
            self.b.validate()
            s = self.load()
            need(s is not None, 'SELECTOR_PREPARE_REQUIRED')
            versions = self.versions(); self.ensure_known(versions)
            event = s['events'][-1]
            if event['phase'] == 'COMPLETE':
                self.b.verify_running('28e', versions['28e'])
                return s
            need(event['phase'] == 'PREPARED', 'SELECTOR_RECOVERY_REQUIRED')
            need(self.b.current() == 'd8a' and self.b.active() == versions['d8a'], 'SELECTOR_EXACT_PREDECESSOR')
            s = self.checkpoint(s, 'QUIESCING', 'd8a', None)
            self.b.quiesce(); self.b.predecessor()
            baseline = self.b.fingerprint()
            s = self.checkpoint(s, 'QUIESCED', 'd8a', baseline)
            s = self.checkpoint(s, 'SWITCHING', '28e', baseline)
            self.select('28e', versions)
            s = self.checkpoint(s, 'SELECTED', '28e', baseline)
            self.b.start('28e')
            self.b.verify_running('28e', versions['28e'])
            return self.checkpoint(s, 'COMPLETE', '28e', baseline)

    def recover(self):
        with self.b.lock():
            self.b.validate()
            s = self.load()
            need(s is not None, 'SELECTOR_PREPARE_REQUIRED')
            versions = self.versions(); self.ensure_known(versions)
            event = s['events'][-1]
            if event['phase'] == 'PREPARED':
                need(self.b.current() == 'd8a' and self.b.active() == versions['d8a'], 'SELECTOR_PREPARED_DRIFT')
                return s  # no switch was started
            if event['phase'] == 'RECOVERED':
                self.b.verify_running(event['direction'], versions[event['direction']])
                return s
            self.b.quiesce()
            if event['phase'] == 'QUIESCING':
                # Crash while stopping services: no selector write was allowed
                # yet. Full predecessor compatibility is still mandatory.
                need(self.b.current()=='d8a' and self.b.active()==versions['d8a'], 'SELECTOR_PRE_SWITCH_DRIFT')
                self.b.predecessor()
                event = dict(event, baseline=self.b.fingerprint())
            need(isinstance(event['baseline'], str) and len(event['baseline']) == 64, 'SELECTOR_BASELINE_REQUIRED')
            # Conservative: ANY database/schema change goes forward. Equality
            # alone never permits downgrade: old exact guard must also pass.
            direction = '28e'
            if self.b.fingerprint() == event['baseline']:
                self.b.predecessor()
                direction = 'd8a'
            if event['phase'] == 'RECOVERING':
                # Never reverse a recorded forward-recovery decision.
                if event['direction'] == '28e': direction = '28e'
                need(not (event['direction'] == 'd8a' and direction != 'd8a'), 'SELECTOR_RECOVERY_DATA_DRIFT')
            s = self.checkpoint(s, 'RECOVERING', direction, event['baseline'])
            self.select(direction, versions)
            if direction == '28e':
                self.b.forward()  # existing 796a operator preserves Admission + Revoke
            else:
                self.b.predecessor()
                self.b.start('d8a')
            self.b.verify_running(direction, versions[direction])
            return self.checkpoint(s, 'RECOVERED', direction, event['baseline'])

    def status(self):
        with self.b.lock():
            self.b.validate()
            s = self.load()
            if s:
                versions = self.versions()
                self.ensure_known(versions)
                event = s['events'][-1]
                if event['phase'] in {'PREPARED','SELECTED','COMPLETE','RECOVERED'}:
                    version = event['direction']
                    need(self.b.current() == version and self.b.active() == versions[version],
                         'SELECTOR_STATUS_PAIR_MISMATCH')
            return s
