# Required tool dependency retry / supersession V1.1

Scope: backend architecture, tests, and isolated deterministic replay only.
No Production mutation, Provider call, frontend, Skill, Prompt, schema, or
migration change.

## V1 problem

Real `deepseek-v4-pro` evidence copied the server-issued `retry_of` token in
3/3 runs, but changed the coupled `prompt` while repairing `aspect_ratio`.
V1 treated all submitted retry arguments as canonical and required byte-level
stability for every non-repairable field, so all three otherwise legitimate
retries failed `invalid_retry_lineage`.

V1.1 no longer asks the model to preserve canonical bytes. The model must
still explicitly copy `retry_of`; it cannot cause automatic retry inference.

## Server-side reconstruction contract

`RetryReceiptLedger` is the authority for changed-input retry:

1. Platform validation fails before Provider invocation.
2. The Platform MCP service stores the original canonical arguments in a
   process-local ledger and returns an opaque, one-use `rtr1_...` capability.
   Only the token digest is the lookup key; canonical arguments are not placed
   in the public receipt.
3. The receipt declares `retryable`, failure category, repairable fields,
   allowed values, and coupled text fields. It is bound to the authenticated
   Runtime principal, execution scope, server, and tool, and expires after a
   bounded lifetime.
4. A retry validates and consumes the capability before Provider invocation.
   Effective arguments start as a deep copy of the original canonical args.
5. Only declared repairable fields accept submitted values, and only from the
   receipt's allowlist. Declared coupled text fields are reconstructed from the
   original text with exact replacement of every old-scalar occurrence.
6. If the old scalar does not occur, the original coupled text is retained.
   No semantic comparison, embeddings, fuzzy matching, or model-authored full
   replacement is used.
7. All other submitted drift is ignored for execution and retained in the
   attempt audit as `ignored_retry_argument_drift`.

`CodexRuntimeProvider` installs a fresh, random
`X-Runtime-Execution-Scope` header in the thread start/resume configuration for
each product Task. Platform MCP reads that model-inaccessible header and binds
the receipt to it. The existing signed Runtime principal supplies tenant,
Agent, runtime profile, and, for productized Agents, execution context and
instance identity. MCP session ID is a compatibility fallback for older HTTP
clients. The legacy in-process adapter uses a bearer-derived scope; the same
principal, one-use, expiry, server, and tool checks still apply.

## Fail-closed boundary

The service blocks before Provider invocation for unknown/forged, expired, or
consumed capabilities; wrong principal/execution scope/server/tool; disallowed
failure category; missing repair field; unchanged rejected value; or a new
value outside the receipt allowlist. It does not infer lineage from equal tool
names, similar arguments, adjacency, or elapsed time.

Each initial SDK tool call still creates an independent logical dependency,
including byte-identical calls. A validated child inherits only the dependency
identified by its receipt. The failed parent remains immutable and gains
`superseded_by`; the successful child gains `retry_parent`. Finalization checks
the logical dependency rather than erasing the failed Attempt.

## Audit and persistence

Existing Run Trace JSON carries the new audit fields; no schema migration is
required. Each Attempt retains:

- `submitted_args`: what the model actually submitted;
- `effective_args`: what the Platform service executed;
- `ignored_retry_argument_drift`: non-authoritative changed field names;
- deterministic coupled-repair details and fingerprints;
- receipt validation, parent/supersession, Provider-side-effect, and timing
  evidence.

The public receipt itself remains free of the original prompt and references.
The Run Trace already stores user/task content and is the authorized audit
surface for submitted/effective values.

## Isolated verification

`tests/test_tool_dependency_supersession.py` covers V11-01 through V11-18 and
the three observed drift shapes:

- R1 model changes `3:4` to `4:5` in prompt;
- R2 model deletes the `构图比例 3:4` phrase;
- R3 model rewrites prompt and references.

All three execute the same server-reconstructed canonical intent with one
stub Provider call. Tests also cover all-occurrence exact replacement, absent
scalar retention, invalid/forged/reused/wrong-scope/wrong-principal/wrong-
server/wrong-tool/disallowed-value rejection, two independent image
requirements, a normal first legal call, missing retry, persistence,
Generation association, SSE completion, credit/idempotency accounting,
non-image required tools, and the legacy image Agent path.

TD-043 remains **MITIGATED / P1** after isolated contract and regression pass.
`PRODUCTION_PROVEN = NO`; real-model 3/3 validation remains the next gate.

### Verification evidence (2026-09-29)

- Focused V1.1 suite: **46 passed**.
- Full related regression: **271 passed, 0 failed, 0 skipped**, with two
  dependency deprecation warnings, in 192.76 seconds.
- Linux Python 3.11 isolated Git snapshot with temporary SQLite/local storage;
  HTTP generation/download were in-process stubs.
- Actual Provider calls: zero. Production, original Task/Run/Asset, frontend,
  Prompt, Skill, schema, and deployment state were unchanged.

Operational observation: receipts are intentionally process-local and bounded
to 4,096 live entries with TTL cleanup. A future multi-replica Platform MCP
deployment will require sticky routing or a shared confidential receipt cache;
missing state fails closed and cannot invoke the Provider.
