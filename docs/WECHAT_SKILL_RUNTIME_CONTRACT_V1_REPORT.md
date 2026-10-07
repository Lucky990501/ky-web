# WECHAT_SKILL_RUNTIME_CONTRACT_V1_REPORT

日期：2026-10-07。窗口：01 / Agent Workbench Architecture。
任务附件：`bd7ed38d-5416-4efa-85e2-5874fdcc32ef`。
本地基准 HEAD：`fc073b84008e6e8faee638ac4360635ca489fc83`。

状态：**WECHAT_SKILL_RUNTIME_CONTRACT_READY**。

本轮完成 Tenant Secret 引用与 Action 权限解析合同、最小实现和离线确定性测试。这是 Contract READY，不是上传执行器、PRIMARY 环境或真实草稿创建 READY。真实 CREATE_DRAFT 仍不执行；PRIMARY Python 3.11 dependency acceptance 保持未验收且本轮 OUT OF SCOPE。上一轮报告保留为历史证据，不回写其 Native Skill identity。

## 1. Tenant Config Contract

复用现有 `enterprise_configs.payload`、Enterprise Admin 配置 API 和 `ProductStore.update_enterprise_config()`；不新增表、Migration、账号配置服务或 Agent editable schema。

逻辑位置为 `payload.wechat_account`：

| 字段 | V1 约束 |
| --- | --- |
| account_display_name | 非空展示名，最多 120 字符 |
| wechat_app_id | `wx` + 16 个字母/数字；普通账号标识，不是 Secret |
| wechat_app_secret_ref | 可选、严格类型化 Secret Reference；不是 Secret 值 |
| wechat_access_token_ref | 可选、严格类型化 Secret Reference；不是 Token 值 |

拒绝未知账号字段。配置写入前递归拒绝 AppSecret / Access Token 等明文字段别名；`*_ref` 不能传入原始字符串或其他 Tenant 的引用。无凭据引用的账号配置可以存储，但不能获得 CREATE_DRAFT 权限。

此检查作用于本轮之后的配置写入，不是历史 DB 明文扫描/清理。本轮未读取或更改任何 live Enterprise Config。

## 2. Secret Reference Contract

现有 `app/settings.py` 使用进程环境作为凭据 backend；审计未找到既有 Tenant WeChat Vault 或类型化引用解析器。因此仅补充引用适配层 `app/tenant_secret_reference.py`，复用该 backend，不新建 Vault、Secret 数据库、Secret CRUD / provision 接口或 `.env` 加载器。

引用必须精确包含：

```json
{
  "provider": "runtime_environment",
  "tenant_id": "tenant-a",
  "environment": "test",
  "name": "account-a"
}
```

这是无凭据值的合成示例，不是真实账号登记。`name` 满足 `[a-z][a-z0-9-]{0,63}`；调用者不能任意选择环境变量名。

解析键规则：

```text
WORKBENCH_SECRET_<ENVIRONMENT>_<SHA256(TENANT_ID)>_<PURPOSE>_<SHA256(REFERENCE_NAME)>
```

Hash 使用完整 64 位十六进制大写。PURPOSE 仅允许 `WECHAT_APP_SECRET` / `WECHAT_ACCESS_TOKEN`，相互隔离。禁止回退到全局 `WECHAT_APP_SECRET`、`WECHAT_ACCESS_TOKEN` 或其他 Provider 环境变量。每个已登记引用都必须可解析为非空值；一个可用引用不能掩盖另一个失效引用。

Secret 值不写入 Enterprise Config、Registry、Prompt、article.json、命令行、审计或 workspace。测试显式传入合成内存 mapping，不读取真实凭据。未来 provisioning 只向受控服务 backend 提供独立 Test 材料，不向模型进程/通用 Worker 配置复制这些变量；本轮没有实施 provisioning。

## 3. Secret scope

隔离维度：Tenant + Environment + Purpose + Reference name。
Environment 仅允许 `test` / `development` / `production`，运行环境来自受信任服务器 Settings，不是模型参数。注入自定义 resolver 时，其环境也必须匹配合同环境。

Tenant 和 Environment 验证发生在 backend lookup 之前。Test 解析 Production 引用直接拒绝，测试确认 backend read count = 0；Tenant A 不能选择 Tenant B 的引用。无 ambient fallback。这里证明的是解析器隔离，不声称建立了新的 OS Vault 或完成 live 凭据存储验收。

## 4. PREPARE permission

映射到现有 Tool Capability：`wechat_prepare_authorize` → scope `wechat:prepare`。

用途仅为 Task workspace 读写、Skill assets/references 读取、HTML/CSS inline、local image processing、offline validation。不需要账号配置、Wechat Secret、Wechat egress 或外部发布权限。

已有 `wechat_skill.execute('PREPARE', server_task_workspace, article_config)` 保持固定 `--check`、限定路径和 scrubbed environment。测试实际生成离线 prepared.html / preflight.json / verification.json；远程图片不下载。没有 Secret 时 PREPARE 权限和离线生成均可通过。

## 5. CREATE_DRAFT permission

映射到现有 Tool Capability：`wechat_create_draft_authorize` → scope `wechat:draft:create`。

该 capability 的实现是 **权限解析端点**，不是创建草稿端点。成功 receipt 固定为 `PERMISSION_RESOLVED_NOT_EXECUTED`，`draft_media_id = null`。不得将 capability 的 `implemented=true` 解读为上传执行器已实现。

授权同时要求：

1. 现有 Runtime V2 token 的 scope、context/profile/instance 和当前 DB capability 通过正式 `authorize_tool()`。
2. 现有 HMAC Task scope 有效；Task 属于同一 Tenant / Agent / execution context 且仍 running。
3. Agent 稳定 slug 是 `wechat-official-account-writing`，Revision 绑定精确 published Skill V1，context snapshot、DB checksum 和 package SHA 一致。
4. 当前 Task 持久化的用户原文明确要求上传到公众号/草稿箱、创建公众号草稿或放到草稿箱。
5. 当前 Tenant Account Config 有效，所有已登记 Secret References AVAILABLE。
6. 该 action scope 的网络目标仅为 `api.weixin.qq.com:443`；PREPARE 无此目标。

任何条件缺失 FAIL CLOSED。网络目标是 permission contract 声明，不是已配置的防火墙、实际网络开放或真实 WeChat 连通性证明。没有任意 host 参数，也没有开放 publish / mass send / delete / overwrite。

此前 Native Skill 的 API endpoint allowlist 保持不变，只包含 token、素材/图片上传、draft/add 和 draft/get；本轮不执行这些路径。真实上传还必须经过受控执行器及独立验收，不能直接调用原始脚本绕开合同。

## 6. Action authorization flow / Trigger 分离

```text
Existing MCP bearer + X-Runtime-Execution-Scope
  → RuntimeTokenIssuer 验签 / Task scope 验签
  → authorize_tool：当前 DB scopes / bindings / instance
  → Task ↔ execution context ↔ exact Agent/Skill Revision
  → 持久化用户 input_text：trigger + explicit action intent
  → CREATE_DRAFT 专属：Tenant account + scoped secret availability
  → sanitized permission receipt / existing execution_events
```

两个 MCP tool 不接收模型传入的 Tenant、Task、账号、Secret 或 intent 参数。Transport 必须提供已有签名 Task header；禁止 session-id / legacy bearer compatibility fallback。Legacy token、错误签名、跨 Tenant、disabled instance、未绑定 capability 均拒绝。

普通聊天：不触发。写/排版公众号文章：PREPARE。上传到草稿箱：CREATE_DRAFT intent。触发 Skill 不等于授权外部写；仅写文章不能获得 CREATE_DRAFT。

intent 使用保守确定性规则，要求直接命令句式或明确的“请/帮我把…上传”句式；排除引号/代码块/Markdown 引文、历史叙述、教程/条件/假设/询问和否定上传用语。模糊请求可能被拒绝，不由模型自报授权。该 V1 规则不是通用自然语言授权证明，未来扩充表达需附带反例回归。本轮未安装 Runtime 自动触发器或修改 Prompt。

## 7. Agent Skill Binding

复用 `SkillRegistry` import/test/publish、现有 `AgentProductization.bind_skills()` 和 `bind_tools()`。

`wechat_skill.binding_plan()` 要求唯一已存在的 productized 公众号 Agent、显式 draft Revision 和精确 published Skill，保留其他 Skill/Tool bindings，增加两个 **optional** authorization tools。没有新增 live Agent、自动 publish、instance enable 或新的 binding 表。

`apply_binding_plan()` 仅为现有 platform-admin authoring 流程的内部辅助方法，不是绕过身份验证的新 public API。两次 draft binding 写入不是跨方法原子事务；失败留在 unpublished draft，不能据此启用 live instance。

本轮在合成 SQLite fixture 中正式 import/publish 后绑定，真实测试 token / Task / context 验证两个 action。实际 PRIMARY/Production 公众号 Agent 和 bindings 未读未改；不得宣称已 live 绑定。缺失/多个目标必须阻断而非创建副本。

`runtime_binding_authorized=false` 保持，原因现在是 `PRIMARY_DEPENDENCY_ACCEPTANCE_REQUIRED`。稳定 slug 不随展示名“公众号运营助手”变化，历史 route/身份不改。

## 8. Runtime temporary secret injection

服务器内部 `prepare_secret_injection()` 在未来子进程启动前重新核验当前权限、Task intent、Config 和 References，返回不可作为 API/model result 的 `SecretLease`。

`lease.child_environment()` 仅继承 PATH / SYSTEMROOT / WINDIR / TEMP / TMP，临时提供 WECHAT_APP_ID + WECHAT_APP_SECRET 和/或 WECHAT_ACCESS_TOKEN。禁止继承其他 Tenant/Provider Secret；不修改全局 `os.environ`，不写 args / 文件。

输出必须在 lease 关闭前经 `redact()` 脱敏；异常转换为静态安全错误。退出 scope 时清空临时 dict 和 lease，关闭后的 lease 不能复用或继续脱敏。Python 字符串不能保证物理内存抹除，backend 仍持有其自身凭据；这里不声称 cryptographic zeroization。

本轮测试验证合成注入、隔离、脱敏、异常和 scope cleanup，**没有启动带真实 Secret 的 Skill 子进程，也没有实现网络 runner**。未来 executor 必须捕获/脱敏输出、限制 egress、保持 workspace 隔离，不得凭这个 hook 自动启用上传。

## 9. Audit receipt

复用 `store.log_event()` → `execution_events`，event_type 为 `wechat.action.permission`，不新增审计系统或 Schema。

receipt 包含：contract、tenant_id、environment、agent_id、agent_revision_id、task_id、skill_revision_id、skill_slug/version/checksum、action、account_config_identity、execution_status、draft_media_id、network_targets、UTC timestamp。

account_config_identity 为账号标识/引用 metadata 的 canonical SHA256，不含凭据值。PREPARE 无账号 identity / network target。授权成功状态为 `PERMISSION_RESOLVED_NOT_EXECUTED`；已验证 Task 的账号/凭据拒绝为 `BLOCKED_NOT_EXECUTED`，无网络目标。

两者 draft_media_id 均为 null，不虚构草稿 ID。今后真实执行须追加实际执行/回读状态及非 Secret draft ID；本轮未测试或宣称真实创建 receipt。无效 token / Task / binding / intent 在可信 Task 建立前拒绝，不伪造可归属 Tenant 的 receipt。日志不记录 Secret 值，SecretLease repr 也固定 redacted。

## 10. Tests / evidence

离线 runner：`enterprise_agent_poc/scripts/verify_wechat_skill.py`。
本地证据：`.codex-wechat-runtime-contract-v1/local-validation.json`。

最终两次完整本地运行均 **66 PASS / 0 failures / 0 errors / 0 skipped**：17 retained Skill behavior + 23 retained integration/security + 26 new contract tests。两次运行分别 4.327 / 4.385 秒，exit code 均为 0。

| 用户要求的确定性场景 | 结果 |
| --- | --- |
| 无 Secret PREPARE | PASS：权限解析 + 已有离线生成回归 |
| 无 Secret CREATE_DRAFT | BLOCK，测试 PASS |
| 合成 Test Secret CREATE_DRAFT permission resolution | PASS / NOT_EXECUTED / null draft ID |
| 普通聊天 | 不触发，PASS |
| 写公众号文章 | PREPARE，PASS |
| 上传到草稿箱 | CREATE_DRAFT intent，PASS |
| trigger 未要求上传 | CREATE_DRAFT 拒绝，PASS |
| Tenant A 读取 Tenant B | 拒绝，PASS |
| Test 读取 Production | 拒绝且 backend 0 reads，PASS |
| Secret log redaction | receipt、repr、child output / exception、cleanup PASS |

另覆盖明文 Config 拒绝、AppSecret/Access Token 两种引用、签名 Task header 必需、legacy/cross-Tenant token 拒绝、disabled instance、unbound action、禁止其他 actions、缺账号、环境 mismatch、凭据失败审计及全局 ambient Secret 不回退。

测试通过真实 RuntimeTokenIssuer、ExecutionResolver、authorize_tool、Registry 和 draft binding，不 mock 权限判定。26 个新测试仅用合成内存 Secret 和独立本地 SQLite；现有 FakeAPI 输出“草稿创建成功 / draft-id”是 mock，不是 WeChat 网络调用。

两个中间扩展运行曾各因 Windows 对隔离 Registry package rename 的 PermissionError 失败。正式 Registry/rename 逻辑未放宽：夹具改为一次正式 import/publish 不可变包，每项测试独立 SQLite 副本；最后连续两次完整运行通过。测试临时目录使用 workspace 内继承 ACL 的短路径，仅清理自身成功 disposable fixture；历史/失败证据不自动清理。

Windows Python **3.12.14**，使用用户已提供的本地 dependency directory；无 pip install。10 个相关 Python 文件的 Python 3.11 AST syntax PASS，不等于 Python 3.11 execution acceptance。Native Source lock 与 ZIP SHA 再核对 PASS，tracked implementation `git diff --check` PASS。

未启动真实 FastMCP transport；签名 header 的实际纯函数通过 AST 提取测试，服务端封装做 syntax review。此前标准 pytest 受 Windows/Linux-only conftest 限制未收集，仍 NOT_RUN；不把它计为 skip/PASS。本轮未 Full pytest / Stage 2 / Candidate Build / PRIMARY 验证。

## 11. PRIMARY unchanged

PRIMARY operations = 0。未 SSH、安装/加载代码、改服务/env、读凭据或更改 DB/Redis。未创建真实 User/Admin/Agent/Runtime Test/Instance、未启用 Provider。封存 Test contracts / Candidate manifests 和 fixture epoch 规则不变。

后续需另行受控完成 PRIMARY Linux Python 3.11 dependency/wheel identity 和当前 Source 合同验收，然后定位已有公众号 Agent 显式 draft，验证 restricted runtime discovery / PREPARE，再决定是否允许受控 CREATE_DRAFT executor 和单独授权的 WeChat smoke。本轮不授权这些步骤。

## 12. Production unchanged / Changed files

本轮新增：`app/tenant_secret_reference.py`、`app/wechat_action_contract.py`、`tests/test_wechat_action_contract.py`、本报告和离线 evidence JSON。

本轮最小适配：

- `app/product_store.py`：Wechat account config 写入校验。
- `app/agent_productization.py`：现有 capability 注册两个 authorization scopes。
- `app/platform_mcp/service.py` / `server.py`：复用既有 token/header 的权限解析入口。
- `app/wechat_skill.py`：已有 Agent draft 增加 optional action binding plan / helper。
- `scripts/verify_wechat_skill.py`：纳入合同测试。
- `tests/test_wechat_skill_integration.py`：Windows disposable fixture 的短路径和清理约束。

未更改 Product Runtime executor、Skill V1 Source、Prompt、Model、Schema、Migration 或 Production 权限配置。原始 ZIP、Native ZIP、Revision 和 dependency descriptor identity 保持：

```text
Original ZIP: a2f9aae56b1ad59cf9d4532fe9310bfb2827f43465d68e0a4db3746e8b2e14c3
Native ZIP:   4a140c878ae7057583089a4410cd1a23c5c95664988e6dd9700f75ed51d3b18c
Revision:     0329daa05bfaff79c158807364d88003d3c1fdc88ebdf245d8bfa11a6bbfd490
Dependencies: 4320d2df0cc5c28780a8b6b4774b9961fba8f611e732f20d015d805d5e82ca1b
```

既有 `.gitignore` / `deploy/RELEASE_TEST_ENVIRONMENT.md` 用户修改及历史目录保留；未 commit/push/deploy。以上新增/适配仍待代码 review，不是 sealed Source approval。

Provider / Image / WeChat = **0 / 0 / 0**。PRIMARY unchanged。**PRODUCTION_UNCHANGED**。

STOP。
