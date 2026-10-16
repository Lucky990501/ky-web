# One customer pilot: bounded Test calls

This is an isolated Test successor of business894, reusing the completed3030
request-bound/wire mechanism. It is not3030 deployment or Production authority.
Default configuration leaves the gate disabled and business behavior unchanged.

The active contract is ZHIYE_BATCH1_TEST_PILOT_V1. One durable SQLite ledger and
one initialization anchor bind the exact technical Scope SHA, Operation, dedicated
Test database, independent member/operator, WeChat Agent/Revision/fingerprint,
and the original Owner approval/event hash. Never initialize after state loss,
reset counters, delete failed attempts, or borrow another Operation's allowance.

Limits across ALL processes and tool phases are 6 ordinary customer Tasks, at
most1 formal Runtime Test, 20 actual Text HTTP attempts, 3 Image HTTP attempts,
input131072 and output INCLUDING reasoning65536 tokens. Expiry may only be
shorter than the original Owner24h window (2026-10-16T04:43:23.195Z to
2026-10-17T04:43:23.195Z). The30CNY acceptance (Text10/Image20) is risk acceptance,
not a proven supplier invoice ceiling. Image tariff is not verified.

Only the existing legally delegated Test technical installation can create an
APPROVED, Root-owned,0440/0444, immutable-path technical context. The operator
entry consumes this protected context and all9 exact execution-file hashes,
including the authenticated task-creation entry and its Test-only wiring.
Runtime consumes the SAME context hash, expiry and REVOKED state before EACH
paid attempt. No Fixture, chat JSON serialization or initializer issues Root
permission. Final Source/Tree/full artifact verification belongs to the existing
external startup/install Guard. No c602 Owner/Root/Claim transfers to this Source.

The initializer requires actual customer/member/operator and new WeChat Revision
identity; it cannot run now with placeholders. The operator is distinct from the
ordinary customer and has only separately approved temporary control-plane rights.

Each complete finite SDK Responses envelope uses the V2 finite-text byte bound:
4096+ASCII_JSON_BYTES+CANONICAL_DECODED_ARGUMENT_BYTES
+256*(items+parts+tools)+128*decoded_argument_keys.
All instructions, Skill text when read, tools, function output and history must
remain in the final envelope. Unknown/opaque/remote/multimodal context is refused.
No per-request16384 assumption is imposed: the original cumulative131072 is the
limit. SDK request/stream retry are0 and Test structured paid correction is1
attempt. Normal model/tool dialogue still consumes an HTTP slot every time.

Before send, BEGIN IMMEDIATE reserves full input upper bound and capped output
(max8192 each). Slots commit durably BEFORE HTTP. Failure, timeout, missing usage,
truncated SSE, incomplete/error terminal or unknown billing keeps the full token
reservation and permanently spends the HTTP slot. Only one complete successful
Provider terminal with valid input/output/total/reasoning usage can settle its
reserved tokens to actual usage; this never returns an HTTP/Task slot. Concurrent
requests cannot reuse in-flight token capacity. An input envelope larger than the
remaining pool is stopped BEFORE HTTP. Maximum20 requests is not a promise that
all20 can fit, or that A-E must succeed within the approved pool.

Task admission also runs inside the original task-creation transaction BEFORE
any Task/context insert. A PG transaction advisory lock and READ COMMITTED count
serialize the six member/one formal-operator limits; SQLite fixtures use BEGIN
IMMEDIATE. All actual Task states count, including queued, cancelled and failed
before SDK startup. The private formal-test parameters must match the exact
operator/Agent/Revision/fingerprint. A browser cannot supply those parameters.
The SDK allowed_tasks ledger remains an independent execution/HTTP witness; a
queued Task does not need a fabricated Run ID or pre-created SDK ledger entry.
No schema migration, Task deletion, counter refund or automatic retry is added.

## V2 byte-bound derivation and reference

The former 4x whole-JSON multiplier rejected all four real Native first-packet
fixtures (158628/189944/156832/177948 against the entire131072 input pool).
Those measurements are preserved. The limit, complete context and pre-HTTP
reservation remain unchanged; this correction bounds the actual published text
encoding instead of counting four hypothetical copies of every input field.

The public [Responses compatibility specification](https://api-docs.deepseek.com/guides/responses_api/)
defines stateless full input, one top-level tools vector/format, instructions as
the initial system message, developer as user, plain reasoning and merged tool
items. Unknown, referenced, encrypted or multimodal inputs remain rejected.
The public [V4 reference encoder](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/b5968e9190ef611bbf34a7229255be88a0e937c1/encoding/encoding_dsv4.py)
serializes tool/schema definitions with JSON, copies each text/value into its
message or DSML parameter, and adds finite global/message/parameter framing.
The reference revision is b5968e9190ef611bbf34a7229255be88a0e937c1; encoding.py
SHA256 bdbd57c132a1b3725042323d02b98b9d1df28e5f388f134399555d041f5055e0.

The reference tokenizer.json SHA256 is
8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf.
It has an empty normalizer sequence and ByteLevel(add_prefix_space=false) followed
by BPE. Splits add no content; BPE merges only reduce byte units. Thus rendered
UTF-8 bytes bound token count. ASCII-escaped JSON bounds the decoded UTF-8
strings and the JSON tool/schema serialization while also counting every
otherwise ignored control field. DSML's canonical decoded argument JSON is
counted once again, including nested arrays/objects whose compact input may
acquire spaces. The4096 global allowance dominates the published tools/format/
BOS prefixes;256 per item/part/tool and128 per decoded argument cover the
published role/transition/invoke/result/parameter separators.

This is a bound for the documented, finite, stateless V4 text protocol, not a
claim about arbitrary undocumented supplier context or an invoice hard cap.
Reference files are audit/test inputs, not new runtime dependencies. Actual
Provider usage must still pass complete-terminal consistency checks before any
settlement. Namespace tool availability is a separate protocol-compatibility
question: counting its entire JSON does not prove a Provider executes it.

## SDK0.147 namespace identity compatibility

The ordinary SDK serializes MCP tools as namespace wrappers. The documented
DeepSeek interface accepts function definitions; disabling namespaces in this
SDK filters them out rather than flattening them. The existing Test-only HTTP
gate therefore maps each namespace/function pair to a unique `namespace__name`
function, retaining its complete schema and description. Namespace metadata is
retained in instructions. Duplicate/colliding identities or unregistered
namespace histories fail before reservation/HTTP; no tool is silently dropped.

Before the actual send, historical function identities and tool_choice use the
same map. All unrelated strings, paths, argument text, call IDs, Skill content
and observations remain unchanged. The final transformed bytes are the bytes
bounded, reserved, hashed and sent. On return, function_call namespace/name is
restored at output_item.added/done and response output paths, preserving call_id,
arguments and event framing. `function_call_output` continues to pair by call_id.
Incomplete SSE is never made complete by the compatibility step. Settlement
uses the original Provider terminal and original response hash.

This is a finite compatibility step in the existing per-Task gate, not an SDK
fork or a new provider gateway. It grants no additional tools, network access,
Task/HTTP allowance, model permission, Root permission or Production authority.
Actual Native/MCP round-trip proof remains required; static DTO field support
alone is not that proof.

Image MCP checks the real signed Task scope/DB ownership before each actual
https://llm-api.net/v1/images/generations send. Only gpt-image-2.5-sunburst-c, n1,
JPEG/base64 and this trial's1536x1024 or1536x864 are accepted. Reference editing
and retry_of are denied. Unique Task/item and request fingerprints prevent paid
image replay even with a changed prompt. Ledger hashes cover exact sent bytes.
SDK subprocesses receive only local short-lived gate credentials; inherited
Provider credentials are masked. No secret is written in reports or fixtures.

Offline tests use private SQLite, synthetic keys and mocked upstream. Authority
reader substitution is explicitly a Fixture, never real Root verification.
Native SDK diagnostics are only wire/retry checks, not completed A-E E2E or real
Skill discovery/Runtime Test. Required live checks remain actual technical scope,
customer provisioning, bounded configuration, genuine Runtime Test/Publish and
ordinary customer tasks. All real Provider traffic remains zero until those pass.

Retain original894 release/zero-paid configuration, all prior receipts and failed
claims. Recovery must preserve legitimate new customer data, Task/Run/credits and
this budget's spent attempts. No DB snapshot refund, old-claim replay, Production
switch, or service change is authorized by this document.
