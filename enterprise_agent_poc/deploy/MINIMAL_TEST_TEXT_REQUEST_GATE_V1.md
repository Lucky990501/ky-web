# 894 successor: minimal Test-only Text request gate

This is an OFF-by-default local development candidate, **not deployment-ready**.
Parent: `894bdd2a031725a0d0df3b95d6dfc26d08f0e4ab`.
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
and stream retry settings are both zero. Any later SDK retry still has to pass
the same counter; configuring zero is not claimed as real CLI verification.
The raw HTTP body is hashed, not persisted or logged; attempts retain Task/Run,
slot, time and request hash. There is no refund, automatic reset, billing
system, public allowlist API, Agent publish bypass or business DB migration.

Image calls additionally reject at PlatformMCPService entry while enabled.
The exact894 application has no CREATE_DRAFT executor to enable in this change;
no WeChat dispatch, credential or network permission is introduced.
Embedding must remain local-hash. Existing disabled-provider recovery settings
remain unchanged when the new flag is absent/false.

## Remaining blocking input contract

`FIRST_FAILURE_POINT=CODEX_RESPONSES_INPUT_1024_BOUND_NOT_PROVEN`.

The prior accepted single diagnostic call proved a conservative 1024-token
bound for **one string input**, no history/instructions/tools, using official
high-effort single-user framing (563 framed UTF-8 bytes for 22 input bytes).
Only that narrow shape is admitted here: input UTF-8 bytes +541 <=1024 and
output, including reasoning, <=512. No text or content is silently truncated.

Codex Responses message arrays, tool definitions/results, system/developer
instructions, previous-response references and unfamiliar fields do NOT have
a proved bound under that diagnostic contract. The gate rejects them with
`test_text_gate_input_bound_not_proven` before reserving/sending. Tests explicitly
prove this rejection; they are **not proof of a usable ordinary Agent E2E**.
Do not deploy/enable this candidate for the remaining two real requests until
the actual SDK request shape has a trustworthy <=1024 input bound. Do not solve
this by deleting instructions/tools, dropping conversation history, guessing a
token conversion, expanding the budget or pretending a string request is an
actual Agent request. Full native SDK egress/retry behavior is also not yet
qualified; no paid call should be used to discover these preflight failures.

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
The SDK process itself is a labelled double. This is not real PG/Redis Worker,
Native, provider or live Test evidence. No Production/Test PRIMARY mutation,
Root operation, Source switch, actual Task creation or Provider call occurred.

The selected inherited activity integration case
`test_full_plan_running_history_detach_duplicate_and_word_are_one_run[True]`
also fails with the same timeout on untouched exact894. Keep its raw failure;
do not count it as passed, skip it, or repair unrelated activity business here.

Local final run: 37 new gate cases PASS; inherited selected cases 129 PASS,
1 FAIL; combined 166 PASS / 1 FAIL / 0 ERROR / 0 SKIP (167 unique cases).
The one failure independently reproduces on untouched894 with no gate code.
The failed input-bound qualification is deliberately represented by explicit
pre-request rejection tests, not mislabeled as successful Agent execution.

`READY_FOR_MINIMAL_TEST_GATE_DEPLOYMENT=false` until the input/SDK boundary is
closed and a separate exact Test installation is authorized.
