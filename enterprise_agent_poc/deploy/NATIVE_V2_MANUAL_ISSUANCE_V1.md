# B38 business successor: Native V2 manual issuance

This is a fixed-purpose connection to the unchanged Native V2 consumer. It is
not a deployment/backup/restore runner or a general authorization platform.
No approval exists merely because these files or a request exist.

The final Source is a reviewed successor of b38. Application, migrations,
dependencies and frozen consumers remain byte-identical to b38. The only test
fix selects the named APP_ENV field instead of relying on report row order.

Use `deploy/native_v2_manual_entry.ps1` without `-Execute` first. It accepts
only explicit POSIX paths under the one release's preparation directory or its
installed entry. The independent entry/request SHA256 checks happen before
execution; no Candidate imports precede immutable archive verification at seal.
Owner performs the actual confirmation in the delegated visible TTY, without
manual SSH commands or hand-written JSON. Codex never types confirmations.

## Future production input, not issued in implementation tests

An approved preparation step must create only the fixed root-owned preparation
and audit parents. The immutable request binds the final actual Source/tree/parent,
archive/raw/canonical Manifest, exact d906 identity, issuer SHA256, Native V2
binding/schema fingerprint, ten fixed report paths, actual operation/fee text,
and remaining work estimate. It has kind B38_NATIVE_V2_MANUAL_ISSUANCE_V1,
business_base_source=b38e37a1a9c6a8dc2f8b98b501f1e21a92a251f3 and fixture=false.
Never prefill dynamic artifact, backup, report or human-decision values.

`owner` captures only the actual Owner decision and original bytes; `root`
captures a different actual technical decision bound to it. Their times start
at actual acknowledgements. The maintenance window starts at Root confirmation,
not at later Policy/record entry. Technical sealing validity is one hour,
maintenance four hours, with its last hour reserved for recovery. Later work
uses the same window; it does not demand a new full four hours at every entry.

`seal` requires all ten actual source-bound reports before asking for the
independent release/recovery decision. Reports wrap original JSON/JUnit bytes
by fixed path/hash and expose counts, Source/tree, run/time and resolved-skip
receipts. Failures/errors or unresolved release-critical skips stop before a
claim or protected record is written. Raw skips remain visible and need their
actual closure receipts. This entry never generates Native execution evidence.

The release lock spans final checks, human release review, record writes and
the frozen consumer. An exclusive claim prevents replays. Failure preserves
its claim/records; any newly written consumable approval is withdrawn into the
same audit archive if final consumer validation fails. No old claim is reset.

Native V2 does not consume revocation sidecars. `revoke` therefore withdraws
only this exact approval name under the existing lock and preserves its original
bytes before writing its audit receipt. It cannot operate after the active
runtime has left exact d906; recovery then requires the existing authorized
release/recovery workflow. No service or database action exists in this entry.

## Boundaries

The isolated lane is forbidden on the Production hostname. It accepts only
private native-v2-fixture-* directories, marks decisions/reports/records as
FIXTURE_NOT_AUTHORITY, and cannot produce an actual Native V2 permit. CLI tests
are control-flow evidence, never successful Production qualification.

Native V2 still requires Full pytest, Stage2, Source installation, API/MCP/Worker
Native, exact d906/Schema015 compatibility, pre/post-commit recovery and business
data preservation. No evidence, independent release/recovery permission, or
paid Smoke permission is created by this implementation. Preserve Production's
1536-dimensional profile and existing124 vectors; WeChat1.0.1 registration through
the authenticated platform-admin lifecycle remains separate from Agent binding,
real Runtime Test and publication.
