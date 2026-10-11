"""One-shot Yiming encrypted receiver; no database writes or authority issuance.

The unchanged 69696 producer authenticates /proc identity and both pipe ends.
Python's executable pin alone does not pin this script: -I, immutable root-owned
source/dependencies and the code SHA in the approved cmdline are mandatory too.
Only ciphertext is persisted. A pending envelope is NOT proof of DB commit.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import select
import stat
import subprocess
import sys
import time

OPERATION = '9af042d7-04c0-4253-9ee6-5bb429e81456'
TENANT = 'yiming-education'
EMAIL = 'ymjy@ky.com'
APPLICATION = '562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3'
PROVISION_SOURCE = '69696d598f62a57834720a685b728f3bdc87332d'
PROVISION_TREE = 'b852cd9aa01e5fec65efc35c8588f4451a5fd105'
CONFIG_SHA = '8d020e34ebb7857f59ee9f35330d3ccc1a5736d3acb84d282ac4632d43dcecd2'
APPROVAL = Path('/etc/enterprise-agent-customer-provisioning-v1') / (OPERATION+'.json')
PENDING = OPERATION+'.pending.rsa-oaep-sha256.bin'
CLAIM = OPERATION+'.receiver-claim.json'
PINS = {
    'scripts/provision_customer_tenant_v1.py': 'e45725e4307a8bc4a605a73ca1beb0ad2e1a1a467ed949f1e338d9612cc1fca3',
    'app/auth.py': '7bb862bdcd36bb02b39039e3de05db1560912f08de9fd8bb3777de535f7eec80',
    'app/store.py': 'a6c3718d928c0c7698af2f6ad121ac61173f1a5b37af2b8471e42e888b6008be',
    'app/product_store.py': '2c1142704f84d55b6961c6ae4f600a50bdfa6c6c6aaad6534f3541e328c88a5f',
}


class Blocked(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise Blocked(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        need(key not in value, 'DUPLICATE_JSON_KEY')
        value[key] = item
    return value


def parse_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError):
        raise Blocked('INVALID_JSON') from None


def root_trusted(path, *, regular=False):
    """Check every ancestor, not only the final file; no symlink trust escape."""
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, 'TRUSTED_PATH_REQUIRED')
    for part in reversed((path, *path.parents)):
        info = part.lstat()
        mode = stat.S_IMODE(info.st_mode)
        sticky_root_directory = (stat.S_ISDIR(info.st_mode) and mode & stat.S_ISVTX
                                 and part in (Path('/tmp'), Path('/var/tmp')))
        need(info.st_uid == 0 and info.st_gid == 0 and not stat.S_ISLNK(info.st_mode)
             and (not mode & 0o022 or sticky_root_directory), 'ROOT_TRUST_REQUIRED')
    if regular:
        need(stat.S_ISREG(path.stat().st_mode), 'REGULAR_FILE_REQUIRED')
    return path


def runtime_preflight(code_sha):
    need(sys.platform == 'linux' and os.geteuid() != 0, 'NONROOT_LINUX_REQUIRED')
    need(sys.flags.isolated == 1 and sys.dont_write_bytecode, 'ISOLATED_NO_PYC_PYTHON_REQUIRED')
    need(re.fullmatch('[0-9a-f]{64}', code_sha) is not None, 'CODE_PIN_REQUIRED')
    script = root_trusted(Path(__file__).absolute(), regular=True)
    need(sha(script.read_bytes()) == code_sha, 'RECEIVER_CODE_PIN_MISMATCH')
    root_trusted(Path('/proc/self/exe').resolve(), regular=True)
    spec = importlib.util.find_spec('cryptography')
    need(spec is not None and spec.origin is not None, 'CRYPTOGRAPHY_REQUIRED')
    root_trusted(Path(spec.origin).absolute(), regular=True)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)
    return script


def read_regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= limit, 'BOUNDED_REGULAR_FILE_REQUIRED')
        raw = os.read(fd, limit+1)
        need(len(raw) <= limit, 'BOUNDED_REGULAR_FILE_REQUIRED')
        return raw
    finally:
        os.close(fd)


def public_key(path, fingerprint):
    # The independent approver authenticates ownership of the public key and
    # binds its DER-SPKI fingerprint in the receiver's approved command line.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    need(re.fullmatch('[0-9a-f]{64}', fingerprint) is not None, 'APPROVED_PUBLIC_KEY_REQUIRED')
    raw = read_regular(path, 16384)
    need(b'PRIVATE KEY' not in raw, 'PUBLIC_KEY_ONLY')
    try:
        key = serialization.load_pem_public_key(raw)
    except (ValueError, TypeError):
        raise Blocked('PUBLIC_KEY_INVALID') from None
    need(isinstance(key, rsa.RSAPublicKey) and key.key_size in (3072, 4096), 'RSA3072_OR_4096_REQUIRED')
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    need(sha(der) == fingerprint, 'PUBLIC_KEY_FINGERPRINT_MISMATCH')
    return key


def pipe_identity(fd, access):
    import fcntl
    need(type(fd) is int and fd > 2, 'PRIVATE_PIPE_REQUIRED')
    info = os.fstat(fd)
    need(stat.S_ISFIFO(info.st_mode) and re.fullmatch(r'pipe:\[\d+\]', os.readlink('/proc/self/fd/'+str(fd)))
         and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == access, 'PRIVATE_PIPE_REQUIRED')
    for standard in (0, 1, 2):
        other = os.fstat(standard)
        need((other.st_dev, other.st_ino) != (info.st_dev, info.st_ino), 'STDIO_PIPE_ALIAS')
    return info.st_dev, info.st_ino


def output_directory(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, 'OUTPUT_PATH_REQUIRED')
    # Held FD defeats a replacement of the directory path after validation.
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    need(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700, 'PRIVATE_OUTPUT_DIRECTORY_REQUIRED')
    for parent in path.parents:
        info = parent.lstat()
        mode = stat.S_IMODE(info.st_mode)
        sticky = parent in (Path('/tmp'), Path('/var/tmp')) and info.st_uid == 0 and mode & stat.S_ISVTX
        need(not stat.S_ISLNK(info.st_mode) and info.st_uid in (0, os.geteuid())
             and (not mode & 0o022 or sticky), 'OUTPUT_ANCESTOR_TRUST_REQUIRED')
    return fd


def exclusive_write(dirfd, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dirfd)
    try:
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            need(written > 0, 'CIPHERTEXT_WRITE_FAILED')
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(dirfd)


def exact_packet(raw):
    need(raw.endswith(b'\n') and raw.count(b'\n') == 1 and len(raw) <= 4096, 'ONE_BOUNDED_FRAME_REQUIRED')
    packet = parse_json(raw)
    need(isinstance(packet, dict) and set(packet) == {'operation_id', 'tenant_id', 'admin_email', 'temporary_password'}, 'PACKET_SCHEMA')
    need((packet['operation_id'], packet['tenant_id'], packet['admin_email']) == (OPERATION, TENANT, EMAIL), 'EXACT_CUSTOMER_REQUIRED')
    need(isinstance(packet['temporary_password'], str)
         and re.fullmatch('[A-Za-z0-9_-]{43}', packet['temporary_password']) is not None, 'GENERATED_CREDENTIAL_REQUIRED')
    return packet


def receive(inbound, ack, key, dirfd, timeout=600):
    need(0 < timeout <= 600, 'BOUNDED_RECEIVE_WINDOW_REQUIRED')
    need(pipe_identity(inbound, os.O_RDONLY) != pipe_identity(ack, os.O_WRONLY), 'DISTINCT_PIPES_REQUIRED')
    # A claim survives failure. No automatic retry, overwrite or cleanup.
    exclusive_write(dirfd, CLAIM, json.dumps({'operation_id': OPERATION, 'state': 'RECEIVER_STARTED_NOT_COMMITTED'}).encode())
    need(bool(select.select([inbound], [], [], timeout)[0]), 'RECEIVE_TIMEOUT')
    raw = os.read(inbound, min(4096, os.fpathconf(inbound, 'PC_PIPE_BUF'))+1)
    exact_packet(raw)
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    need(len(raw) <= key.key_size//8-2*32-2, 'OAEP_PAYLOAD_TOO_LARGE')
    encrypted = key.encrypt(raw, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    exclusive_write(dirfd, PENDING, encrypted)
    acknowledgement = json.dumps({'operation_id': OPERATION, 'credential_sha256': sha(raw)}, separators=(',', ':')).encode()
    # Only after ciphertext file AND directory fsync. ACK is not DB commit.
    need(len(acknowledgement) <= os.fpathconf(ack, 'PC_PIPE_BUF'), 'ACK_SIZE')
    need(os.write(ack, acknowledgement) == len(acknowledgement), 'ACK_WRITE_FAILED')
    return {'status': 'CIPHERTEXT_PENDING', 'operation_id': OPERATION, 'ciphertext_sha256': sha(encrypted), 'database_commit': 'NOT_VERIFIED', 'customer_delivery': 'FORBIDDEN_PENDING_COMMIT_REVIEW'}


def process_binding(process, argv):
    peer = Path('/proc') / str(process.pid)
    wanted = b'\0'.join(os.fsencode(arg) for arg in argv)+b'\0'
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        need(process.poll() is None, 'RECEIVER_START_FAILED')
        raw = (peer/'cmdline').read_bytes()
        if raw == wanted:
            return {'mechanism': 'PRIVATE_ANONYMOUS_PIPE_V1', 'receiver_pid': process.pid,
                    'receiver_uid': os.geteuid(), 'receiver_executable_sha256': sha((peer/'exe').read_bytes()),
                    'receiver_cmdline_sha256': sha(raw)}
        time.sleep(.01)
    raise Blocked('RECEIVER_EXEC_IDENTITY_NOT_READY')


def spawn_receiver(script, code_sha, key_path, fingerprint, sink, *, python=None):
    incoming, credential_fd = os.pipe2(os.O_CLOEXEC)
    ack_fd, acknowledgement = os.pipe2(os.O_CLOEXEC)
    interpreter = Path(python or sys.executable).resolve()
    root_trusted(interpreter, regular=True)
    argv = [str(interpreter), '-I', '-B', str(script), 'receive',
            '--code-sha256', code_sha, '--public-key', str(key_path), '--public-key-fingerprint', fingerprint,
            '--output-dir', str(sink), '--credential-fd', str(incoming), '--ack-fd', str(acknowledgement)]
    try:
        process = subprocess.Popen(argv, pass_fds=(incoming, acknowledgement), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})
    finally:
        os.close(incoming)
        os.close(acknowledgement)
    return process, credential_fd, ack_fd, process_binding(process, argv)


def safe_emit(value):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)


def wait_approval(path, binding):
    need(path == APPROVAL, 'EXACT_APPROVAL_PATH_REQUIRED')
    deadline = time.monotonic()+300
    while time.monotonic() < deadline:
        if path.exists():
            root_trusted(path, regular=True)
            need(stat.S_IMODE(path.stat().st_mode) == 0o444, 'APPROVAL_MODE_REQUIRED')
            value = parse_json(read_regular(path, 65536))
            need(value['credential_handoff'] == binding and value['operator_uid'] == os.geteuid(), 'EXACT_RECEIVER_APPROVAL_REQUIRED')
            need(value['environment'] == 'production' and value['production_authority'] is True
                 and value['scope'] == 'CUSTOMER_TENANT_SECURE_PROVISIONING_V1', 'PRODUCTION_APPROVAL_REQUIRED')
            need(value['tooling'] == {'source': PROVISION_SOURCE, 'tree': PROVISION_TREE}
                 and value['application']['source'] == APPLICATION and value['code_pins'] == PINS, 'FROZEN_SOURCE_REQUIRED')
            wanted = {'tenant_id': TENANT, 'tenant_name': '易明教育', 'admin_email': EMAIL,
                      'admin_display_name': '易明管理员', 'operation_id': OPERATION, 'config_sha256': CONFIG_SHA}
            need(value['request'] == wanted and type(value['expires_at']) is int
                 and time.time() < value['expires_at'] <= time.time()+86400, 'EXACT_REQUEST_AND_EXPIRY_REQUIRED')
            return value
        time.sleep(.2)
    raise Blocked('FORMAL_APPROVAL_NOT_ISSUED')


def producer_argv(tool_root, approval, config, credential_fd, ack_fd, mode):
    return [str(Path(sys.executable).resolve()), '-B', str(tool_root/'scripts/provision_customer_tenant_v1.py'),
            '--tenant-id', TENANT, '--tenant-name', '易明教育', '--admin-email', EMAIL,
            '--admin-display-name', '易明管理员', '--operation-id', OPERATION,
            '--approval', str(approval), '--enterprise-config', str(config), '--'+mode,
            '--credential-fd', str(credential_fd), '--credential-ack-fd', str(ack_fd)]


def launch(args, script):
    tool_root = Path(args.provision_root)
    need(tool_root.is_absolute() and tool_root.resolve() == tool_root, 'EXACT_PROVISION_PATH_REQUIRED')
    for name, pin in PINS.items():
        # Producer runs nonroot and performs its own exact Git/code/Approval
        # checks. A root-owned Git checkout must not require a new global trust
        # bypass; operator-owned source is permitted only by explicit approval.
        path = tool_root/name
        need(path.resolve() == path and path.is_file(), 'EXACT_PROVISION_PATH_REQUIRED')
        need(sha(path.read_bytes()) == pin, 'PROVISION_CODE_PIN_MISMATCH')
    # No git safe.directory relaxation: the approved operator must already have
    # a legitimate read-only Git ownership context. The producer rechecks it.
    config = Path(args.enterprise_config)
    confirmed = parse_json(read_regular(config, 8192))
    need(confirmed == {'brand_name': '易明教育'}, 'CONFIRMED_CONFIG_REQUIRED')
    need(os.environ.get('APP_ENV') == 'production' and os.environ.get('ENTERPRISE_POC_DATABASE_URL', '').startswith(('postgres://', 'postgresql://')), 'APPROVED_DATABASE_ENVIRONMENT_REQUIRED')
    public_key(args.public_key, args.public_key_fingerprint)
    dirfd = output_directory(args.output_dir)
    os.close(dirfd)
    process, credential_fd, ack_fd, binding = spawn_receiver(script, args.code_sha256, args.public_key, args.public_key_fingerprint, args.output_dir)
    try:
        safe_emit({'status': 'AWAITING_INDEPENDENT_ROOT_APPROVAL', 'operation_id': OPERATION, 'credential_handoff': binding,
                   'public_key_spki_sha256': args.public_key_fingerprint, 'receiver_code_sha256': args.code_sha256})
        approved = wait_approval(Path(args.approval), binding)
        # DB password only in the pre-approved injection channel, never argv or
        # receiver environment. No user-site/PYTHONPATH/LD_PRELOAD inheritance.
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'APP_ENV': 'production',
               'ENTERPRISE_POC_DATABASE_URL': os.environ['ENTERPRISE_POC_DATABASE_URL'],
               'PYTHONDONTWRITEBYTECODE': '1', 'HOME': os.environ.get('HOME', '/nonexistent')}
        def invoke(mode):
            need(process.poll() is None, 'RECEIVER_NOT_ALIVE')
            run = subprocess.run(producer_argv(tool_root, args.approval, config, credential_fd, ack_fd, mode),
                                 cwd=tool_root, env=env, pass_fds=(credential_fd, ack_fd),
                                 stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
            # Never echo raw subprocess output, including exception details.
            value = parse_json(run.stdout)
            need(run.returncode == 0, 'FORMAL_PRODUCER_REJECTED')
            return value
        dry = invoke('dry-run')
        need(dry.get('status') == 'dry_run_pass' and dry.get('execution_ready') is True
             and dry.get('creates') == {'tenants': 1, 'users': 1, 'enterprise_configs': 1, 'credit_accounts': 1, 'tenant_agent_instances': 3}, 'FORMAL_DRY_RUN_NOT_READY')
        safe_emit({'status': 'FORMAL_DRY_RUN_PASS', 'operation_id': OPERATION, 'expected_insert_count': 7, 'database_writes': 0})
        if args.mode == 'dry-run':
            return
        # Same live Receiver PID and immutable Approval survive the dry-run.
        # Execute is an explicit private terminal action, never automatic.
        need(time.time()+120 < approved['expires_at'], 'EXECUTE_WINDOW_TOO_SHORT')
        with open('/dev/tty', 'r', encoding='ascii') as terminal:
            safe_emit({'status': 'WAITING_EXPLICIT_OPERATOR_CONFIRMATION', 'required_nonsecret_input': 'EXECUTE '+OPERATION})
            need(select.select([terminal], [], [], min(60, approved['expires_at']-time.time()))[0], 'OPERATOR_CONFIRMATION_TIMEOUT')
            need(terminal.readline(100).strip() == 'EXECUTE '+OPERATION, 'EXECUTE_NOT_CONFIRMED')
        result = invoke('execute')
        need(process.wait(timeout=5) == 0, 'RECEIVER_RESULT_NOT_CONFIRMED')
        receipt = Path(approved['receipt_dir'])/(OPERATION+'.jsonl')
        phases = [parse_json(line) for line in read_regular(receipt, 131072).splitlines()]
        need(result.get('status') == 'provisioned' and result.get('operation_id') == OPERATION
             and phases and phases[-1].get('phase') == 'COMMITTED'
             and phases[-1].get('operation_id') == OPERATION, 'COMMIT_RECEIPT_NOT_CONFIRMED')
        # Additional independent read-only DB reconciliation is a handoff gate;
        # the receiver never delivers or decrypts credentials automatically.
        safe_emit({'status': 'PRODUCER_COMMITTED_RECEIPT_OBSERVED', 'operation_id': OPERATION,
                   'customer_delivery': 'REQUIRES_INDEPENDENT_READONLY_DB_COMMIT_RECONCILIATION',
                   'ciphertext': PENDING, 'plaintext_disclosed': False})
    finally:
        os.close(credential_fd)
        os.close(ack_fd)
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2, '{"status":"BLOCKED","first_failure_point":"INVALID_ARGUMENTS"}\n')
    parser = Parser(description=__doc__)
    parser.add_argument('command', choices=('receive', 'launch'))
    for name in ('code-sha256', 'public-key', 'public-key-fingerprint', 'output-dir'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--credential-fd', type=int)
    parser.add_argument('--ack-fd', type=int)
    parser.add_argument('--provision-root')
    parser.add_argument('--approval', default=str(APPROVAL))
    parser.add_argument('--enterprise-config')
    parser.add_argument('--mode', choices=('dry-run', 'execute'), default='dry-run')
    args = parser.parse_args(argv)
    try:
        script = runtime_preflight(args.code_sha256)
        if args.command == 'launch':
            need(args.provision_root is not None and args.enterprise_config is not None, 'LAUNCH_INPUTS_REQUIRED')
            launch(args, script)
        else:
            key = public_key(args.public_key, args.public_key_fingerprint)
            dirfd = output_directory(args.output_dir)
            try:
                receive(args.credential_fd, args.ack_fd, key, dirfd)
            finally:
                os.close(dirfd)
        return 0
    except Exception as error:
        # Only locally defined fixed error codes, never key/input/DB exception text.
        safe_emit({'status': 'BLOCKED', 'first_failure_point': str(error) if isinstance(error, Blocked) else 'RECEIVER_IO_OR_PROTOCOL_FAILURE'})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
