# WECHAT_HTML_DRAFT_SKILL_INTEGRATION_V1_REPORT

## PRIMARY Runtime Acceptance V1 — 2026-10-07

本轮任务附件：fbe8556b-6667-4dbe-9f14-0b66c3914445。
最新Primary验收状态：**WECHAT_SKILL_RUNTIME_DEPENDENCY_BLOCKED**。
上游Contract READY/Windows66PASS保持原归属，不改为PRIMARY runtime READY。

PRIMARY Python3.11.16 fresh：requests2.34.2/Pillow12.3.0 PRESENT_COMPATIBLE + imports PASS；
beautifulsoup4/css-inline/tinycss2 MISSING。现有pip check PASS；缺失包不能被pip check PASS掩盖。
Managed dependency snapshot0ee0a997e95f650a5be699075ec214dd7d9c2d94a94d8c7a7becfd0397fd1008未改。
未找到可直接使用的revision-bound隔离dependency resolver；
现有prepare_runtime_dependencies.sh是固定Production路径/Pillow专用，未运行。
Skill Linux/Python3.11 wheel hash lock仍NOT_ESTABLISHED。
未pip install、升级全局Workbench或创建临时新依赖架构。

批准Native ZIP4a140c878ae7057583089a4410cd1a23c5c95664988e6dd9700f75ed51d3b18c，
descriptor0329daa05bfaff79c158807364d88003d3c1fdc88ebdf245d8bfa11a6bbfd490，
dependencies4320d2df0cc5c28780a8b6b4774b9961fba8f611e732f20d015d805d5e82ca1b保持；
ZIP13文件及每文件SHA fresh MATCH。未从dirty source复制或改ZIP/SKILL.md。
Runtime/action模块和66 tests仍是本地未提交实现，PRIMARY当前b25fc43 Source中没有它们。
没有sealed adapter Source安装输入，不把用户的Skill artifact授权扩大成dirty runtime patch。

Registry install、Agent binding、Task workspace、PRIMARY17/66tests、PREPARE smoke、
4case live triggers、security实测、CREATE_DRAFT credential-gate receipt：均NOT_EXECUTED，非PASS。
没有自动创建ProviderTask或微信凭据；所有WeChat/Provider/Image calls=0。

PRIMARY既有五个service active且PID未变；
API HTTP200/statusok但knowledge degraded；
MCP ping/instance1 PASS；PG ready PASS；Redis unauth probe NOAUTH，authenticated health未验证；
Test HTTPS401/BasicAuth/TEST/noindex可达，frontendOPEN/login未fresh复核，不能伪造全面health/页面PASS。
未重启、更改current/contract/env/Registry/binding或修复其他服务问题。
Production未连接，UNCHANGED_BY_THIS_TASK；未读取/Provision Secret或真实CREATE_DRAFT。

完整本轮报告：
[WECHAT_SKILL_PRIMARY_RUNTIME_ACCEPTANCE_V1_REPORT.md](WECHAT_SKILL_PRIMARY_RUNTIME_ACCEPTANCE_V1_REPORT.md)。

Next gate：由架构/总控先封存runtime adapter Source并确认正式revision-bound依赖provision/隔离方案及Linux wheel身份，
然后再按原审批运行PRIMARY import/pip check/17/66/正式安装/Binding/PREPARE。
本轮不临时实现新架构或绕过依赖/Source权限。
**WECHAT_SKILL_RUNTIME_DEPENDENCY_BLOCKED — STOP。**

## Historical integration report — preserved unchanged


Date: 2026-10-07 · Role: 01 / Agent Workbench Architecture.
Task attachment: `3b2140ca-e3f0-41cc-9c14-8179a6809dd7`.

**WECHAT_SKILL_CONTRACT_ADAPTATION_REQUIRED**

Native package adaptation, source locking, security hardening, offline PREPARE and Agent binding preparation are implemented and tested locally. Formal runtime activation remains blocked by the missing tenant Secret Reference / Skill Action permission executor and the unvalidated PRIMARY Python 3.11 dependency environment. Do not treat this report as `WECHAT_HTML_DRAFT_SKILL_TEST_READY` or enable the Skill in a real Agent yet.

## 1. Original Skill identity

Original: `C:/Users/猪猪/Documents/Codex/2026-10-05/new-chat/outputs/wechat-html-draft-skill.zip`.
SHA256: `a2f9aae56b1ad59cf9d4532fe9310bfb2827f43465d68e0a4db3746e8b2e14c3`.
The original ZIP remains unchanged. Its 12 individual file sizes/hashes are recorded in `enterprise_agent_poc/integrations/wechat-html-draft.original.v1.json`.

Read the supplied Skill instructions and its style/upload references. The instructions preserve offline formatting, explicit-upload intent, create/get verification and recovery behavior. No article, real draft, publish, mass-send, delete or old-draft replacement was requested or performed by this integration task. Historical claims in the references are not fresh verification of this integration.

Source was imported as a new tree with explicit LF normalization. Existing content/style/template/reference/PowerShell files are retained. Changes to the business script are limited to path guards, config-field rejection, controlled image fetch and an endpoint allowlist. Draft creation/deduplication/pending-outcome recovery/readback business functions remain intact. SKILL.md receives a Workbench boundary addendum; agents/openai.yaml remains original interface metadata.

## 2. Workbench identity / Revision / Source checksums

Skill slug: `wechat-html-draft`.
Display name: `公众号文章排版与草稿上传`.
Existing Registry field `skill_versions.version`: `1.0.0` (V1); Registry IDs remain its existing generated UUIDs.

New source root: `enterprise_agent_poc/skill_sources/wechat-html-draft/1.0.0`.
The 13 files are the original 12 plus `scripts/workbench_guard.py`.

Adapted Native ZIP:
`C:/Users/猪猪/Documents/ChatGPT/ky_web/.codex-wechat-html-draft-v1/wechat-html-draft-1.0.0.zip`.
SHA256: `4a140c878ae7057583089a4410cd1a23c5c95664988e6dd9700f75ed51d3b18c`.
Size: 69,071 bytes. Rebuild produced identical bytes.

Revision descriptor: `enterprise_agent_poc/integrations/wechat-html-draft.revision.v1.json`.
SHA256: `0329daa05bfaff79c158807364d88003d3c1fdc88ebdf245d8bfa11a6bbfd490`.
It binds original ZIP, source root, every file SHA/mode, Native artifact SHA, entrypoint, dependency contract SHA, actions and proposed binding. `verify_revision_contract()` rejects drift before import/prepare. `import_revision()` rejects a changed existing V1 even if the Registry row is still draft; changes require a new revision.

The descriptor is an auditable integration contract, not a new Registry/database schema. Existing persistence remains `skills`, `skill_versions`, `skill_packages`, with published versions immutable. No bundled bootstrap manifest or old Skill identity was changed.

## 3. Existing formal components inspected

| Contract | Existing implementation / finding |
| --- | --- |
| Registry / Revision / Native Source | `app/skill_registry.py`: NativeSkillArchive and SkillRegistry import/test/publish/checksum checks |
| Source identity / package builder | `app/bundled_skills.py`: deterministic Native ZIP builder and immutable bundled source/artifact identities |
| Skill Deployment | `app/skills.py`: existing published-package deployment to Codex skills directory |
| Native runtime executor | `app/runtime/codex_provider.py`: Codex native Skill discovery and profile-scoped workspace/sandbox |
| Agent Skill Binding | `app/agent_productization.py`: `agent_template_version_skills`, exact published Skill/Version IDs, draft-only `bind_skills` |
| Permissions | `app/domain.py` sandbox policy and `app/security.py` MCP scopes; tool capabilities currently cover config/knowledge/assets/image, not WeChat actions |
| Dependencies | Project pyproject plus explicit managed-runtime dependency preparation; no per-Skill dependency executor/environment resolver |
| Credentials | Existing settings hold process-local provider credentials; no tenant-scoped WeChat Secret Reference resolver or account-binding contract was found |

No second Skill Registry was created. Local tests import/test/publish through the existing methods, deploy with SkillDeployment, and bind a synthetic existing productized Agent's draft with `AgentProductization.bind_skills`. Application HTTP control-plane authentication remains required for real Registry operations; the adapter is not a new public endpoint or MCP permission bypass.

## 4. Runtime / Dependencies

Linux entrypoint remains `scripts/wechat_draft.py`; PowerShell is a retained local helper. Python requirement >=3.10; Workbench target 3.11.

Observed local behavior-test interpreter: bundled Windows Python **3.12.14**. Python 3.11 syntax parsing passed for the adapter and all Skill Python files; **this is not execution compatibility proof** for 3.11.

Exact dependency version contract: `integrations/wechat-html-draft.dependencies.v1.json`, SHA `4320d2df0cc5c28780a8b6b4774b9961fba8f611e732f20d015d805d5e82ca1b`.

| Direct dependency | Locked observed version |
| --- | --- |
| requests | 2.34.2 |
| beautifulsoup4 | 4.15.0 |
| css-inline | 0.20.2 |
| Pillow | 12.3.0 |
| tinycss2 | 1.5.1 |

Transitives are also exact-version recorded in that JSON: certifi, charset-normalizer, idna, soupsieve, typing_extensions, urllib3 and webencodings. The versions come from the supplied Skill task's existing local dependency directory; no dynamic installation was performed. `check_dependencies()` refuses missing or different versions at execution time.

**WECHAT_SKILL_RUNTIME_DEPENDENCY_BLOCKED remains an unresolved sub-gate:** no PRIMARY Linux/Python 3.11 execution or Linux wheel-hash closure was established. Version locking alone is not a platform-specific wheel supply/installation approval. Do not copy the Windows dependency directory to PRIMARY. Use a separately controlled environment provision, verify Linux wheel hashes and versions, then repeat offline behavior/compatibility tests.

## 5. Credential / Account Config contract

The original standalone script retains its local environment-variable usage. The Workbench adapter does not inherit WECHAT_APP_SECRET or WECHAT_ACCESS_TOKEN for PREPARE and never resolves them from examples or article.json. Unknown article config fields, including credential fields, are rejected without echoing values.

Formal account authority must come from Enterprise/Agent configuration, keyed by tenant and stable Agent identity, with AppID/display name and an opaque Secret Reference. `references/account.example.json` remains a reference only; its public AppID is not a live account binding. No config update or real credential read occurred.

The existing productized Agent editable schema excludes secret/account/tool metadata. No existing tenant WeChat secret manager was found, so the adapter cannot truthfully resolve a formal secret reference. CREATE_DRAFT rejects missing upload intent with `WECHAT_UPLOAD_NOT_AUTHORIZED`; even with explicit intent it currently rejects with `WECHAT_SECRET_AND_EGRESS_CONTRACT_REQUIRED`. An ambient token cannot bypass this gate.

Concrete required integration: an authenticated server broker receives tenant/task/Agent/account identity plus an opaque secret reference, resolves credentials server-side, and implements the original script's `api.request` / `api.upload` interface. `publish_to_draft` already accepts that interface, allowing its business logic to remain unchanged. The broker must keep raw secret/token values outside Skill arguments, prompt, article files, Registry metadata and logs. No new secret storage system or schema was invented in this task.

## 6. Workspace / Path Security

Workbench offline entry: `app/wechat_skill.py.execute('PREPARE', server_task_workspace, article_config)`.
The caller must supply the server-owned per-task directory, never a model-chosen root. The current profile workspace is not a per-task authorization primitive; live dispatch needs the formal task binding before activation.

The adapter runs the Python entry with fixed --check / --work-dir arguments and a scrubbed environment, captures helper output instead of logging it, and returns product-safe status. Input article.html/article.json/assets remain in that task directory. Outputs are run/prepared.html, run/preflight.json and workspace/verification.json. Original run/state.json is reserved for the original upload state machine and is not fabricated during PREPARE.

Checks cover HTML, cover, image_map values, CSS and local image paths after URL decoding / resolve. Absolute paths, Windows drive/UNC paths, URI/alternate-stream colons, traversal and resolved escape are rejected. Output/state/temp/lock target paths are also checked. Neither article outputs nor virtual environments are written into Skill installation paths. No Source checkout files are used as article output targets in the tests.

Containment is an application guard, not an OS sandbox against a concurrently hostile process swapping filesystem objects. An isolated task executor must own the workspace; this remaining runtime contract is not claimed by plain Python path checking. The symlink-escape test deterministically simulates realpath resolution; native Linux symlink/permission integration is pending PRIMARY validation.

## 7. SSRF / Network permissions

`scripts/workbench_guard.py` handles image GETs with these rules:

- HTTP/HTTPS only, conventional ports, no URL userinfo, localhost/internal aliases rejected.
- Resolve DNS and require every answer to be global/non-multicast; loopback/private/link-local/metadata/unspecified and IPv4-mapped private addresses reject.
- Connect to a validated numeric IP without a second hostname lookup; TLS retains hostname verification/SNI. No ambient HTTP proxy is used for this image transport.
- Follow at most five redirects; resolve and validate every new target before connection. Public→private redirects reject.
- Require successful image Content-Type and limit bytes to the existing 10 MiB guard, followed by original Pillow image validation.

The original WeChat request method now allowlists only token, material/add_material, media/uploadimg, draft/add and draft/get, with the correct methods. Redirects remain disabled. No publish/mass-send/delete/update endpoints are allowed.

PREPARE does not download remote images, resolve their DNS or call WeChat. It reports remote images as offline-unverified. This guard is not permission to grant the Skill arbitrary outbound access. Formal CREATE_DRAFT must use api.weixin.qq.com:443 through the server broker and a separately controlled image-fetch capability. The current Runtime lacks those Action-specific egress grants, so upload dispatch is disabled.

## 8. Agent Binding / Trigger rules

Target stable slug: `wechat-official-account-writing`; display name changes do not affect identity.

`binding_plan()` reads existing templates, requires exactly one productized target and an explicitly selected draft Revision, requires the exact published Skill checksum, and preserves other bindings. It returns the existing formal `AgentProductization.bind_skills` arguments. It does not create an Agent, alter prompts, publish an Agent, enable an instance, or apply a live binding. Plans remain `runtime_binding_authorized=false` until the Action permission contract is implemented.

The local source CATALOG has three native Agents; the target公众号 Agent uses the productized path rather than a newly added native definition. No live database was read, so this task does not claim the target Agent or binding currently exists in PRIMARY or Production. Missing/ambiguous target must block instead of creating a duplicate.

`action_for_request()` classifies公众号文章/排版/标题/摘要/封面 requests as PREPARE, explicit upload/create-draft requests as CREATE_DRAFT, and ordinary chat/other tasks as no invocation. Negative upload wording maps to offline preparation when relevant. Classification is not an upload authorization token; account/action authorization still belongs to the server. No new auto-dispatch was installed into Agent Runtime.

## 9. Tests

| Validation | Result / scope |
| --- | --- |
| Original ZIP's unchanged scripts/test_workflow.py | **17 PASS / 0 failed / 0 skipped**, original script loaded from ZIP |
| Adapted original behavior suite + integration/security tests | **40 PASS / 0 failed / 0 skipped = 17 retained + 23 new** |
| Native package inspect / deterministic rebuild | PASS, 13 files; identical ZIP SHA |
| Registry import/test/publish/SkillDeployment | PASS in isolated local SQLite fixture |
| Existing productized draft binding | PASS using the formal bind_skills method in a synthetic local fixture; adapter does not create duplicate Agent |
| Python 3.11 syntax | PASS; runtime compatibility NOT_EXECUTED |
| PRIMARY Test execution / real Agent Runtime | NOT_EXECUTED |
| Real WeChat draft smoke | NOT_AUTHORIZED / NOT_EXECUTED |

New tests cover Skill/revision loading, source drift, immutable version handling, existing Agent identity/rename/binding, no-credential PREPARE outputs, CREATE_DRAFT intent/credential-contract rejection, HTML/CSS/cover/image_map escape, symlink resolution escape, private/mixed DNS/localhost/link-local targets, public URL, redirect rejection, pinned connection, secret-field redaction, denied endpoints/actions and ordinary chat noninvocation.

The initial standard temporary-directory run hit Windows sandbox ACL/realpath restrictions. The reviewed offline runner preserves test-directory ACL inheritance and was allowed to run outside that restriction; it did not loosen production guards. Mock output saying “草稿创建成功” is from FakeAPI with draft-id, not a live request.

Attempted three existing pytest files (Registry/Deployment/Bundled Skills) did not collect on this Windows environment: first sandbox ACL restriction, then existing Linux-only tests/conftest.py calls `os.getuid`. They are **NOT_RUN**, not skipped/PASS. No full pytest or Stage 2 was run. Formal Registry/Deployment operations are exercised by the passing new unittest fixtures; no existing test or platform gate was rewritten.

Runner: `enterprise_agent_poc/scripts/verify_wechat_skill.py`.
Local commands, using an explicitly provisioned dependency environment:

```text
python -B -X utf8 enterprise_agent_poc/scripts/verify_wechat_skill.py --original-zip <original-zip>
python -B -X utf8 enterprise_agent_poc/scripts/verify_wechat_skill.py
python -B -X utf8 enterprise_agent_poc/scripts/verify_wechat_skill.py --build-package <new-artifact-path>
```

The build command refuses to overwrite a different existing artifact. It is an offline Native Skill artifact builder, not a Candidate Build or deployment command.

## 10. Concrete closure boundary / handoff

Current deliverables are reviewable code, fixed source/package/dependency descriptors and offline tests. Before returning WECHAT_HTML_DRAFT_SKILL_TEST_READY:

1. Extend the existing runtime capability/permission path with server-enforced PREPARE/CREATE_DRAFT task authorization, workspace isolation, permitted network destinations and upload-intent evidence. Do not rely on SKILL prose or the classifier to enforce permission.
2. Supply the formal tenant/account Secret Reference resolver and existing account-authority configuration contract; broker-side credentials only. No key in chat or direct environment fallback.
3. Establish a reviewed Linux Python 3.11 dependency/wheel identity, bound to the candidate Source/artifact, then run offline compatibility and targeted regressions in PRIMARY with fresh baseline. The current repository authority reference approves only its fixed historical Source, not this new working tree or a silent dependency upgrade.
4. Locate the existing target Agent, use its explicit draft Revision and the existing publish/bind workflow; retain other skills and history. Verify Skill discovery and PREPARE under the actual restricted runtime before enabling the binding. CREATE_DRAFT stays disabled pending separately authorized real WeChat smoke.

These are missing implementation/validation contracts, not a request to repeat an already authorized upload or Test permission. No PRIMARY mutation, secret access or real API call was used to substitute for them.

## 11. Changed files / Safety

- `app/wechat_skill.py`: Native package/revision verification, existing Registry import, read-only Agent binding plan, action classification and gated offline execution.
- `skill_sources/wechat-html-draft/1.0.0/`: retained original structure plus audited security changes/new guard.
- `integrations/wechat-html-draft.{original,revision,dependencies}.v1.json`: provenance and version contracts.
- `tests/test_wechat_skill_integration.py`, `scripts/verify_wechat_skill.py`.
- This report and the generated offline ZIP.

No existing Runtime, Prompt, Schema, migration, model or Production file changed. Existing user changes to .gitignore and RELEASE_TEST_ENVIRONMENT.md are preserved. No commit/push was requested or performed.

The original user ZIP and historical directories/reports are retained. Successful isolated test workspaces (including their disposable synthetic SQLite fixtures) were removed by the test harness; no live/historical data was cleaned. Failed sandbox temporary directories were not forcibly cleaned outside the workspace.

Provider/Image/WeChat calls=0/0/0 · PRIMARY operations=0 · **PRODUCTION_UNCHANGED**.

STOP.

## 2026-10-07 follow-up: Revision-bound Python Runtime V1

本段为后续记录，原报告的历史状态/Source/Native identity 不回写。
本轮已提供 `app/skill_python_runtime.py`、独立 runtime descriptor、完整 Linux CPython3.11 x86_64 wheel lock 和受控 offline installer/resolver；PREPARE 正式 adapter 启动不再使用主 `sys.executable`。12 个 wheels 的官方 SHA 与完整 closure 均 PASS，无 source build，无全局 pip install。

Adapter 分支：`codex/wechat-revision-python-runtime-v1`；精确 commit/tree 采用提交后外部 seal 交接，见 `WECHAT_SKILL_REVISION_RUNTIME_V1_REPORT.md` 与 `.codex-wechat-revision-runtime-v1/adapter-source-seal.json`。旧 Windows dependency descriptor 保留，新 Linux lock SHA：`3a5f482475c7cafe660df2d171f369a32832ac91e12e9b0ddffde5c5d6ac8abe`。

本轮离线：原始17 PASS、adapted66 PASS、新runtime29 PASS；无失败/跳过。Linux probes/install I/O 是合成 fixture，不是 PRIMARY execution。PRIMARY 未安装，实际 Agent runtime dispatch/discovery/专用 venv3.11验收仍必须下一轮完成；CREATE_DRAFT network runner仍不启用。Native ZIP及13 checksum不变，Provider/Image/WeChat=0/0/0，Production unchanged。
