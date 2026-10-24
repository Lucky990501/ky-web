"""Fixed seven-callback worker inside the existing non-Root Test sandbox.

Not a daemon or alternate installer. EOF/failure always closes this one marked
IsolatedServices instance. All Provider output is the existing zero-cost double.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS)); sys.path.insert(0, str(SCRIPTS.parent))
from scripts import test_delivery_context as context
from scripts import test_delivery_native as native

p = context.p


def installed_readback(archive, manifest, project, bound):
    """Read back the installed private files against the SAME verified artifact."""
    from scripts.release_manifest import validate_manifest_contract
    p.require(native.sha(archive) == bound['archive_sha256']
        and native.sha(manifest) == bound['manifest_sha256'], 'TRANSFER_IDENTITY')
    spec = validate_manifest_contract(p.read(manifest))
    files = list(project.rglob('*'))
    p.require(not any(x.is_symlink() for x in files)
        and {x.relative_to(project.parent).as_posix() for x in files if x.is_file()} == set(spec['selected_files']),
        'INSTALLED_FILE_SET')
    with tarfile.open(archive) as tf:
        for member in tf:
            if member.isfile():
                h=hashlib.sha256()
                with tf.extractfile(member) as stream:
                    for part in iter(lambda:stream.read(65536),b''):h.update(part)
                p.require(native.sha(project.parent/member.name) == h.hexdigest(), 'INSTALLED_FILE_BYTES')
    return dict(status='PASS',file_count=len(spec['selected_files']))


class IsolatedCallbacks:
    """Actual mappings to the already-reviewed private Job, not legacy894 CLI."""
    def __init__(self, request, archive, manifest, root, *, job=None):
        context.validate_request(request)
        self.request = request; self.archive = Path(archive); self.manifest = Path(manifest)
        self.root = Path(root); self.receipts = {}; self.index = 0; self.recovery = None
        self.job = job or native.Job(request, self.root)
        self.job.input_size = self.archive.stat().st_size+self.manifest.stat().st_size+len(p.canonical(request))+1

    def preflight(self):
        p.require(self.job.input_size <= p.INPUT_LIMIT, 'COMBINED_INPUT_BUDGET')
        p.require(native.sha(self.archive) == self.request['artifact']['archive_sha256']
                  and native.sha(self.manifest) == self.request['artifact']['manifest_sha256'], 'TRANSFER_IDENTITY')
        self.job.space()
        return dict(target='ISOLATED_CANDIDATE', primary_access='DENIED')

    def backup(self):
        # This stage precedes installation. There is no old private instance/data
        # to back up. Actual populated private-data dump/restore is in qualify.
        p.require(self.job.s.root is None, 'ISOLATED_INSTANCE_MUST_BE_NEW')
        return dict(backup_kind='NOT_APPLICABLE_NEW_EMPTY_INSTANCE',
                    primary_backup='NOT_PERMITTED', populated_restore_check='QUALIFY')

    def install(self):
        self.job.prepare_root()
        self.job.project = native.safe_extract(self.archive, self.manifest, self.request['candidate'],
            self.request['artifact'], self.job.s.root/'candidate')
        p.require(self.job.project.is_relative_to(self.job.s.root), 'INSTALL_OUTSIDE_PRIVATE_INSTANCE')
        return dict(install_kind='NEW_PRIVATE_DIRECTORY',
            readback=installed_readback(self.archive,self.manifest,self.job.project,self.request['artifact']))

    def activate(self):
        p.require(self.job.project is not None, 'CANDIDATE_NOT_INSTALLED')
        self.job.start_databases()
        self.job.stage = 'PROVISION_SYNTHETIC'
        self.job.command(self.job.role_args('provision'))
        for role in ('mcp', 'api', 'worker'):
            self.job.start_role(role)
        return dict(activation_kind='ISOLATED_START_ONLY', primary_pointer='NOT_TOUCHED')

    def health(self):
        self.job.health()
        tables = self.job.database_fingerprint(self.job.s.database)
        return dict(backend='postgresql-redis-worker-mcp', processes=self.job.process_identities,
            private_database=dict(name=self.job.s.database,
                socket_path_sha256=hashlib.sha256(str(self.job.s.root/'pg/socket').encode()).hexdigest(),
                schema_ledger=tables.get('schema_migrations'), runtime_versions=self.job.runtime_versions))

    def qualify(self):
        self.job.phase('five')
        self.job.stop_role('worker'); self.job.phase('cancel')
        self.job.start_role('worker'); self.job.phase('cancel-check')
        self.job.phase('interrupt'); self.job.stop_role('worker', kill=True)
        self.job.start_role('worker'); self.job.phase('unknown-check')
        for role in ('worker', 'api', 'mcp'):
            self.job.stop_role(role)
        self.recovery = self.job.backup_restore(self.job.project, reader_identity=self.request['candidate'])
        p.require(self.recovery['reader_kind'] == 'SAME_CANDIDATE'
            and self.recovery['reader_source'] == self.request['candidate']['source']
            and self.recovery['restored_database_distinct'] is True, 'PRIVATE_RESTORE_BINDING')
        self.job.space()
        return dict(checks={x: 'PASS' for x in context.CHECKS}, recovery=self.recovery,
                    provider_calls=0, real_model_quality='NOT_TESTED')

    def export(self):
        p.require(self.recovery is not None, 'QUALIFICATION_NOT_COMPLETED')
        return dict(stages=self.job.evidence, processes=self.job.process_identities,
            installed_readback=installed_readback(self.archive,self.manifest,self.job.project,self.request['artifact']),
            disk_peak_bytes=self.job.peak, min_root_free_fraction=self.job.free_min,
            runtime_versions=self.job.runtime_versions,
            knowledge=self.job.health_value.get('knowledge', 'NOT_REPORTED'),
            knowledge_semantic_acceptance='NOT_IN_SCOPE_LOCAL_HASH',
            native_systemd_per_service_parity='NOT_PROVEN',
            primary_backup='NOT_EXECUTED', primary_activation='NOT_EXECUTED',
            artifact_rebuilt=False, provider_calls=0)

    def step(self, command):
        p.require(self.index < len(context.STEPS) and command == dict(
            stage=context.STEPS[self.index], request_sha256=p.digest(self.request),
            prior_sha256=p.digest(self.receipts)), 'FIXED_STAGE_ORDER_OR_BINDING')
        name = context.STEPS[self.index]
        self.job.stage = name.upper()
        # Only the fixed table above is callable. Request JSON cannot supply a
        # Python function, file name, shell command or lifecycle method.
        result = dict(status='PASS', context=context.CONTEXT, stage=name,
            operation_id=self.request['operation_id'], request_sha256=p.digest(self.request),
            source=self.request['candidate']['source'], artifact=self.request['artifact'],
            measurement=getattr(self, name)())
        self.receipts[name] = result; self.index += 1
        return result

    def close(self):
        if self.job.s.root:
            self.job.s.stop()


def serve(callbacks, incoming, outgoing):
    """One request stream; no retries/resume. Safe failure before PG cleanup."""
    try:
        for line in incoming:
            p.require(len(line.encode()) <= 4096, 'COMMAND_SIZE')
            command = json.loads(line, object_pairs_hook=p.unique)
            with redirect_stdout(sys.stderr):
                value = callbacks.step(command)
            outgoing.write(json.dumps(value, ensure_ascii=False)+'\n'); outgoing.flush()
        p.require(callbacks.index == len(context.STEPS), 'INCOMPLETE_STAGE_STREAM')
    except BaseException as exc:
        code = str(exc) if isinstance(exc, (p.Block, RuntimeError)) and re.fullmatch('[A-Z0-9_:-]{1,100}', str(exc)) else 'REDACTED'
        outgoing.write(json.dumps(dict(status='FAILED', failure_code=code,
            stage=callbacks.job.stage, exception_class=type(exc).__name__,
            stages=callbacks.job.evidence))+'\n'); outgoing.flush()
        raise
    finally:
        with redirect_stdout(sys.stderr):
            callbacks.close()
    outgoing.write(json.dumps(dict(status='CLOSED', context=context.CONTEXT,
        operation_id=callbacks.request['operation_id'], request_sha256=p.digest(callbacks.request),
        private_services_stopped=True))+'\n'); outgoing.flush()


def main():
    parser = argparse.ArgumentParser()
    for name in ('request', 'archive', 'manifest', 'root'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    native.sandbox_preflight(args.root)
    request = p.read(args.request)
    serve(IsolatedCallbacks(request, args.archive, args.manifest, args.root), sys.stdin, sys.stdout)


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        # Failure receipt was emitted before close; never leak arbitrary payload.
        raise SystemExit(2) from None
