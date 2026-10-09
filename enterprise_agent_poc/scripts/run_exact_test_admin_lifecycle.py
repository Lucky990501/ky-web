"""Formal sealed Test operator entry. No default grant, model call or deploy."""
from pathlib import Path
import sys
# The formal launcher supplies the separately sealed Application on PYTHONPATH.
# Do not shadow it with the historical app copy in the Tooling checkout.
sys.path.append(str(Path(__file__).resolve().parents[1]))
import scripts
scripts.__path__ = [str(Path(__file__).resolve().parent), *scripts.__path__]


def main():
    import argparse
    import json
    import os
    import stat
    from scripts.exact_test_admin_lifecycle import (ExactTestAdmin, FixedLoopbackCapabilityProbe,
        Blocked, load_authority, load_dead_operation_proof, REGISTRATION, need)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'grant', 'revoke', 'recover', 'status'))
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    try:
        need(os.environ.get('APP_ENV') == 'test' and os.environ.get(REGISTRATION) == 'true'
            and sys.dont_write_bytecode and os.environ.get('PYTHONDONTWRITEBYTECODE') == '1',
            'EXACT_ADMIN_NATIVE_ENVIRONMENT_REQUIRED')
        scope = load_authority()
        need(args.run_id == scope['run_id'], 'EXACT_ADMIN_RUN_ID_MISMATCH')
        # Existing established Test runtime config only, after ROOT/source checks.
        # Never parse a project .env or use Production fallback credentials.
        from app.store import POCStore
        need(bool(os.environ.get('ENTERPRISE_POC_DATABASE_URL')), 'EXACT_ADMIN_TEST_DATABASE_CONFIG_REQUIRED')
        store = POCStore(os.environ['ENTERPRISE_POC_DATABASE_URL'])
        service = ExactTestAdmin(store, environment='test')
        call = {k: scope[k] for k in ('principal_id', 'tenant_id', 'run_id')}
        if args.action == 'prepare':
            from app.test_runtime_tooling import authority_root, ISOLATED_AUTHORITY
            token_file = Path('/run/enterprise-agent-native-isolated-v1/session.token' if
                authority_root() == ISOLATED_AUTHORITY else '/run/enterprise-agent-test-exact-admin-v1/session.token')
            for item in (token_file, *token_file.parents):
                info = item.lstat()
                need(not stat.S_ISLNK(info.st_mode) and info.st_uid in (0, 1000)
                    and not info.st_mode & 0o022, 'EXACT_ADMIN_SESSION_PATH_TRUST')
            info = token_file.stat()
            need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 4096,
                'EXACT_ADMIN_SESSION_FILE_PROTECTED')
            service.gate_probe = FixedLoopbackCapabilityProbe(token_file.read_text().strip())
            result = service.prepare(**call)
        elif args.action == 'grant': result = service.grant(**call)
        elif args.action == 'revoke': result = service.revoke(**call)
        elif args.action == 'recover': result = service.revoke(**call, dead_operation_proof=load_dead_operation_proof)
        else:
            from scripts.wechat_runtime_native_successor import NativeSuccessor
            result = NativeSuccessor().verify()
        print(json.dumps(result, sort_keys=True)); return 0
    except Exception as exc:
        # No DB URL, signed session, credential, SQL error or raw exception text.
        code = str(exc) if isinstance(exc, Blocked) else 'EXACT_ADMIN_NATIVE_OPERATION_BLOCKED'
        print(json.dumps({'status': 'BLOCKED', 'code': code})); return 2


if __name__ == '__main__':
    raise SystemExit(main())
