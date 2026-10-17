# Two Runtime probes with bounded multi-turn HTTP

This dedicated extension reuses `test_text_gate`, the existing Runtime SDK,
`release_test_environment.py` and the Native V2 Owner archive. No billing
service or new issuer is introduced. Business baseline is exact28948; final
Source/tree and byte hashes must be independently bound to a new decision.

## Operator and identity

Use the existing `scripts/release_test_environment.py run --credential-file
<dedicated0600> --admission-directory <Root-owned Native Owner export>`.
Only the two existing formal Stage2 Provider modules may run, using existing
`stage25-test-tenant` / `runtime@stage2.test` identity and exact module prompts.
The dedicated non-Production key is read by the Worker only, never in chat.
Task/Run ownership, runtime-test revision and configuration fingerprint must
match on every request. Two tasks are allowed, not two arbitrary tasks or
an extra WeChat1.0.1 Runtime test.

Root-managed request.json/owner.json exports are byte-identical0444 copies
of original0600 Native records. Existing issuance and revocation semantics
remain in force. This code neither creates an Owner decision nor resets its
24-hour maximum. Revocation must reach the protected export before another
request; the authorized operator owns that transport. No old Source grant
or prior ledger can authorize this profile.

## Exact fee profile and send boundary

`fees.stage2_two_probe` must bind profile
`STAGE2_TWO_PROBE_MULTITURN_ADMISSION_V2`, exact Source `LIMITS`, model
`deepseek-v4-pro`, reasoning high, rates input9/output27 micro-CNY per token,
automatic_paid_retries0, other_paid_calls0, price_verified_at within24hours,
and ledger_path ending stage2-two-probe.sqlite beneath the existing Test
release-evidence root. The outer fees.automatic_paid_retries is also0.

* Maximum4 actual model HTTP sends per Runtime task,8 total.
* Input each65536 and cumulative524288 tokens; finite ASCII JSON plus the
  existing conservative framing bound covers the complete SDK/tool history.
  Unsupported/opaque/multimodal content is refused before sending.
* Output each at most8192, cumulative65536; actual Responses request enforces
  max_output_tokens, including reasoning. No omitted unbounded output cap.
* Worst documented peak/cache-miss arithmetic:
  524288*9/1000000 +65536*27/1000000 = CNY6.488064.
  Durable pre-send reservation ceiling is CNY6.49. Reservations never refund.
* The local ceiling bounds accepted requests at those verified rates. It is
  not a separately enforced vendor invoice/account balance cap. Unexpected
  vendor charging is outside local enforcement and must be disclosed in the
  actual fee decision. No fee permission is implied by this document.

Official rate source: https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
Output cap: https://api-docs.deepseek.com/api/create-response/
Prices and supported output semantics require Fresh confirmation before a
paid run; implementation tests make zero real Provider requests.

All Runtime model traffic uses the signed loopback Admission. The sole actual
outbound post targets the fixed Responses URL with retries0, redirects off,
trust_env false. SDK request/stream retries and structured-model retries are0.
Paid server search/fetch and all separately priced server tools are refused.
Image/Embedding are outside this profile and remain unauthorized. MCP tools
in the two bound probes are existing read-only tools; no image generation or
extra agent/task is admitted.

SQLite BEGIN IMMEDIATE serializes reservations. Inflight/unknown attempts,
identical request replay, changed Task/Run/test revision/fingerprint, a fifth
turn, a ninth request, timeout/cancel/error or unknown/excess usage fail closed.
Successful Skill/MCP outputs may continue the same bounded task. SDK0.147
exec success envelopes and actual MCP result envelopes are checked; failed,
running or unknown tools stop before another paid model request, so model
repair/compensation cannot consume extra fees.

## Evidence and limits

The actual installed SDK0.147 CLI reads a real sandboxed Skill and invokes a
real local FastMCP tool. Explicit upstream MockTransport responses complete
two tasks in6 HTTP sends. Tests also exercise4-send exhaustion, Provider
read timeout, MCP error and MCP deadline. This is full SDK path simulation,
not a real Provider Stage2 PASS. The original approval reviewer stays active;
existing truthful read-only MCP annotations are retained, not bypassed.

Audits contain identities, request/response hashes, denied-request reasons,
reserved/actual token counts and integer monetary reservations; no API Key,
prompt or generated content. A durable exclusive initialization marker
survives missing ledger state and prevents allowance reset/reentry. Old
single-HTTP profile grants do not transfer to the new profile.
