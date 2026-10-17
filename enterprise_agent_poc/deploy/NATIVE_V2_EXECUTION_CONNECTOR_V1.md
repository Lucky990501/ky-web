# Exact-pair Native V2 execution connector

This batch-specific CLI connects the existing Native V2 human archive to the
historical read-only snapshot/pg_dump/OpenSSL CMS backup pipeline and a real
private PG restore. It does not issue decisions or replace the seven pinned
release/recovery consumers. It cannot switch Production, restore its database,
write business records there, or authorize Provider calls.

## Scope and inputs

The business baseline is28948. The final candidate request must bind actual
Source/tree/parent/release_id, exact Archive SHA, raw and canonical Manifest
SHA and Native V2 binding SHA. The predecessor is only exactd906, using
`native_v2_record_issuer.PRIOR` including its original immutable pins. Its
historical562 metadata stays unchanged; runtime recovery remains d906.

Use the existing `native_v2_record_issuer.py` Owner and independent Root
capture, with the full request displayed. Original request/owner/root0600
archives are retained. No old V3 Policy, c602 failed Operation or consumed
claim is converted. This connector merely calls the unchanged issuer reader.

The request's `native_execution` contains profile
BATCH1_NATIVE_V2_EXECUTION_V1, helper_sha256 and recovery_helper_sha256 of the
final bytes, a fresh UUID operation_id, private_root=/tmp/ea-native-v2-UUID,
production_switch=false, production_database_write=false, provider_calls=0,
registry_packages=true, recipient_certificate with actual certificate SHA,
backup_pg_bin=/usr/lib/postgresql/16/bin,
restore_pg_bin=/opt/enterprise-agent-workbench-test/shared/runtime/postgresql-16.6/bin,
and the actual release_lock_identity=[device,inode] from Fresh readback.
No certificate, ciphertext, snapshot or Operation Hash is prefilled.

Recipient RSA3072 private key is created through the existing approved private
resource preparation on Test,0600, never exported/logged. Its matching X.509
public certificate is transferred by the existing approved channel; its actual
SHA is bound before Owner/Root confirmation. The native_execution fields are
part of the whole Native request Hash; adding them after signing is rejected.

Every phase checks the exact request, original decision hashes, Source,
expiry and revocation. Root maximum1hour, Owner maximum24hours, maintenance
maximum4hours anchored at actual Root acknowledgement and at least1hour
recovery reserve are retained. A phase must fit its explicit remaining-work
estimate. There is no repeated full4hour check at Policy entry.

## Existing operator execution sequence (future authorization required)

All connector commands use `python -I -B -S scripts/native_v2_execution.py
<ACTION> --authority <native archive/export> --request-sha256 <raw request SHA>`.

1. Production Root: `status` validates originals through unchanged issuer.
   The approved operator exports byte-identical request/owner/root as Root-owned
   read-only0444 copies under Root-only-managed parents on Test. During private
   work, the same operator refreshes Root-owned0444 live-status.json using the
   actual status output. It must be younger than60seconds and revocation must
   propagate immediately. This is not a new signing service or authority.
   Without that existing authorized transport/status maintenance, private real
   execution remains BLOCKED; Fixture cannot substitute for it.
2. Production Root: `install --archive <actual> --manifest <actual>` creates
   only a previously absent nonactive candidate directory, reads every byte,
   checks unchanged seven consumer pins and the original binding declaration.
   It never writes release-current or starts/stops business services.
3. Test private slice: `materialize-private --archive <candidate> --manifest
   <candidate> --predecessor-archive <d906> --predecessor-manifest <d906>` copies
   the exact two immutable artifacts to independent startup slots and verifies
   files. It does not install or change PRIMARY.
4. Production Root: `backup` checks d906 pointer and3active service CWDs,
   exact lock inode, takes existing release flock and an exported read-only
   repeatable snapshot, verifiesSchema015/PG16.14 and takes read-only table
   counts/digests. pg_dump streams directly into CMS AES256 with RSA3072;
   no plaintext dump file. Published Registry packages are independently
   hash/size checked and copied under a bounded whitelist. This phase never
   mutates business data. This proof covers the SQL snapshot and Registry;
   existing business object storage retention must also be verified before a
   real release, rather than claimed from SQL backup alone.
5. `transfer-context` returns actual ciphertext size/SHA, snapshot/backup
   receipt SHA, package inventory, Operation, Source and Root binding. Use
   existing approved file transfer for those files; a sender exit0 is not
   acceptance. Test `verify-transfer --context <file> --context-sha256 <actual>`
   requires every byte and both receipts then creates transfer.complete.json.
6. Test private slice: `restore` with the same context verifies receiver
   completion, both exact Source slots and15migration checksums before taking
   a new one-time restore claim. CMS decrypt streams into pg_restore with
   single transaction/exit-on-error. PG is a private-netns127.0.0.1:25432,
   never website/PRIMARY. SQL counts/digests and ledger must match. Published
   Registry package/cache mirrors retain original absolute paths through a
   private mount. Private PG stops before restore.complete.json is committed.
7. Test controller: `native_v2_recovery_probe.py rehearse` with the same
   authority/request, pinned recovery helper and completed restore runs real
   candidate/d906 API/MCP/Worker under existing Test systemd delegation.
   Private PG/Redis/network, own UUID units, readonly source and no Provider
   keys isolate them. It verifies actual PID/CWD/interpreter and MCP protocol;
   injects candidate bind failure before a private pointer change, then a
   running candidate API crash and exactd906 private recovery. SQL digests
   remain equal. Only its own UID/cgroup PIDs can be signalled. This is an
   isolated execution probe, not execution of Production release_switch.sh.

## Audit, failure and formal consumers

All write-once receipts0600 are fsynced/read back. Backup ciphertext0400;
private directories0700; private key0600. JSON input uses stdin/file paths,
no secret or large argument payload. Failed/consumed claims and incomplete
files are preserved, never cleared/retried; a new authorized scope/Operation
is required. Missing transfer/Hash/status/Registry/source/permissions stop
before restore claim. Pipeline EOF is explicitly closed and both child
exit statuses, timeout and authorization are checked; no SSHexit0 shortcut.

Fixture mode is accepted only under this user's marked temporary root, with
the original issuer FIXTURE_NOT_AUTHORITY and no Production host access.
Actual pg_dump, CMS, pg_restore, SQL comparison and systemd services still
execute, but all outputs stay fixture and cannot pass the real consumer.
Original raw failures remain available beside successful development runs.

The ten final reports remain the unchanged issuer's contract: full_pytest,
stage2, source_installation, api_native, mcp_native, worker_native,
exact_predecessor_on_schema015, pre_commit_failure_recovery,
post_commit_runtime_recovery, business_data_preserved. Each report binds
actual Source/tree, measured assertions, raw bytes/SHA, observed time and
skip-closure receipts. The real published report wrapper must be produced
from actual execution, not generated as PASS by this adapter. Fixture and
Test environment results are not Production-native measurements. Final
`.approval.v1.json`/`.evidence.v1.json` still require the unchanged Native
issuer, ten valid real reports and independent Release/Recovery decision.
No Seal or Production switch is executed during this repair.

## Admin login dependency

Existing normal workbench login POST /api/v1/auth/login establishes the
workbench_session cookie. Lucky must use an existing platform_admin account
in the ordinary browser UI; tenant-admin login is insufficient. Do not export
Cookie or request credentials in chat. Supported admin endpoints are
POST /api/v1/platform/skills/import and
POST /api/v1/platform/skill-versions/{id}/test then /publish.
The browser session must be proved before those authorized writes. Preserve
1.0.0 and existing customer bindings; Skill import/test/publish do not prove
Agent Binding, Runtime Test, Agent Publish, Instance Enable or customer UI.
