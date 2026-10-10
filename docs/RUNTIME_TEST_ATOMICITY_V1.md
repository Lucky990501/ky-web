# Runtime Test initialization atomicity V1

Application successor of `d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7`.
The separate Tooling successor is based on `1e11e1a4835a465963ec5bd88a5ed40d4b300086`.
Neither identity is a PRIMARY installation authorization.

## Actual defect and transaction

`AgentRuntimeTest.run` previously committed the first configured Tenant Instance,
then called `ProductStore.create_task`, which committed Context, Task, initial
events, context association and Runtime Test in another transaction. API death
between those commits left an Instance with no Test. The Native Guard correctly
rejected that state; accepting it is not the fix.

The caller now lends its existing short repository transaction to `create_task`.
Instance, Context, Task, events, association and Test commit or roll back together.
The borrowed connection is restricted to internal formal Runtime Test creation.
Queue operations, Worker execution, Runtime Manager and Provider calls remain
outside the transaction. No schema, migration, normal chat or automatic enabling
change is introduced. Existing enabled test instances remain rejected.

## Retry contract

`POST /api/v1/platform/agents/{agent}/versions/{revision}/test` accepts an optional
`Idempotency-Key` header containing a canonical lowercase UUID. A retry MUST reuse
that key. The deterministic Test identity binds Tenant, actor, Agent and Revision;
the saved fingerprint and Context association are checked under the template
lock. A matching replay returns the existing Test/Task without enqueueing again.
A conflicting fingerprint fails closed. Omitting the header preserves the old
explicit-new-test API behavior and is not an idempotent retry.

The Test-only gate still checks current authorization before replay. Tooling
records an observational operation, not a duplicate Admission or a new lease.
An interrupted outstanding operation must be recovered before another operation;
replay cannot grant a revoked principal permission. A committed queued Test can
remain queued after recovery: retained Admission is not authority to resume it.

## Preserved integrated draft

The separate, root-approved Application/Tooling loader, exact-admin request gate,
Worker execution permission check and Synthetic seeding restriction are retained.
Production does not load this Test authority. Application code never chooses a
Tooling checkout from request parameters. Installed location, exact Source/Tree,
file maps, ownership and protected approval determine the allowed pair.

## Validation and acceptance limits

`tests/test_runtime_test_atomicity.py` has 15 deterministic repository tests.
With lifecycle and release-scoped PG tests, the Application group has 44 tests;
the paired Tooling runner adds 429 inherited/integration component tests.
Component tests are not Native qualification or real Provider quality evidence.

The Tooling `run_native_atomicity_rehearsal.py` separately invokes real formal
issuance, HTTP, systemd API/MCP/Worker and PostgreSQL 16.6. Its A/B/D crashes prove
pre-commit rollback; C/F/G retain queued Reservations; E runs the real early
Manager failure; R checks same-key HTTP replay. It checks protected Recover,
Admin=0, current Worker permission, Guard, PG restart and service health.

Final exact Source/Tree, per-case results and remote identities belong in the
external handoff evidence produced after both commits exist. No Provider PASS,
Agent Publish/Enable, PRIMARY installation or real WeChat request is implied.

Old d8a/old Guard direct rollback is unsafe once newer Runtime Test records exist.
Preserve data and use an independently approved, exact-pair compatible forward
recovery successor; never remove legitimate records to make an old Guard pass.
