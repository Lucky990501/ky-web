# 894 successor: minimal Test-only Text request gate

This is an OFF-by-default Test-only deployment candidate, **not installed**.
Direct parent: `c2dbb92d5f2b63aab4149462d08e9875829f2286` (based on exact894).
Production d906, running Test894, Skills, Publish contracts and migrations are
unchanged. No real Provider requests were authorized or performed here.

## Implemented request boundary

`TaskService` forwards its server-side Task ID when the flag is enabled.
`AgentService` preserves that current ID for new/resumed sessions. Grounding
audit children inherit it; corrections use the same session and allowance.
`CodexRuntimeManager` binds a separate Test Task Codex home/process cache key
to a loopback-only HTTP gate, using the existing signed Runtime token issuer.
The child receives a local short-lived credential, not the upstream key.
Known inherited key/token/secret/password/proxy environment values are masked.
This is not an OS isolation guarantee against a malicious same-UID host process.

The loopback handler accepts only `POST /responses`. It does not accept a URL,
Tenant, Task ID, credential location or target host in Tool/client arguments.
Its server-side signed context identifies Tenant/Task/Agent/User/Run. Every
request rereads the actual accepted Task, live Run and enabled User, then the
exact whitelist. Unknown identities, cancelled/failed Tasks, stale Runs, bad
signatures, expired/revoked/missing/unreadable state all reject before sending.

One configured **persistent local** SQLite ledger is shared by all Worker/API
processes and all allowed Tasks. `BEGIN IMMEDIATE`, full synchronous commit,
unique attempt slots 1 and 2, and a fixed maximum of 2 implement reservation
before the one `httpx` upstream POST. Failed HTTP responses, timeouts and exits
after reservation never refund. Restart only opens `mode=rw`; it cannot create
a new budget. A separate one-time initialization marker prevents replenishing
the same path after losing the mutable counter. Never delete/reset either file,
restore an older counter, select a different ledger or Operation to replenish.

Upstream is fixed to `https://api.deepseek.com/responses`, model V4 Pro, high
effort. HTTP transport retries and redirects are disabled; SDK provider request
and stream retry settings are both zero. Installed openai-codex 0.147.0 was
executed against the real gate and mocked upstream: HTTP500 and broken SSE
each produced exactly one request, one reservation, and a terminal error.
Any explicit correction/review or Worker retry still shares the same counter.
The final capped HTTP envelope is hashed, not persisted or logged; attempts retain Task/Run,
slot, time and request hash. There is no refund, automatic reset, billing
system, public allowlist API, Agent publish bypass or business DB migration.

Image calls additionally reject at PlatformMCPService entry while enabled.
The exact894 application has no CREATE_DRAFT executor to enable in this change;
no WeChat dispatch, credential or network permission is introduced.
Embedding must remain local-hash. Existing disabled-provider recovery settings
remain unchanged when the new flag is absent/false.

## Complete finite-text input and cost bound (2026-10-15 successor)

The prior 1024/512 diagnostic ceiling is superseded by the Owner's bounded
business-test authorization. No history, instructions or tools are removed.
The final request includes an explicit `max_output_tokens`, default/maximum
8192; a smaller explicit cap is retained. Per the official Responses contract,
this limit includes reasoning AND visible output. It is not a visible-only cap.

Only a finite stateless text envelope is admitted: complete system/developer/
user/assistant messages and text parts, plain reasoning, function-call JSON
objects and tool results, apply_patch custom call text, instructions, tools,
and output-format schema. All other allowed control/metadata fields are also
included in the bound, not treated as free. Unknown top-level fields, unknown
input types, external history/item references, encrypted reasoning, nonempty
reasoning summaries/annotations, image/file inputs and malformed call arguments
fail before reservation or HTTP. Provider-ignored namespace/web_search tool
definitions may pass, but their entire serialized bytes are still counted;
web_search_call history is refused. This does not add tool execution rights.

Let J be the ASCII-escaped JSON byte length of the ENTIRE FINAL request,
U the total of input items + content parts + top-level tools, and A the number
of top-level keys in decoded function-call argument objects. The bound is:

```
input_tokens_upper = 4096 + 4*J + 256*U + 128*A
cny_upper = (input_tokens_upper*1.32 + max_output_tokens*3.96) * 10 / 1_000_000
admit only cny_upper <= 5 and input_tokens_upper + max_output_tokens <= 1048576
```

Derivation, not a text-tokenizer heuristic: official V4-Pro-0813 encoder
`encoding/encoding_dsv4.py` at model repository commit
`72e1d3230f6c080a530b0a1d46f8eb4602340597` (SHA256
`abc0d26120250dda0ae077dc64aa28836026e61e970854aaeb792445e6a0dde6`)
renders bounded role/transition/invoke/result wrappers (<256 UTF-8 bytes per
item/part/tool) and DSML parameter wrappers (<128 per decoded key). Its single
high-effort prefix, BOS, tools instruction and response-format instruction
together fit 4096 bytes. Message merging/sorting/dropping does not duplicate
content. All variable strings, tool schemas, names, schema values and nested
argument values occur in J; ASCII escaping dominates their UTF-8 byte lengths.
The 4*J allowance also dominates JSON reserialization, separators and retained
field copies. No unbounded retrieval, multimodal expansion or opaque context is
accepted. For this byte-level BPE (empty normalizer, non-expanding Split +
ByteLevel), initial symbols are at most UTF-8 bytes; merges only reduce count.
Thus complete rendered bytes, including wrappers, bound tokens. This is an
intentionally loose ceiling, NOT an exact token prediction or an empirical
characters/token ratio. Raw/ASCII envelopes are also limited to 256 KiB;
budget rejection may occur well before that limit. Never truncate to fit.

Official references (checked 2026-10-15):

- https://api-docs.deepseek.com/api/create-response/ (full input, reasoning cap)
- https://api-docs.deepseek.com/guides/responses_api/ (stateless conversion,
  supported/ignored fields; no server-side history is admitted)
- https://api-docs.deepseek.com/quick_start/pricing/ (peak cache-miss input
  $1.32/M, output $3.96/M; no cache/discount assumption)
- https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-0813/blob/72e1d3230f6c080a530b0a1d46f8eb4602340597/encoding/encoding_dsv4.py

The 10 CNY/USD factor is a conservative conversion ceiling, not an observed FX
rate. With the same two atomic slots and every slot <=5 CNY, aggregate upper
bound is <=10 CNY without extending/resetting the existing private ledger.
Failures consume full slots. This is not a Provider invoice hard cap. Before
separately authorized Test deployment, verify the served model/version, price
ceiling and conversion ceiling still apply; changed contracts must fail review.
Unknown shapes fail closed rather than claiming a bound. No paid request was
used to qualify the input shape, cap or retry behavior.

## Local operator interface (future separately authorized Test control only)

Required settings: `APP_ENV=test`, `ENTERPRISE_POC_TEST_TEXT_GATE_ENABLED=true`,
`ENTERPRISE_POC_TEST_TEXT_GATE_STATE=<approved absolute persistent local path>`.
The route must be deepseek / deepseek-v4-pro / high / responses, exact official
base URL; image provider disabled-test-provider; embeddings local-hash. The
dedicated Test key remains only in the existing protected backend process env.
No secret value is an argument, output, ledger entry or source file.

Use the approved existing Test maintenance control to prevent consumption while
the formal API accepts the intended Task, then allow the exact existing ID:

```
python -B -m scripts.test_text_request_gate initialize --tenant-id <exact-test-tenant> --operation-id <new-approved-uuid> --valid-seconds <at-most-86400>
python -B -m scripts.test_text_request_gate allow-task --task-id <accepted-task-uuid>
python -B -m scripts.test_text_request_gate status
python -B -m scripts.test_text_request_gate revoke
```

Initialization is one-shot, not an authority issuer. The protected deployment
configuration must bind this one path/Operation/database and preserve the
ledger + `.initialized` marker (0600/0400; private non-symlink directory). All
related API/Worker processes must share it. Do not race immediate queue
consumption against allowlisting, revive an already failed Task, or use old e87
context. Native Source/config admission requires a new approved successor, not
an ad-hoc drop-in or modification of running894. No such operation occurred.

## Verification boundaries

Tests use a real private SQLite business fixture, real cross-process/thread
counter contention and restart readback, signed contexts, a real loopback HTTP
handler and mocked external HTTP. They test normal/failure/timeout/retry calls,
quota exhaustion, loss/corruption/revocation, ownership and current Run binding,
new/resumed/audit Task propagation, inherited credential masking and Image deny.
SDK-specific integration tests also launch the actual installed CLI via its
Python SDK against the actual loopback gate and mocked httpx upstream. The
provider configuration is generated by the real Runtime manager; only MCP
transport is omitted in this labelled wire/retry diagnostic. This is not real
PG/Redis Worker, MCP/Native, provider or live Test evidence. No Production/Test PRIMARY mutation,
Root operation, Source switch, actual Task creation or Provider call occurred.

The selected inherited activity integration case
`test_full_plan_running_history_detach_duplicate_and_word_are_one_run[True]`
also fails with the same timeout on untouched exact894. Keep its raw failure;
do not count it as passed, skip it, or repair unrelated activity business here.

Previous immutable candidate evidence: 37 gate cases PASS; inherited selected
cases 129 PASS / 1 FAIL; combined 166 PASS / 1 FAIL / 0 ERROR / 0 SKIP.
The failure independently reproduces on untouched894 with no gate code. It is
not repaired, hidden, skipped or included in the current targeted PASS count.
Current targeted tests: 56 PASS / 0 FAIL / 0 ERROR / 0 SKIP, including two actual
SDK/CLI-to-gate wire diagnostics. No unrelated full QA rerun.

`READY_FOR_MINIMAL_TEST_GATE_DEPLOYMENT=true` means code-level gate qualification
only. Exact Test successor/config installation, one persistent shared ledger,
accepted Task allowlisting before consumption, current SDK parity and the
remaining <=2 real Text attempts still require separate operator authorization.
It does not authorize deployment, a successful business E2E, or two new calls
in addition to any attempts already spent in this same Operation.
