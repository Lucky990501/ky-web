"""Read-only, approved-schema rollback gate; NOT migrate.status/up.

Trust comes from the reviewed declaration shipped with controlled tooling,
not a caller-supplied directory, manifest, checksum, or future-version range.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

ROOT = Path(__file__).absolute().parents[1]
BASE = Path('/opt/enterprise-agent-workbench')
sys.path.insert(0, str(ROOT))
HASH = re.compile(r'[0-9a-f]{64}')
COMMIT = re.compile(r'[0-9a-f]{40}')
RELEASE = re.compile(r'[A-Za-z0-9._-]+')


class RollbackBlocked(ValueError):
    """Only non-sensitive, constant check names may be exposed."""


def require(condition, check):
    if not condition:
        raise RollbackBlocked(check)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def unique_object(pairs):
    obj = {}
    for k, v in pairs:
        require(k not in obj, 'duplicate_json_key')
        obj[k] = v
    return obj


def read_json(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size < 4_000_000, 'json_file_identity')
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)


def migration_lock(items):
    require(isinstance(items, list) and bool(items), 'migration_lock')
    for i, item in enumerate(items, 1):
        require(set(item) == {'version', 'filename', 'canonical_sha256'}, 'migration_lock_fields')
        require(item['version'] == f'{i:03}' and isinstance(item['filename'], str)
                and re.fullmatch(item['version'] + r'_[A-Za-z0-9_]+\.sql', item['filename'])
                and HASH.fullmatch(item['canonical_sha256']), 'migration_lock_sequence')
    return items


def release_identity(base, rid, expected_commit, expected=None):
    require(isinstance(rid, str) and RELEASE.fullmatch(rid) and rid not in {'.', '..'}
            and COMMIT.fullmatch(expected_commit), 'release_identity_input')
    directory = base / 'releases' / rid
    source = directory / 'enterprise_agent_poc'
    require(directory.resolve() == directory and source.resolve() == source and source.is_dir(), 'controlled_release_path')
    # Settings' legacy secret loader also checks PROJECT_ROOT.parent/.env.
    # Reject that unmanifested input without opening it before any app import.
    require(not (directory / '.env').exists() and not (directory / '.env').is_symlink(), 'unapproved_environment_file')
    manifest = read_json(directory / f'{rid}.manifest.json')
    require(set(manifest) == {'release_id', 'source_commit', 'archive_sha256', 'selected_files', 'selected_file_count', 'build_platform'}, 'manifest_fields')
    require(manifest['release_id'] == rid and manifest['source_commit'] == expected_commit
            and HASH.fullmatch(manifest['archive_sha256']), 'manifest_identity')
    if expected is not None:
        require(manifest['archive_sha256'] == expected['archive_sha256']
                and digest(manifest) == expected['manifest_sha256'], 'approved_manifest_identity')
    files = manifest['selected_files']
    require(isinstance(files, list) and files and all(isinstance(f, str) for f in files)
            and len(set(files)) == len(files) and type(manifest['selected_file_count']) is int
            and len(files) == manifest['selected_file_count'], 'manifest_file_set')
    archive = directory / f'{rid}.tar.gz'
    require(archive.is_file() and not archive.is_symlink() and archive.stat().st_size < 512_000_000, 'archive_identity')
    require(hashlib.sha256(archive.read_bytes()).hexdigest() == manifest['archive_sha256'], 'archive_checksum')
    from app.bundled_skills import forbidden_path, secret_content
    with tarfile.open(archive) as tar:
        require(tar.pax_headers.get('comment') == expected_commit, 'archive_source_commit')
        members = tar.getmembers()
        require(len({m.name for m in members}) == len(members), 'duplicate_archive_member')
        for member in members:
            p = PurePosixPath(member.name)
            require(not p.is_absolute() and '..' not in p.parts and '\\' not in member.name
                    and p.parts[0] == 'enterprise_agent_poc' and (member.isfile() or member.isdir())
                    and not forbidden_path(member.name), 'archive_safe_paths')
        require(set(m.name for m in members if m.isfile()) == set(files), 'archive_file_set')
        for member in members:
            if member.isfile():
                path = directory / member.name
                content = tar.extractfile(member).read()
                require(path.is_file() and not path.is_symlink() and path.resolve() == path
                        and path.read_bytes() == content and not secret_content(content)
                        and path.stat().st_mode & 0o111 == member.mode & 0o111, 'release_file_identity')
    for path in source.rglob('*'):
        require(not path.is_symlink(), 'release_symlink')
        if path.is_file():
            name = path.relative_to(directory).as_posix()
            machine_cache = '__pycache__' in path.relative_to(source).parts and path.suffix == '.pyc'
            require(name in files or machine_cache, 'unmanifested_release_file')
    return source, manifest


def check_sources(directory, lock):
    from scripts import migrate
    files = migrate.migration_files(directory / 'migrations' / 'postgres')
    require([f.name for f in files] == [x['filename'] for x in lock], 'source_migration_set')
    result = []
    for path, item in zip(files, lock):
        checksums = migrate.migration_checksums(path)
        require(checksums['canonical_checksum'] == item['canonical_sha256'], 'source_migration_checksum')
        result.append({'version': item['version'], 'name': item['filename'][4:], **checksums})
    return result


def epoch_contract(declaration):
    contract = declaration['epoch_contract']
    require(set(contract) == {'version','scope','contract_id','schema_migrations','epochs','schema_compatibility_evidence'}
            and contract['version'] == 1 and contract['scope'] == 'agent_data_contract'
            and contract['contract_id'] == 'agent-productized-v1', 'epoch_contract_identity')
    lock = migration_lock(contract['schema_migrations'])
    require(lock[:len(declaration['baseline']['migrations'])] == declaration['baseline']['migrations'], 'epoch_schema_baseline')
    epochs = contract['epochs']
    require(isinstance(epochs, list) and len(epochs) == 2, 'epoch_declaration')
    for item, name, rank, scope in zip(epochs, ('legacy_v1','productized_v1'), (1,2), ('legacy_only','productized_v1')):
        require(set(item) == {'epoch','epoch_rank','data_scope','minimum_target','allowed_targets'}
                and item['epoch'] == name and type(item['epoch_rank']) is int and item['epoch_rank'] == rank
                and item['data_scope'] == scope and item['minimum_target'] in item['allowed_targets'], 'epoch_declaration')
        for reference in item['allowed_targets']:
            require(set(reference) == {'release_id','source_commit'} and RELEASE.fullmatch(reference['release_id'])
                    and COMMIT.fullmatch(reference['source_commit']), 'epoch_target_reference')
            require(sum(t['release_id'] == reference['release_id'] and t['source_commit'] == reference['source_commit']
                        for t in declaration['approved_targets']) == 1, 'epoch_target_unapproved')
    return contract


def read_history(database_url, postgres_major, *, plan=False, target_id=None, target_commit=None, contract=None):
    from psycopg import connect
    from psycopg.rows import dict_row
    require(database_url.startswith(('postgresql://', 'postgres://')), 'postgresql_required')
    with connect(database_url, row_factory=dict_row, options='-c default_transaction_read_only=on') as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        require(conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on', 'read_only_required')
        require(int(conn.execute('SHOW server_version_num').fetchone()['server_version_num']) // 10000 == postgres_major, 'evidence_postgresql_major')
        rows = conn.execute('SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version').fetchall()
        from scripts.compatibility_epoch import read_state, has_productized_data, EpochBlocked
        applied = {r['version'] for r in rows}
        try:
            # Only the pre-migration PLAN can evaluate an absent 011 table.
            # Actual rollback / advance ALWAYS requires the persistent singleton.
            exists = conn.execute("SELECT to_regclass('public.platform_compatibility_state') AS name").fetchone()['name']
            if not exists and plan and '011' not in applied:
                has_v2 = has_productized_data(conn, include_execution='009' in applied) if '008' in applied else False
                state = {'epoch': 'productized_v1' if has_v2 else 'legacy_v1', 'epoch_rank': 2 if has_v2 else 1}
                source = 'pending_011_initialization_plan'
            else:
                state = read_state(conn)
                if state['epoch'] == 'legacy_v1':
                    require(not has_productized_data(conn), 'legacy_epoch_productized_data_conflict')
                source = 'platform_compatibility_state'
        except EpochBlocked as exc:
            raise RollbackBlocked(str(exc)) from None
        epoch = next(e for e in contract['epochs'] if e['epoch'] == state['epoch'])
        reference = {'release_id': target_id, 'source_commit': target_commit}
        if reference == contract['epochs'][0]['minimum_target'] and state['epoch_rank'] > 1:
            raise RollbackBlocked('rollback_target_below_data_compatibility_floor')
        require(reference in epoch['allowed_targets'], 'target_not_approved_for_compatibility_epoch')
        return rows, state, source


def check_history(rows, items, *, plan=False):
    from scripts import migrate
    require(bool(rows) and len({r['version'] for r in rows}) == len(rows), 'history_duplicate_or_empty')
    versions = [r['version'] for r in rows]
    expected = [x['version'] for x in items]
    require(versions == (expected[:len(rows)] if plan else expected), 'history_missing_unknown_or_order')
    previous = None
    for row, item in zip(rows, items):
        require(row['name'] == item['name'], 'history_filename')
        require(migrate.compatibility_status(row['checksum'], item) in
                {migrate.EXACT_MATCH, migrate.LEGACY_LINE_ENDING_COMPATIBLE}, 'history_checksum')
        stamp = row['applied_at']
        require(stamp is not None and (previous is None or stamp >= previous), 'history_applied_order')
        previous = stamp


def verify(base, trusted_root, target_id, target_commit, *, plan=False, database_url=None):
    base = base.absolute()
    require(base.resolve() == base and trusted_root.resolve() == trusted_root
            and trusted_root.parent.parent == base / 'releases', 'trusted_controlled_path')
    own_manifest = read_json(trusted_root.parent / f'{trusted_root.parent.name}.manifest.json')
    own_source, _ = release_identity(base, trusted_root.parent.name, own_manifest['source_commit'])
    require(own_source == trusted_root, 'trusted_release_identity')
    declaration = read_json(trusted_root / 'deploy' / 'rollback_compatibility.json')
    require(set(declaration) == {'schema_version', 'baseline', 'approved_targets','epoch_contract'}
            and type(declaration['schema_version']) is int and declaration['schema_version'] == 2, 'declaration_schema')
    baseline = declaration['baseline']
    require(set(baseline) == {'id', 'source_commit', 'migrations'} and COMMIT.fullmatch(baseline['source_commit'])
            and isinstance(baseline['id'], str) and bool(baseline['id']), 'baseline_identity')
    lock = migration_lock(baseline['migrations'])
    contract = epoch_contract(declaration)
    schema_lock = contract['schema_migrations']
    # Read epoch before opening or validating the requested target Artifact.
    if database_url is None:
        from scripts.migrate import settings
        database_url = settings.database_url
    rows, state, epoch_source = read_history(database_url, 16, plan=plan, target_id=target_id,
                                           target_commit=target_commit, contract=contract)
    targets = declaration['approved_targets']
    require(isinstance(targets, list) and len({t['release_id'] for t in targets}) == len(targets), 'approved_target_set')
    matching = [t for t in targets if t['release_id'] == target_id and t['source_commit'] == target_commit]
    require(len(matching) == 1, 'unapproved_target')
    target = matching[0]
    require(set(target) == {'release_id', 'source_commit', 'archive_sha256', 'manifest_sha256', 'known_migrations', 'compatibility_evidence'}
            and HASH.fullmatch(target['archive_sha256']) and HASH.fullmatch(target['manifest_sha256']), 'target_declaration')
    target_lock = migration_lock(target['known_migrations'])
    require(target_lock == schema_lock[:len(target_lock)], 'target_baseline_not_approved_subset')
    evidence = target['compatibility_evidence']
    require(set(evidence) == {'version', 'baseline_id', 'report_sha256', 'postgres_major', 'checks', 'scope', 'schema_baseline_fingerprint', 'target_identity_fingerprint', 'data_scope'}
            and isinstance(evidence['version'], str) and evidence['version'] and evidence['baseline_id'] == baseline['id']
            and evidence['schema_baseline_fingerprint'] == digest(lock)
            and evidence['target_identity_fingerprint'] == digest({k: v for k, v in target.items() if k != 'compatibility_evidence'})
            and HASH.fullmatch(evidence['report_sha256']) and type(evidence['postgres_major']) is int and evidence['postgres_major'] >= 11
            and evidence['data_scope'] in {'legacy_only','productized_v1'}
            and evidence['checks'] == dict.fromkeys(('api', 'mcp', 'worker', 'legacy_minimal_run'), 'PASS')
            and isinstance(evidence['scope'], str) and evidence['scope'], 'compatibility_evidence')
    target_root, _ = release_identity(base, target_id, target_commit, target)
    approval = contract['schema_compatibility_evidence']
    require(set(approval) == {'status','version','report_sha256','postgres_major','checks'}
            and approval['status'] == 'PASS' and approval['postgres_major'] == 16
            and isinstance(approval['version'],str) and approval['version']
            and HASH.fullmatch(approval['report_sha256']) and approval['report_sha256'] != '0'*64
            and approval['checks'] == dict.fromkeys(('legacy_011','productized_011','restart','control_plane_rollback','reenable'),'PASS'),
            'epoch_schema_compatibility_evidence')
    if state['epoch'] == 'productized_v1':
        require(evidence['data_scope'] == 'productized_v1', 'target_data_scope_evidence')
    trusted_items = check_sources(trusted_root, schema_lock)
    target_items = check_sources(target_root, target_lock)
    check_history(rows, trusted_items, plan=plan)
    require(len(rows) >= len(target_items), 'target_required_migrations_missing')
    check_history(rows[:len(target_items)], target_items)
    return {'status': 'rollback_plan_passed' if plan else 'rollback_preflight_passed',
            'read_only': True, 'target_release_id': target_id, 'target_source_commit': target_commit,
            'schema_baseline_id': baseline['id'], 'schema_baseline_source_commit': baseline['source_commit'],
            'schema_baseline_fingerprint': digest(lock), 'applied_versions': [r['version'] for r in rows],
            'target_known_versions': [r['version'] for r in target_items], 'evidence_version': evidence['version'],
            'compatibility_epoch': state['epoch'], 'epoch_rank': state['epoch_rank'], 'epoch_source': epoch_source,
            'epoch_schema_fingerprint': digest(schema_lock),
            'old_runner_invoked': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-release-id', required=True)
    parser.add_argument('--target-source-commit', required=True)
    parser.add_argument('--check-plan', action='store_true', help='Pre-switch only: validate existing applied prefix and the complete approved future plan.')
    args = parser.parse_args()
    try:
        result = verify(BASE, ROOT, args.target_release_id, args.target_source_commit, plan=args.check_plan)
        print(json.dumps(result)); return 0
    except RollbackBlocked as exc:
        print(json.dumps({'status': 'BLOCKED', 'check': str(exc), 'gate': 'trusted_rollback_preflight'})); return 2
    except Exception:
        # Database and configuration exceptions can carry DSNs or credentials.
        print(json.dumps({'status': 'BLOCKED', 'check': 'rollback_preflight_input_or_io', 'gate': 'trusted_rollback_preflight'})); return 2


if __name__ == '__main__':
    raise SystemExit(main())
