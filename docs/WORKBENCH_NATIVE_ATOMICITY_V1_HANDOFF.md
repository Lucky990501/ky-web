# Workbench Native atomicity and parent-chain handoff V1

## Scope and architecture

Tooling is a direct successor of `1e11e1a4835a465963ec5bd88a5ed40d4b300086`.
Application is separately a direct successor of d8a. Do not install the Tooling
checkout as an Application release. This document describes the implementation
and reproducible acceptance contract; final commit/tree and exact-pair results
are supplied by the post-commit handoff, not by a self-referential manifest.

The existing Native verifier now separates immutable security identity from
environment-owned historical state. PRIMARY retains its original protected
parent inputs. The isolated realm issues its own empty-cluster predecessor,
Synthetic state and pins through the formal Issuer, never PRIMARY row copies.
Both paths use the same Runtime quality, Admission, Admin and action validators.

Contracts:

- `ISOLATED_NATIVE_TEST_CONTEXT`, `NATIVE_ENVIRONMENT_PARENT_CONTRACT_V1`
- `INTEGRATED_RUNTIME_RELEASE_PAIR_V2` (isolated); V1 separate pair for PRIMARY
- `WECHAT_RUNTIME_TEST_NATIVE_SUCCESSOR_V2`
- `EXACT_TEST_ADMIN_SEPARATE_RELEASE_APPROVAL_V2`
- `NATIVE_PROTECTED_FORWARD_RECOVERY_V1`

Security includes exact App/Tool Source/Tree, full byte maps, root ownership,
Code Pins, environment and cluster identity, schema, policy and approval.
State includes only the environment's own Synthetic Tenant/User/Agent/Revision
and historical pins. No new permission system, recovery ledger or per-row
Witness was added. No migration, Skill, Secret backend or UI was changed.

## Atomicity closure

Application joins first configured Instance, Context, Task and Test in one short
repository transaction, before enqueue. The old split commit was the actual
`NATIVE_FIRST_INSTANCE_FORMAL_TEST_REQUIRED` cause. The Guard rule is preserved.
`Idempotency-Key` observational replay must reference an already admitted exact
Test; `finish_operation` emits no duplicate Admission. Recover still requires
exact abandoned-ticket/actor/Tenant/Agent/Revision/Source/Scope association and
root-owned dead-process proof, then atomically retains Admission and revokes the
owned membership. Admission never becomes current execution permission.

## Reproducible real Native entry

Only on an authorized disposable Linux/systemd host, with non-root UID/GID 1000
named `lucky`, PG **16.6**, Redis, and the application's existing Python runtime:

```sh
PYTHONDONTWRITEBYTECODE=1 python -B enterprise_agent_poc/scripts/run_native_atomicity_rehearsal.py \
  --repository /approved/local/repository/.git \
  --application-source <exact-app-commit> --application-tree <exact-app-tree> \
  --tooling-source <exact-tool-commit> --tooling-tree <exact-tool-tree> \
  --python /approved/venv/bin/python \
  --pg-bin /approved/postgresql-16.6/bin \
  --redis /approved/redis-server \
  --evidence-dir /new/absolute/evidence-directory --cases ABCDEFGR
```

Run as root; all service processes use UID 1000. This is an isolated rehearsal
bootstrap, NOT a PRIMARY installer. It requires exact direct parents, pristine
checkouts and a fixed loopback-only network namespace with no egress. It cannot
accept arbitrary database URLs. It uses only `/opt/enterprise-agent-native-isolated`
and `/etc/enterprise-agent-native-isolated-v1`, PG 56432, Redis 56479, API 28100,
MCP 28101, five uniquely named systemd units and a resource slice. No 80/443.

Each case formally issues a fresh independent contract; prior data/contracts
are archived after proving all service PIDs zero. Nothing is resealed or deleted.
The exact installed Tooling invokes these existing entries:

```text
prepare_exact_admin_authority.py
runtime_recovery_operator.py startup
run_exact_test_admin_lifecycle.py prepare --run-id <issued run UUID>
run_exact_test_admin_lifecycle.py grant --run-id <same UUID>
POST /api/v1/platform/agents/<exact Agent>/versions/<exact Revision>/test
runtime_recovery_operator.py recover
run_exact_test_admin_lifecycle.py status --run-id <same UUID>
```

`service.env`, Policy, pair and approval are root-owned and code-pinned. Generated
Synthetic login/session secrets stay in local protected files and are never
part of Git or exported evidence. `PYTHONDONTWRITEBYTECODE=1` and `-B` are mandatory.

## Native fault matrix

| Case | Actual injection |
|---|---|
| A | Block Instance INSERT, SIGKILL API: all initialization absent |
| B | Instance written but uncommitted, block Context INSERT, SIGKILL: all rolled back |
| C | Commit complete; Redis write paused before Manager; SIGKILL: queued Reservation retained |
| D | Task written but Test INSERT blocked; SIGKILL: entire initialization rolled back |
| E | Actual Worker running, block trace write; SIGKILL API, let real early Manager failure persist |
| F | Reservation committed; operation-finish Admission transaction blocked; SIGKILL API |
| G | F followed by SIGKILL protected Recover at admin transaction lock; retry original Recover |
| R | Two actual HTTP calls with same key: same Test/Task and exactly one Redis job |

Each case checks unchanged business records across recovery, exact ownership,
Admin=0, shared Native Guard, repeat Recover without extra audit/business rows,
revoked API rejection, PG stop/start, protected startup, API/MCP/Worker health and
final five PIDs=0. F/G/R observe real Redis processing held after revocation.
No PG trigger, direct Test/Task/PASS insert, Runtime Double or SQL cleanup is used.
PG locks select interruption boundaries only.

Actual negative Native loader tests use private mount namespaces to substitute
bad App Tree, Tool Source, Approval and Tenant inputs, plus Production environment.
Original authority bytes must remain unchanged and subsequently pass. Component
regressions independently check failed/no-evidence Publish denial, fake evidence,
cross-Tenant association, historical PREPARE/actions, Receipt V2, seeding and Config.

Before final exact-pair run, 473 component tests (429 inherited + 44 Application)
passed, and preliminary A–G/R rehearsals passed. These preliminary snapshots are
not the final approved pair. Final results must be read from the exact-pair JSON
matrix. Real Provider quality remains NOT_EXECUTED; real Publish/Enable and
CREATE_DRAFT are NOT_EXECUTED. A real early Runtime FAIL is not model PASS.

## Recovery and PRIMARY restrictions

The recovery operator first validates installed identity, unit files/environment
and data; it accepts failed/inactive services before recovery. It quiesces exact
processes, generates short-lived dead-operation proof, calls the existing Admin
Recover transaction, verifies Admin=0, and restarts through the compatible Guard.
It cannot switch source, restore old DB snapshots or start PRIMARY units.

06 must separately approve the new Test successor and both exact sources, attest
the current PRIMARY schema, current Config/Secret/history, original pins and
service startup/recovery binding, then qualify the PRIMARY adapter. The isolated
operator intentionally refuses PRIMARY: copying its approval into PRIMARY is
not an installation path. Current old d8a/old Guard cannot safely be restarted or
directly rolled back over new Runtime records. Use a formally approved compatible
forward recovery path preserving all evidence; never delete records to pass pins.

Local WSL disaster-recovery evidence is not full PRIMARY platform parity. Existing
pgvector version difference and prior debts remain. All active isolated resources
must be stopped, all Admin memberships zero; root-protected archived evidence is
retained for review, not counted as a running resource or installation authority.
