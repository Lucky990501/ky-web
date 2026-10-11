"""New real encrypted Receiver + unchanged 69696 producer on independent PG.

All keys below are ephemeral SYNTHETIC TEST KEYS kept in memory, never customer
keys or exported private PEM. The fixed protocol IDs occur only in random marked
socket-only databases. These records are not Production customer accounts.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import pytest

from scripts import yiming_credential_receiver_v1 as receiver
from scripts import provision_customer_tenant_v1 as tool
from app.auth import verify_password
from test_customer_tenant_provisioning_postgres import pg, approval, counts, no_external_http

pytestmark = pytest.mark.skipif(not os.environ.get('YIMING_ROOT_FIXTURE_SCRIPT'), reason='root-owned isolated Receiver code fixture required')


@pytest.fixture(scope='module')
def ephemeral_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


@contextmanager
def actual_receiver(tmp, private, *, key_fingerprint=None, preexisting_ciphertext=False):
    target = tmp/'private-receiver'
    target.mkdir(mode=0o700)
    public = target/'synthetic-public.pem'
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    der = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    fingerprint = key_fingerprint or receiver.sha(der)
    script = Path(os.environ['YIMING_ROOT_FIXTURE_SCRIPT'])
    if preexisting_ciphertext:
        (target/receiver.PENDING).write_bytes(b'preexisting ciphertext sentinel')
    process, fd, ack_fd, binding = receiver.spawn_receiver(script, receiver.sha(script.read_bytes()), public, fingerprint, target, python='/usr/bin/python3')
    try:
        yield type('R', (), {'process': process, 'fd': fd, 'ack_fd': ack_fd, 'binding': binding, 'target': target})()
    finally:
        os.close(fd)
        os.close(ack_fd)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def packet(password='z'*43, **changes):
    value = dict(operation_id=receiver.OPERATION, tenant_id=receiver.TENANT, admin_email=receiver.EMAIL, temporary_password=password)
    value.update(changes)
    return json.dumps(value, separators=(',', ':')).encode()+b'\n'


def decrypt(target, private):
    return private.decrypt((target/receiver.PENDING).read_bytes(), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))


def fixed_approval(pg, target):
    return approval(pg, target.binding, tenant=receiver.TENANT, email=receiver.EMAIL, operation=receiver.OPERATION)


def test_real_encrypted_receiver_correct_ACK(tmp_path, ephemeral_key):
    with actual_receiver(tmp_path, ephemeral_key) as target:
        raw = packet()
        tool.handoff_ready(target.fd, target.ack_fd, target.binding)
        os.write(target.fd, raw)
        assert select.select([target.ack_fd], [], [], 5)[0]
        ack = json.loads(os.read(target.ack_fd, 4096))
        assert ack == {'operation_id': receiver.OPERATION, 'credential_sha256': receiver.sha(raw)}
        assert target.process.wait(timeout=5) == 0
        assert receiver.sha(decrypt(target.target, ephemeral_key)) == receiver.sha(raw)
        ciphertext = target.target/receiver.PENDING
        assert ciphertext.stat().st_mode & 0o077 == 0
        assert b'z'*43 not in ciphertext.read_bytes()
        assert not any(b'z'*43 in p.read_bytes() for p in target.target.iterdir())


@pytest.mark.parametrize('change', [{'operation_id': '00000000-0000-0000-0000-000000000000'}, {'tenant_id': 'another-tenant'}, {'admin_email': 'other@isolated.invalid'}, {'temporary_password': 'short'}])
def test_wrong_exact_payload_no_ACK(tmp_path, ephemeral_key, change):
    with actual_receiver(tmp_path, ephemeral_key) as target:
        os.write(target.fd, packet(**change))
        assert target.process.wait(timeout=5) == 2
        assert os.read(target.ack_fd, 4096) == b''
        assert not (target.target/receiver.PENDING).exists()


@pytest.mark.parametrize('raw', [packet()+packet(), packet().rstrip(), b'{}\n', b'{"operation_id":"x","operation_id":"x"}\n', b'x'*4097])
def test_bad_or_multiple_frame_reject(tmp_path, ephemeral_key, raw):
    with actual_receiver(tmp_path, ephemeral_key) as target:
        os.write(target.fd, raw)
        assert target.process.wait(timeout=5) == 2
        assert os.read(target.ack_fd, 4096) == b''
        assert not (target.target/receiver.PENDING).exists()


def test_RSA_public_key_substitution_reject(tmp_path, ephemeral_key):
    with actual_receiver(tmp_path, ephemeral_key, key_fingerprint='0'*64) as target:
        assert target.process.wait(timeout=5) == 2
        assert os.read(target.ack_fd, 4096) == b''
        assert not (target.target/receiver.PENDING).exists()


def test_ciphertext_write_failure_no_ACK(tmp_path, ephemeral_key):
    with actual_receiver(tmp_path, ephemeral_key, preexisting_ciphertext=True) as target:
        os.write(target.fd, packet())
        assert target.process.wait(timeout=5) == 2
        assert os.read(target.ack_fd, 4096) == b''
        assert (target.target/receiver.PENDING).read_bytes() == b'preexisting ciphertext sentinel'


@pytest.mark.parametrize('field', ['receiver_pid', 'receiver_uid', 'receiver_executable_sha256', 'receiver_cmdline_sha256'])
def test_actual_receiver_wrong_identity_reject(tmp_path, ephemeral_key, field):
    with actual_receiver(tmp_path, ephemeral_key) as target:
        bad = dict(target.binding)
        bad[field] = 0 if field in ('receiver_pid', 'receiver_uid') else '0'*64
        with pytest.raises(tool.ProvisionBlocked, match='CREDENTIAL_HANDOFF_BLOCKED'):
            tool.handoff_ready(target.fd, target.ack_fd, bad)
        assert not (target.target/receiver.PENDING).exists()


def test_actual_receiver_exit_before_request_reject(tmp_path, ephemeral_key):
    with actual_receiver(tmp_path, ephemeral_key) as target:
        target.process.terminate()
        target.process.wait(timeout=5)
        with pytest.raises(tool.ProvisionBlocked, match='CREDENTIAL_HANDOFF_BLOCKED'):
            tool.handoff_ready(target.fd, target.ack_fd, target.binding)


def test_real_PG_new_receiver_commit_and_duplicate_reject(pg, ephemeral_key, capsys):
    with actual_receiver(pg.tmp, ephemeral_key) as target:
        a = fixed_approval(pg, target)
        result = tool.provision(pg.store, a.path, a.wanted, a.config, execute=True, credential_fd=target.fd, credential_ack_fd=target.ack_fd)
        assert result['status'] == 'provisioned'
        assert target.process.wait(timeout=5) == 0
        raw = decrypt(target.target, ephemeral_key)
        secret = json.loads(raw)['temporary_password']
        saved = pg.product.user_by_email(receiver.EMAIL)
        assert verify_password(secret, saved['password_hash'])
        assert counts(pg, receiver.TENANT) == dict(tenants=1, enterprise_configs=1, credit_accounts=1, users=1, tenant_agent_instances=3)
        with pytest.raises(tool.ProvisionBlocked):
            tool.provision(pg.store, a.path, a.wanted, a.config, execute=True, credential_fd=target.fd, credential_ack_fd=target.ack_fd)
        assert counts(pg, receiver.TENANT)['users'] == 1
        assert secret not in capsys.readouterr().out
        assert all(secret.encode() not in p.read_bytes() for p in target.target.iterdir())
        assert secret not in (pg.receipts/(receiver.OPERATION+'.jsonl')).read_text()


def test_real_PG_failure_rolls_back_no_credential_delivery(pg, ephemeral_key):
    with pg.store.connection() as conn:
        conn.execute("CREATE FUNCTION isolated_reject_yiming() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'isolated failure'; END $$")
        conn.execute('CREATE TRIGGER isolated_reject BEFORE INSERT ON users FOR EACH ROW EXECUTE FUNCTION isolated_reject_yiming()')
    with actual_receiver(pg.tmp, ephemeral_key) as target:
        a = fixed_approval(pg, target)
        with pytest.raises(Exception):
            tool.provision(pg.store, a.path, a.wanted, a.config, execute=True, credential_fd=target.fd, credential_ack_fd=target.ack_fd)
        assert counts(pg, receiver.TENANT) == dict.fromkeys(tool.TABLES, 0)
        assert not (target.target/receiver.PENDING).exists()
        assert not select.select([target.ack_fd], [], [], .05)[0]


def test_real_PG_commit_failure_after_ACK_keeps_pending_not_delivered(pg, ephemeral_key, monkeypatch):
    # Genuine deferred PG constraint trigger fails COMMIT after real ACK. This
    # is not a mock transaction return value and no row is manually committed.
    with pg.store.connection() as conn:
        conn.execute("CREATE FUNCTION isolated_reject_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'isolated deferred commit failure'; END $$")
        conn.execute('CREATE CONSTRAINT TRIGGER isolated_commit_reject AFTER INSERT ON users DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION isolated_reject_commit()')
    with actual_receiver(pg.tmp, ephemeral_key) as target:
        a = fixed_approval(pg, target)
        with pytest.raises(Exception):
            tool.provision(pg.store, a.path, a.wanted, a.config, execute=True, credential_fd=target.fd, credential_ack_fd=target.ack_fd)
        assert target.process.wait(timeout=5) == 0
        assert counts(pg, receiver.TENANT) == dict.fromkeys(tool.TABLES, 0)
        assert (target.target/receiver.PENDING).exists()
        assert (pg.receipts/(receiver.OPERATION+'.jsonl')).read_text().count('NOT_CONFIRMED') == 1
        assert not any('delivered' in p.name or 'committed' in p.name for p in target.target.iterdir())


def test_real_PG_sink_failure_rolls_back(pg, ephemeral_key):
    with actual_receiver(pg.tmp, ephemeral_key, preexisting_ciphertext=True) as target:
        a = fixed_approval(pg, target)
        with pytest.raises(tool.ProvisionBlocked, match='CREDENTIAL_HANDOFF_BLOCKED'):
            tool.provision(pg.store, a.path, a.wanted, a.config, execute=True, credential_fd=target.fd, credential_ack_fd=target.ack_fd)
        assert counts(pg, receiver.TENANT) == dict.fromkeys(tool.TABLES, 0)


def test_real_PG_actual_receiver_ACK_timeout_rolls_back(pg, ephemeral_key):
    with actual_receiver(pg.tmp, ephemeral_key) as target:
        a = fixed_approval(pg, target)
        # Freeze the genuine Receiver, leaving its actual peer pipes/identity
        # intact. The unchanged producer must time out without accepting ACK.
        os.kill(target.process.pid, signal.SIGSTOP)
        with pytest.raises(tool.ProvisionBlocked, match='CREDENTIAL_HANDOFF_BLOCKED'):
            tool.provision(pg.store, a.path, a.wanted, a.config, execute=True, credential_fd=target.fd, credential_ack_fd=target.ack_fd)
        assert counts(pg, receiver.TENANT) == dict.fromkeys(tool.TABLES, 0)
        assert not (target.target/receiver.PENDING).exists()


def test_real_PG_actual_ACK_digest_disagreement_rolls_back(pg, ephemeral_key, monkeypatch):
    with actual_receiver(pg.tmp, ephemeral_key) as target:
        a = fixed_approval(pg, target)
        original_sha = hashlib.sha256
        class WrongPacketDigest:
            def hexdigest(self):
                return '0'*64
        def disagreed_digest(raw=b''):
            # Inject only the sender's expected packet digest. Identity, file,
            # DB and audit hashing remain real, Receiver executes unmodified.
            if b'"temporary_password"' in raw and raw.endswith(b'\n'):
                return WrongPacketDigest()
            return original_sha(raw)
        monkeypatch.setattr(tool.hashlib, 'sha256', disagreed_digest)
        with pytest.raises(tool.ProvisionBlocked, match='CREDENTIAL_HANDOFF_BLOCKED'):
            tool.provision(pg.store, a.path, a.wanted, a.config, execute=True, credential_fd=target.fd, credential_ack_fd=target.ack_fd)
        assert target.process.wait(timeout=5) == 0
        assert counts(pg, receiver.TENANT) == dict.fromkeys(tool.TABLES, 0)
        assert (target.target/receiver.PENDING).exists()


def test_receiver_core_dump_disabled(tmp_path, ephemeral_key):
    with actual_receiver(tmp_path, ephemeral_key) as target:
        # Wait for the actual child runtime preflight, not merely exec().
        deadline = time.monotonic()+5
        while not (target.target/receiver.CLAIM).exists() and time.monotonic() < deadline:
            time.sleep(.01)
        limits = Path('/proc')/str(target.process.pid)/'limits'
        core = next(line for line in limits.read_text().splitlines() if line.startswith('Max core file size'))
        assert core.split()[-3:-1] == ['0', '0']


def test_duplicate_receiver_never_overwrites_ciphertext(tmp_path, ephemeral_key):
    with actual_receiver(tmp_path, ephemeral_key) as target:
        os.write(target.fd, packet())
        assert target.process.wait(timeout=5) == 0
        original = (target.target/receiver.PENDING).read_bytes()
        public = target.target/'synthetic-public.pem'
        fingerprint = receiver.sha(ephemeral_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))
        script = Path(os.environ['YIMING_ROOT_FIXTURE_SCRIPT'])
        process, fd, ack, binding = receiver.spawn_receiver(script, receiver.sha(script.read_bytes()), public, fingerprint, target.target, python='/usr/bin/python3')
        try:
            assert process.wait(timeout=5) == 2
            assert os.read(ack, 4096) == b''
            assert (target.target/receiver.PENDING).read_bytes() == original
        finally:
            os.close(fd)
            os.close(ack)


@pytest.mark.parametrize('bad', [0, 1, 2])
def test_stdio_FD_reject(bad):
    with pytest.raises(receiver.Blocked, match='PRIVATE_PIPE_REQUIRED'):
        receiver.pipe_identity(bad, os.O_RDONLY)


def test_untrusted_receiver_source_path_reject(tmp_path):
    with pytest.raises(receiver.Blocked, match='ROOT_TRUST_REQUIRED'):
        receiver.root_trusted(tmp_path)


def test_output_symlink_reject(tmp_path):
    private = tmp_path/'out'
    private.mkdir(mode=0o700)
    link = tmp_path/'link'
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(receiver.Blocked, match='OUTPUT_PATH_REQUIRED'):
        receiver.output_directory(link)
