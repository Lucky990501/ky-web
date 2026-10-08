# PRIMARY WeChat minimal Test Provision tool V1

Scope: `TEST ONLY`, no Production authority. Application identity remains
`db8e23658baa6e4b707380e178aded561d3280f2` /
`04b6774d9e7a95e87871ea9c2e360805988a8e13`. This separate Tooling-only commit
changes no application, Skill, Registry, Migration, schema or frontend bytes.

The existing `scripts/provision_test_tenant.py` keeps its original Phase A
default behavior and adds explicit, mutually exclusive flags:

- `--test-only-minimal-provision`
- `--exact-test-tenant-cleanup`

Both require a root-owned, non-symlink, immutable native scope at
`/etc/enterprise-agent-test-successor-wechat-personal-db8-v1/provision-scope.v1.json`.
The closed scope binds the exact Tooling Source/tree, db8 Application Source/tree,
one run UUID, the exact object set and zero WeChat/Provider/Image budgets.
There is no caller-selected Tenant, database URL, SQL file or generic API.
Native execution is Linux-only, `environment=test`, exact loopback PRIMARY
PostgreSQL host/port/role/database, and actual DB identity must also match.

The only created records are one Synthetic Tenant,
`wechat-personal-center-test-v1`, its one enterprise-config row with purpose
`WECHAT_PERSONAL_CENTER_CONFIG_V1`, and one `enterprise_admin` user. Membership is
the existing `users.tenant_id` relationship. That role is required by the
existing profile configuration API; no platform-admin grant is created.
No credit seed, Agent instance, Agent/Skill, Binding, runtime workspace, Task,
Migration ledger or existing Tenant is written.

`--dry-run` remains non-mutating. Actual execution requires `--execute` and
creates a private mode-0600 temporary application credential file, never stdout.
The database inserts use the existing Provision statements inside one transaction.
The receipt identifies every created record by exact primary key and row digest;
it contains no password/Secret/ciphertext. A lost commit acknowledgement retains
the receipt/private credential until exact-scope recovery rather than assuming
the transaction rolled back.

Before Cleanup, the operator must seal the exact receipt SHA and scope SHA in
root-protected `provision-receipt.approval.v1.json`. Cleanup fails closed on a
wrong receipt, row/config drift, foreign object, extra Tenant row or platform grant.
It locks and deletes only the three receipt-owned records, preserving all other
Tenants, Agent/Skill revisions, Bindings, Tasks and migration rows. There are no
unrestricted bulk deletes or storage-prefix scans.

The receipt records initially absent exact per-Tenant encrypted backend paths.
If profile APIs created those files, Cleanup requires the same Synthetic record
to be revoked with `secret=None` before removing its two exact files. No other
Tenant's Secret is resolved. The temporary application credential is removed;
the sanitized receipt and config audit events remain as evidence. The native
guard and final health checks must independently establish baseline restoration.

The deterministic suite `tests/test_wechat_minimal_provision.py` covers the
13 required Provision/Cleanup boundaries plus Source drift, arbitrary scope,
config drift, unreceipted objects and non-mutating dry-run. Run it on PRIMARY
CPython 3.11 under the Test resource slice. Windows cannot prove POSIX mode/UID
checks; Windows fixture failures are retained, not treated as Linux validation.

This tool does not install/switch the application, authorize ordinary Chat,
relax Runtime Test qualification, enable `CREATE_DRAFT`, call WeChat or change
Production. db8 installation remains governed by the separately approved exact
`WECHAT_PERSONAL_DB8_PRIMARY_SUCCESSOR_V1` and its native rollback readiness.
