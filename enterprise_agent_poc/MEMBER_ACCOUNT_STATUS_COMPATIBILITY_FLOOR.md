# Member Account Status Compatibility Floor

## Status

PASS

## Scope

This local-only change closes the rollback safety gap introduced by
`012_member_account_status.sql`.  It does not run a migration, alter a shared
environment, switch a release, access production, or change application
runtime behavior.

## Contract

`rollback_compatibility.json` is now schema version 3.  It pins
`member_account_status_v1` to the canonical checksum of migration 012 and
requires these two semantics:

- disabled login is rejected;
- an already authenticated, then disabled session is rejected.

The rollback preflight reads `schema_migrations` in its existing read-only,
repeatable-read transaction.  The floor becomes active only when the exact
012 filename and canonical (or legacy line-ending-compatible) checksum are
present.  A disabled-account row is not required to activate it, so the floor
is monotonic and cannot be bypassed by changing data.

Before 012, the exact trusted 001--011 prefix retains the established
productized behavior: bd04dcb and b930e87 pass, while 6abccad remains blocked
by the existing productized epoch floor.  After exact 012, 6abccad, bd04dcb,
and b930e87 are blocked.  The current release may pass only as its own
release-id/source-commit identity and only when its shipped declaration
supports `member_account_status_v1`.

No target path is caller-controlled.  The existing release identity checks
remain in force for the trusted current release and static approved targets:
release layout, source commit, archive checksum, manifest checksum, source
bytes, selected-file set, permissions, symlink rejection, and path safety.
Unknown migration history and checksum mismatches remain fail closed.

## Isolated verification

The marked local PostgreSQL 16 cluster under `/private/tmp` was used only for
tests.  It covered:

- 001--011 productized rollback behavior;
- exact 012 with all accounts enabled;
- old artifact blocking after 012 and declared candidate acceptance;
- disabled login and an existing session rejection in the candidate HTTP path;
- unknown/checksum-mismatched 012 history; and
- existing release identity and release-switch checks.

Results:

- Targeted compatibility, release-switch, and PostgreSQL tests: **28 passed,
  74 skipped**.
- Full pytest: **462 passed, 75 skipped, 0 failures, 0 errors**.
- Node route tests: **26 passed**.
- `node --check app/static/workbench.js`, `python -m compileall -q app scripts
  tests`, `bash -n deploy/release_switch.sh`, JSON parsing, `py_compile`, and
  `git diff --check`: **PASS**.

## Changed files

- `deploy/rollback_compatibility.json`
- `scripts/rollback_preflight.py`
- `tests/test_compatibility_epoch.py`
- `tests/test_agent_productization_postgres.py`

No production operation was performed.
