"""Exact d8a -> 810 Test coordinator. Not a Selector or authority issuer.

Native I/O reuses the sealed formal switch/units and existing five services.
Test doubles are component evidence only. No DCA cleanup, DB writer or grant.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import stat
import os
import sys
import importlib.util

VERSION = 'RAG_810_EXISTING_TEST_NATIVE_OPERATOR_V1'
APPLICATION = {'source': '810ccfb5e6d59fdb3e359f3d5e1216ebdca4eeb9',
               'tree': '06d4a1c794f77f9876dc6fae37635e70b1d7bf48'}
PREDECESSOR = {'source': 'd8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7',
               'tree': '6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5'}
ROLES = ('api', 'mcp', 'worker')
PHASES = ('QUIESCING', 'QUIESCED', 'CONFIGURED', 'SELECTED', 'ADMITTED', 'STARTED',
          'COMMITTED', 'RECOVERING', 'RESTORED_D8A', 'RECOVERED_810', 'MANUAL')


class Blocked(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise Blocked(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def validate_journal(events, binding):
    previous = None
    admitted = False
    last = None
    edges = {None: {'QUIESCING'}, 'QUIESCING': {'QUIESCED', 'RECOVERING', 'MANUAL'},
        'QUIESCED': {'CONFIGURED', 'RECOVERING', 'MANUAL'}, 'CONFIGURED': {'SELECTED', 'RECOVERING', 'MANUAL'},
        'SELECTED': {'ADMITTED', 'RECOVERING', 'MANUAL'}, 'ADMITTED': {'STARTED', 'RECOVERING', 'MANUAL'},
        'STARTED': {'COMMITTED', 'RECOVERING', 'MANUAL'}, 'COMMITTED': {'RECOVERING', 'MANUAL'},
        'RECOVERING': {'RECOVERING', 'RESTORED_D8A', 'RECOVERED_810', 'MANUAL'},
        'RESTORED_D8A': {'RECOVERING', 'MANUAL'}, 'RECOVERED_810': {'RECOVERING', 'MANUAL'},
        'MANUAL': {'RECOVERING', 'MANUAL'}}
    need(isinstance(events, list) and len(events) <= 256, 'RAG_OP_JOURNAL_SHAPE')
    for sequence, event in enumerate(events):
        need(set(event) == {'sequence', 'previous', 'binding', 'phase', 'admitted', 'at'},
             'RAG_OP_EVENT_SHAPE')
        need(type(event['sequence']) is int and event['sequence'] == sequence and
             event['previous'] == previous and event['binding'] == binding and event['phase'] in edges.get(last, set()),
             'RAG_OP_EVENT_BINDING')
        need(type(event['admitted']) is bool and not (admitted and not event['admitted']),
             'RAG_OP_ADMISSION_REVERSAL')
        need(not (event['phase'] == 'RESTORED_D8A' and event['admitted']), 'RAG_OP_DOWNGRADE_AFTER_ADMISSION')
        need(event['phase'] not in ('ADMITTED', 'STARTED', 'COMMITTED', 'RECOVERED_810') or event['admitted'],
             'RAG_OP_PHASE_ADMISSION_REQUIRED')
        need(isinstance(event['at'], str) and event['at'].endswith('+00:00'), 'RAG_OP_EVENT_TIME')
        admitted = event['admitted']
        last = event['phase']
        previous = sha(canonical(event))
    return admitted


def validate_live(states, expected):
    """No live mixed pair. Config transitions are permitted only when stopped."""
    need(set(states) == set(ROLES), 'RAG_OP_ROLE_SET')
    live = []
    for role, state in states.items():
        need(type(state['pid']) is int and state['pid'] >= 0, 'RAG_OP_PID')
        if state['pid']:
            need(state['active'] in ('active', 'activating'), 'RAG_OP_PROCESS_STATE')
            live.append(state['source'])
    need(not live or set(live) == {expected}, 'RAG_OP_MIXED_LIVE_SOURCE')


def recovery_direction(record, marker, events, plan, binding):
    admitted = validate_journal(events, binding)
    need(type(marker) is bool and type(record['business_writes_admitted']) is bool,
         'RAG_OP_ADMISSION_SHAPE')
    need(not record['business_writes_admitted'] or marker, 'RAG_OP_ADMISSION_PROOF_MISSING')
    need(not admitted or marker, 'RAG_OP_ADMISSION_MARKER_LOST')
    need(plan['executes_recovery'] is False and plan['synthetic_cleanup'] is False
         and plan['preserve_data'] is True and plan['schema_down'] is False, 'RAG_OP_RECOVERY_PLAN')
    if marker or record['business_writes_admitted'] or plan['mode'] == 'FORWARD_SUCCESSOR_KEEP_DATA':
        return '810'
    need(plan['mode'] == 'EXACT_D8A_RESTORE_REQUIRES_NATIVE_GATES', 'RAG_OP_UNKNOWN_RECOVERY_PLAN')
    return 'd8a'


class ExactOperator:
    """Only one fixed successor; all mutations sit inside the existing lock."""
    def __init__(self, backend):
        self.b = backend

    def checkpoint(self, phase):
        events = self.b.events()
        admitted = validate_journal(events, self.b.binding)
        marker = self.b.admitted()
        need(not admitted or marker, 'RAG_OP_ADMISSION_MARKER_LOST')
        event = {'sequence': len(events), 'previous': sha(canonical(events[-1])) if events else None,
            'binding': self.b.binding, 'phase': phase, 'admitted': marker,
            'at': datetime.now(timezone.utc).isoformat()}
        self.b.append(event)

    def activate(self):
        with self.b.lock():
            # All these checks are read-only. No snapshot/stop before PASS.
            self.b.authorize('activate')
            need(not self.b.events() and not self.b.admitted(), 'RAG_OP_ATTEMPT_ALREADY_STARTED')
            self.b.exact_predecessor(live=True)
            self.b.inspect_data()
            self.b.queue_check(initial=True)
            self.b.profiles_check('d8a')
            self.b.infra_health()
            self.b.capture_profiles()
            self.checkpoint('QUIESCING')
            try:
                self.b.quiesce()
                self.b.assert_stopped()
                self.b.exact_predecessor(live=False)
                self.b.queue_check(initial=True)
                self.b.inspect_data()
                self.checkpoint('QUIESCED')
                self.b.configure('810')
                self.checkpoint('CONFIGURED')
                self.b.switch('810')
                self.checkpoint('SELECTED')
                self.b.profiles_check('810')
                # Immutable NO-REPLACE marker BEFORE Runtime record/service start.
                self.b.seal_admission()
                self.b.runtime_state('ACTIVATING')
                self.checkpoint('ADMITTED')
                self.b.start('810')
                self.checkpoint('STARTED')
                self.b.health('810')
                self.b.inspect_data()
                self.b.runtime_state('COMMITTED')
                self.b.health('810')
                self.checkpoint('COMMITTED')  # commit point; lock still held
                return {'status': 'RAG_810_TEST_SWITCH_PASS', 'source': APPLICATION['source'],
                        'provider_calls': 0, 'schema_change': False}
            except Exception:
                return self._recover_locked()

    def recover(self):
        with self.b.lock():
            self.b.authorize('recover')
            need(bool(self.b.events()), 'RAG_OP_NO_RELEASE_ATTEMPT')
            return self._recover_locked()

    def _recover_locked(self):
        try:
            self.b.authorize('recover')
            self.b.known_state()  # reject live mixed/unknown before guessing
            self.b.seal_live_operator()
            self.b.quiesce()
            self.b.assert_stopped()
            self.b.queue_check(initial=False)
            record, plan = self.b.inspect_data()
            direction = recovery_direction(record, self.b.admitted(), self.b.events(), plan, self.b.binding)
            self.checkpoint('RECOVERING')
            if direction == 'd8a':
                self.b.exact_predecessor(live=False, selected=False)
                self.b.configure('d8a')
                self.b.switch('d8a')
                self.b.start('d8a')
                self.b.health('d8a')
                self.checkpoint('RESTORED_D8A')
                return {'status': 'RAG_810_TEST_RESTORED_D8A', 'rag': 'KNOWN_UNAVAILABLE_NOT_HEALTHY'}
            if not self.b.admitted():
                self.b.seal_admission()
            self.b.configure('810')
            self.b.switch('810')
            self.b.runtime_state('ACTIVATING')
            self.b.requeue_knowledge()  # exact existing queue, preserve every item
            self.b.start('810')
            self.b.health('810')
            self.b.inspect_data()
            self.b.runtime_state('COMMITTED')
            self.b.health('810')
            self.checkpoint('RECOVERED_810')
            return {'status': 'RAG_810_TEST_FORWARD_RECOVERED', 'data_preserved': True}
        except Exception:
            evidence = 'CAPTURED'
            try: self.b.capture_failure()  # no credentials/business row dumps
            except Exception: evidence = 'CAPTURE_FAILED_SNAPSHOTS_RETAINED'
            # Never forge a valid ledger/marker to make unknown state recover.
            try: self.checkpoint('MANUAL')
            except Exception: pass
            return {'status': 'RAG_810_TEST_MANUAL_RECOVERY_REQUIRED', 'evidence': evidence}


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('action', choices=('preflight', 'activate', 'recover', 'pre', 'post'))
    parser.add_argument('role', nargs='?', choices=ROLES)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    # Root -I entry never takes sibling code from ordinary PYTHONPATH. Verify
    # both protected code pins BEFORE evaluating the Native backend module.
    root = Path('/etc/enterprise-agent-test-rag-successor-v1')
    paths = [root / 'operator-policy.v1.json', root / 'operator-approval.v1.json',
             Path(__file__).resolve(), Path(__file__).resolve().with_name('rag810_test_operator_native.py')]
    for path in paths:
        for item in (path, *path.parents):
            st = item.lstat()
            need(not stat.S_ISLNK(st.st_mode) and st.st_uid == st.st_gid == 0 and
                 not st.st_mode & 0o022, 'RAG_OP_ENTRY_PATH_TRUST')
        need(stat.S_ISREG(path.stat().st_mode) and not path.stat().st_mode & 0o222,
             'RAG_OP_ENTRY_READONLY')
    policy_raw = paths[0].read_bytes()
    policy, approval = json.loads(policy_raw), json.loads(paths[1].read_bytes())
    need(approval['policy_sha256'] == sha(policy_raw) and approval['production_authority'] is False and
         approval['authorization'] == 'RAG_810_EXACT_TEST_MAINTENANCE_INDEPENDENTLY_APPROVED',
         'RAG_OP_ENTRY_APPROVAL')
    for path in paths[2:]:
        need(sha(path.read_bytes()) == policy['code_sha256'][path.name], 'RAG_OP_ENTRY_CODE_PIN')
    sys.modules['rag810_test_operator'] = sys.modules[__name__]
    spec = importlib.util.spec_from_file_location('_rag810_native_backend', paths[3])
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    NativeBackend = backend.NativeBackend
    b = NativeBackend()
    if args.action in ('pre', 'post'):
        need(args.role is not None and not args.execute, 'RAG_OP_ROLE_HOOK_ARGUMENT')
        result = b.service_hook(args.action, args.role)
    else:
        need(args.role is None, 'RAG_OP_ROLE_FORBIDDEN')
        if args.action == 'preflight':
            need(not args.execute, 'RAG_OP_PREFLIGHT_READONLY')
            with b.lock():
                b.authorize('preflight'); b.exact_predecessor(live=True)
                b.inspect_data(); b.queue_check(initial=True); b.profiles_check('d8a'); b.infra_health()
                result = {'status': 'RAG_810_TEST_PREFLIGHT_PASS', 'mutation': False}
        else:
            need(args.execute, 'RAG_OP_EXPLICIT_EXECUTE_REQUIRED')
            result = getattr(ExactOperator(b), args.action)()
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'].endswith('PASS') or result['status'].endswith('RECOVERED') else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        label = str(exc) if type(exc) is Blocked and re.fullmatch('RAG_OP_[A-Z0-9_]+', str(exc)) else 'RAG_OP_INPUT_OR_GATE'
        print(json.dumps({'status': 'RAG_810_TEST_OPERATOR_BLOCKED', 'first_failure_point': label}))
        raise SystemExit(75 if label == 'RAG_OP_RELEASE_SWITCH_LOCKED' else 2)
