# Two-probe Stage2 Admission

This fixed profile extends the existing text gate only for the two formal
Stage2 Runtime probes. It is neither a billing platform nor Provider approval.
The business baseline is exact28948; a resulting successor needs its own
Source/tree/Archive/Manifest and current Native Owner decision.

The formal operator command is the existing
`scripts/release_test_environment.py run --credential-file <dedicated0600>
--admission-directory <Root-owned exact Native Owner export directory>`.
Only the two existing real Provider test modules are selected. The Worker
alone reads the dedicated non-Production key. No Key is accepted in chat.

`request.json` and `owner.json` are byte-identical, Root-owned0444 exports
of the existing actual Native V2 Owner archive. Original0600 receipts are
retained. The export must be managed through the same authorized operator;
revocation must stop this run and place `revoked.json` in its protected
export directory before any further execution. No new issuer is supplied.

The signed request's `fees.stage2_two_probe` must contain profile
`STAGE2_TWO_PROBE_ADMISSION_V1`, exact `LIMITS` from the Source, model
`deepseek-v4-pro`, reasoning `high`, peak input/output micro-CNY per token
9/27, `automatic_paid_retries=0`, `other_paid_calls=0`, actual
`price_verified_at` within24hours and `ledger_path` under the existing
Test release-evidence root ending `stage2-two-probe.sqlite`.

Every actual upstream HTTP send reserves durable capacity first:

* Two HTTP sends total, at most one per distinct formal probe/Task.
* Each input at most65536 tokens under the existing finite-text byte bound;
  cumulative input at most131072.
* Each output `max_output_tokens=8192` or a smaller caller cap, including
  reasoning; cumulative output at most16384.
* Nominal peak-price maximum is131072*9/1M+16384*27/1M=1.622016CNY;
  the local pre-send monetary ceiling is1.63CNY. Reservations are never refunded.
* No concurrent send, third request, same-probe replay or automatic retry.
  Unknown usage, HTTP failure, timeout or cancellation stops all later sends.

These are local request and published-price bounds, not an independently
enforced Provider invoice ceiling. Price/account charging errors are not
controlled by this code. Fresh price review and actual Owner fee approval
remain necessary; no paid test runs during implementation.

The Runtime SDK is configured to use the loopback gate instead of the
Provider URL, with local signed gate credentials, masked inherited secrets,
request/stream retries0 and one structured-model attempt. Server-paid
web_search tools are rejected. Other images/Embedding remain outside this
profile and are not authorized. Existing required MCP/tool turns can cause
a Runtime to need a second model request: it will be refused. Two Runtime
Tasks are not assumed to fit two HTTP calls, and no formal PASS is implied.

The SQLite ledger and separate exclusive initialization marker must remain
outside disposable Stage2 fixtures. A second run, missing state, failed
attempt or existing marker cannot initialize/reset the allowance. Audits
contain identities, byte hashes, reserved/actual token counts and integer
monetary reservations, never API keys, prompts or generated content.

Native execution remains a separate gap: historical backup/restore helpers
consume old V3 policy/Operation schemas, while the unchanged Native recovery
CLI demands final qualification before DB/runtime access. Adapting field
names does not create a lawful qualification-only rehearsal permission.
No frozen consumer is relaxed by this Stage2 change.
