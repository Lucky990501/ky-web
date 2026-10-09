# Formal PRIMARY forward recovery binding V1

This is a source candidate, **not installation authority**. PRIMARY/Production
were not accessed. The Application remains `28e061a114118a27609330125c756e193d3248a6`
(`8ff5e4a8ed26c32ba040b4fa27cf1d380f3c7541`). Tooling descends from
`bb3129d9c429f2430ebbbb394239c343e408f700`. The execution report records the
qualified exact Tooling commit/tree and external isolated evidence directory.

## Root cause and one existing entry

The sealed PRIMARY recovery operator runs the old preflight before and after
restart, including active-service topology and d8a data assumptions. bb312's
versioned operator instead supports the new data, but explicitly restricts
itself to the isolated authority. Neither is a valid PRIMARY post-write path.

The same `enterprise_agent_poc/scripts/runtime_recovery_operator.py` now loads
a root-approved binding before calling the **unchanged** NativeSuccessor and
ExactTestAdmin. No second recovery executor, new DB schema, witness or grant
service is introduced. Historical seals/operators are not edited.

```text
Installed Application location -> fixed authority root -> exact App/Tool pair
  -> forward recovery policy/approval + loaded unit/environment identity
  -> compatible Native Guard (recovery mode, no service-active prerequisite)
  -> stop API/MCP/Worker -> revalidate -> exact dead-operation proof
  -> existing transactional Recover: Admission + abandoned operation + Revoke
  -> Admin=0 -> compatible Guard -> pinned startup pre/post hooks
  -> MCP/API/Worker health -> compatible Guard + config unchanged
```

Admission preserves historical authorized writes; it never renews execution
permission. Worker still checks the current lease. No tests, tasks or config
are deleted. No model result is promoted to PASS.

## PRIMARY installation preconditions — 06 only, separately authorized

1. Fresh read-only attest current service, source, original parent contracts,
   schema, scope, Secret version/config witnesses and database identity. This
   source release is not a fresh PRIMARY compatibility attestation.
2. Stage root-owned readonly App 28e and exact qualified Tooling checkouts and
   file maps. Preserve d8a/history. Application and Tooling are distinct; never
   install Tooling as an Application release.
3. Use the existing formal release-control process to approve a fresh exact
   PRIMARY pair V1, exact-admin V2 scope/approval, Native policy/approval with
   original PRIMARY parent/import pins. Do not copy isolated approvals or row
   pins, and do not repin changed data as historical fixtures.
4. Independently approve `forward-recovery-policy.v1.json` and
   `forward-recovery-approval.v1.json` under
   `/etc/enterprise-agent-test-exact-admin-v1`, root:root, readonly, no symlinks.
   Schemas are enforced by `runtime_recovery_binding.shape`; no wildcard fields:
   - contract `FORMAL_RUNTIME_FORWARD_RECOVERY_BINDING_V1`, realm `PRIMARY_TEST`,
     environment `test`, production_authority false;
   - exact SHA256 of pair, Native scope/approval/policy/approval and both operator
     modules; independently pinned policy hash;
   - all three exact unit fragments, all effective dropins (including transient
     system.control dropins), ordered mandatory EnvironmentFiles with exact
     SHA256/UID/GID/mode; full files remain pinned, not only selected properties;
   - Python `/opt/enterprise-agent-workbench-test/shared/runtime/python311/bin/python`;
   - `post_write_recovery=EXACT_CURRENT_PAIR_ONLY`, predecessor_rollback false.
   Approval authorization is `FORMAL_PRIMARY_FORWARD_RECOVERY_SOURCE_FIX_APPROVED`.
   Root-issued approval covers the operator's existing dead-process attestation
   contract; it is not an assertion that 06 already granted installation.
5. Bind all three service pre/post hooks to the exact Tooling operator below;
   reset inherited old ExecStartPre/Post in a new approved dropin. Preserve
   existing resource/network restrictions and include their exact bytes in
   policy. No unrestricted or optional environment files. No root execution of
   the lucky-writable shell environment loader.
6. `current` must already resolve to the approved App path. This operator never
   switches Source. Changing systemd units/current/pair is an explicit future
   06 installation operation, **not performed by this task**.
7. Provider remains disabled under the original PRIMARY parent environment
   contract. This patch does not approve a model budget or relax that contract.

Existing root:root 0644 unit files and lucky-owned 0600/0640 environment files
are accepted only with exact pinned bytes/metadata; executable Authority/code
files remain root-owned readonly. Service hooks cannot read root-only env files,
so systemd loads those; the root recovery preflight additionally hashes them.
Ordinary service preflight still validates environment/DB via Native Guard.

## Executable entry contract

Let `PY` be the approved Python above and `OP` the exact Tooling checkout's
`enterprise_agent_poc/scripts/runtime_recovery_operator.py`:

```text
PY -B OP pre api|mcp|worker     # lucky, systemd ExecStartPre
PY -B OP post api|mcp|worker    # lucky, ExecStartPost: Guard + role health
PY -B OP preflight             # root, read-only, permits inactive services
PY -B OP status                # root, read-only, requires healthy services
PY -B OP startup               # root, Admin=0, guarded restart; no grant
PY -B OP recover               # root, stop/reconcile/revoke/guard/restart
```

These commands require the approved ordered environment loaded by systemd,
APP_ENV=test, PYTHONDONTWRITEBYTECODE=1, PYTHONPATH with the exact Application
project **first**, followed by exact Tooling. For a root command use existing
systemd-run with EnvironmentFile properties (not `source`/eval), the approved
Test slice and existing network/resource restrictions. The `preflight` and
`status` actions do not mutate DB or stop services. `startup` and `recover` are
**future PRIMARY writes, forbidden in this task**.

ExecStart must be exact `PY -B -m uvicorn app.main:app --host 127.0.0.1 --port 18100`,
`PY -B -m app.platform_mcp.server`, or `PY -B -m app.worker`. WorkingDirectory
must be the App project, User/Group lucky, KillMode control-group, Test slice.
No fallback to old d8a hooks if a binding is absent or invalid.

## Safe recovery and rollback

Recovery works when services are inactive/failed but PG/Redis and immutable
authority are available. The existing hash-chained ledger and exact tickets,
process identities and DB reservations are checked before the existing Revoke
transaction. Interrupted recovery is retried with the same exact source pair.
No permanent Admin remains. All data is retained.

Old d8a cannot accept post-write Runtime Test data. **Do not switch to d8a or the
old sealed recovery operator after these writes.** Stop fail-closed if the new
binding cannot be validated. Preserve database/evidence for independently
authorized compatible forward repair. No automatic database restore, deletion
or downgrade is provided. This does not change Production rollback contracts.

## Verification and limits

`verify_runtime_recovery_binding.py` exercises PRIMARY schema/loaded-service
binding with test doubles; these are component tests, not live PRIMARY proof.
`run_native_atomicity_rehearsal.py` uses real systemd, private loopback namespace,
PG16.6, issued Synthetic Native contracts, and **the same operator/pre/post
entry** with exact source checks. Run cases ABCDEFGR and negative controls.
No legitimate PRIMARY parent seal or real config is copied into the fixture.
`run_native_parent_regression.sh` inherits the 473 scoped regressions and adds
binding tests. Exact results and commit identities belong to the run report.

Remaining live gates: fresh original parent/config compatibility, formally
approved final pair/unit/environment pins, separate install authority and
post-install health, real Provider/quality acceptance. None is claimed here.
