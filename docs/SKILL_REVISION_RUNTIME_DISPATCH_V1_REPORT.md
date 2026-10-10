# SKILL_REVISION_RUNTIME_DISPATCH_V1_REPORT

日期：2026-10-07。窗口：01 / Agent Workbench Architecture。
任务附件：`48434713-aea5-4f83-a5ae-52b760faca62`。

**WECHAT_SKILL_DISPATCH_READY** — Source-level dispatch + deterministic Agent/MCP tests。不是 PRIMARY live READY，也不是真实 Provider/WeChat smoke。

## 1. Dispatch Source / precise Git scope

Branch：`codex/skill-revision-runtime-dispatch-v1`。
Base：`e7e96b1a959d8631dc9dcd5c24939fa483ac134a`。
DISPATCH_SOURCE / DISPATCH_TREE 为本报告首次进入该分支的提交及 root tree；最终交接直接给出精确值，提交后外部 seal 记录于 `.codex-skill-dispatch-v1/dispatch-source-seal.json`，不将 commit hash 写入自身形成递归。

只提交 dispatch bridge、正式 MCP/Task scope integration、normalized result/audit、tests、contract 和本轮报告更新。不包含既有 `.gitignore`、release environment 文档、历史数据、wheel cache 或 credential。任务开始时三份报告已有用户/06未提交更新：工作树内容完整保留，两份要求更新的报告只将本轮专属标记段追加到 HEAD/index，不把原有 dirty 段纳入提交。

## 2. Real missing link and smallest implementation

原 MCP 的 `wechat_prepare_authorize` 只解析权限并返回 NOT_EXECUTED；没有 execution bridge。此外 TaskService/AgentService 原本只有 reference-image flow 传递 Task ID，普通公众号 Agent 的 session header 不构成签名 Task scope。

本轮新增 Tool capability `skill_action_execute` / scope `skills:execute`，通过既有 catalog、DB tool scopes、Runtime bearer 和 HMAC Task header授权；不新增 permission system。新增能力为 optional，不能迫使普通聊天执行 Skill。

```text
TaskService.execute
  → AgentService.run (current context / Run)
  → Runtime session with signed Task header
  → model requests existing Platform MCP skill_action_execute
  → current DB Tool + Agent/Skill Revision binding
  → existing Action permission
  → exact published Skill Revision / package checksum
  → sealed revision Runtime.resolve
  → fixed argv / PREPARE --check
  → per-Task workspace outputs
  → normalized Skill Result / safe execution receipt
  → MCP result to Agent
  → Agent continues reply / existing result persistence
```

没有旁路 Runner 服务、第二 Registry/Control Plane、直接脚本冒充 Agent smoke、Prompt/Skill/Model 改写或新 Schema/Migration。

## 3. Reusable bridge / formal bindings

`app/skill_dispatch.py` 的 `SkillActionDispatcher` 负责 auth、Task/Run、exact Registry binding lookup、runtime interface、argv、workspace、normalization contract 与 audit。`ActionRegistration` 是受信任 Source 的静态 adapter/runtime/action 描述，不是持久化第二 Registry，也不接受模型注册或执行路径。

公众号内容特性仅在 `WechatPrepareAdapter`（输入/输出）与现有 `WechatActionContract`。Core dispatcher 不写死公众号 Agent/Skill identity。测试还正式 import/publish/bind 第二个 mock Native Skill，复用同一 MCP/core execution bridge。

`add_dispatch_binding()` 通过现有 `AgentProductization.bind_tools()` 给明确 draft 增加 optional generic capability、保留其他 tools。真实 authoring API 的 platform-admin gate仍必需；helper不自动 create/publish/enable。已有 `wechat:prepare` / `wechat:draft:create` scopes仍需同时满足，generic scope不是业务 action 授权。

## 4. Exact Revision resolution

MCP 输入：skill_key、真实 `skill_versions.id` UUID、action、structured article。Tenant/Agent/Task/profile/workspace由 Runtime token、signed header、当前 DB context 推导，模型不能指定。

验证当前 Task running、当前 Run running、Task ↔ context ↔ Agent ↔ Tenant、Run/conversation association、current Tool capability、context Skill refs、draft/published binding row、package SHA与 Registry inspection。只接受绑定的 exact published UUID/version/checksum；`latest`、随机 UUID、缺失/废弃/改包/不匹配 Revision全部拒绝，不能根据目录名推测。

Task scope 的 create/resume 传递只对新增 capability（及原 reference-image flow）启用，不改普通 Agent session 的调用签名。新 capability不存在时旧流程保持。

## 5. Runtime contract / two separately sealed Sources

旧 runtime Source **不覆盖**：

```text
Runtime Source: e7e96b1a959d8631dc9dcd5c24939fa483ac134a
Runtime Tree: a86b9e4d2f4785f1cbef5ae5e654c54cf20a5f75
Runtime descriptor SHA: 1b7d02d983867a815c898a94944bf3660597fd513d1bf0310608100f9b70e769
Linux lock SHA: 3a5f482475c7cafe660df2d171f369a32832ac91e12e9b0ddffde5c5d6ac8abe
Native artifact SHA: 4a140c878ae7057583089a4410cd1a23c5c95664988e6dd9700f75ed51d3b18c
```

新 `integrations/skill-dispatch.v1.json` 显式声明对此 immutable runtime tuple 的兼容性。Current dispatch Source 与旧 runtime Source不能混淆：06保留独立、精确 e7 detached Source checkout作为 runtime.project，用新 Source运行当前 dispatch。新 app code不复制进旧41-file目录；否则旧 resolver本来就应失败。旧 Native13 /41 manifest/lock/Source seal均保持。

复用 `SkillPythonRuntime`：receipt/binding、venv files、Python3.11/Linux ABI、imports、exact package set、lock、artifact /Source 都必须通过；缺失即 SKILL_RUNTIME_NOT_READY，不自动 pip、不回退到主 sys.executable。`PythonRevisionBinding` 同时要求其 Native Skill Source files/directories实际不可写，直到06完成只读封存前不能执行。

Host binding 只读配置通过 `ENTERPRISE_POC_SKILL_DISPATCH_CONFIG` 选择服务级文件，纳入既有 safe config fingerprint names。文件包含 contract、dispatch_seal、runtime_project、runtime_root、runtime_approval（无 Secret）。Factory要求 Test环境、绝对路径、current dispatch seal/file hashes与旧 runtime tuple exact match；Production factory/未配置状态拒绝。它是 release-scoped artifact binding，不是新 admin/config API。

## 6. Entrypoint / input / workspace / no shell

PREPARE 固定 `scripts/wechat_draft.py --check`，用 resolved revision Python 与 `-I -B`、受信任 bootstrap，仅加入 sealed script目录。argv array、shell=False；用户不能选择 executable/script/Python/options/workspace。

Structured article精确字段：title、digest、html、cover_asset、assets。title/digest bounded，HTML≤256KiB，1–12个验证 PNG/JPEG/WebP assets、每张≤2MiB、总计≤8MiB；文件名仅有限 ASCII 图片名。拒绝 script/iframe/object/embed/form/link/base、event handlers、危险 URI/path及外部 CSS fetch语法；业务 preflight继续由正式 Skill执行。未知字段（含路径、options、Secret等）拒绝。

Workspace复用既有 Runtime profile目录：`data_dir/runtime/<tenant>/<agent>/<profile>/workspace/tasks/<task>/<invocation>`。每次调用独立命名，必须在 Source/Registry/venv之外，无 symlink escape。服务器写 article.html/article.json/assets，输出 run/prepared.html、preflight、safe verification、receipt。Skill Source仅只读，不上传到 OSS或其他外部 storage。

Child env仅 OS essentials +当前 workspace/temp；无 WeChat/Provider credential、PYTHONPATH、HOME/proxy inheritance。固定已批准 --check不走任何远程图片/Wechat API路径；本轮不声称通用恶意代码的 OS网络沙箱。未来 adapter必须独立批准 offline/egress语义，不能凭开放注册获得网络。

## 7. Permissions / CREATE_DRAFT / lifecycle

PREPARE走原 Scope/Trigger policy，但不读取账号配置或解析微信 Secret。普通聊天即使模型误请求该 tool也被拒绝，无 child invocation。

CREATE_DRAFT仍先经原 credential/account/user intent gate；任意缺失拒绝。即使该 gate成功，本轮 action registration仍 disabled，永不启动 --run/Wechat。发布、群发、删除、覆盖等 action未开放。

执行前及结束后重查 Task running/cancelling、Token/context/binding和Runtime身份；被取消/禁用/改绑不得发布 result。当前 asyncio.to_thread +同步 subprocess bounded timeout为120s：现有取消可阻止结果交付，但不宣称每次都即时终止已启动的离线 child；这是剩余生命周期观测项，不在本轮扩展 Stop backend。

## 8. Normalized result / safe errors / receipts

成功字段：status、action、artifact_refs、summary、verification、receipt_ref。artifact refs是当前 Task namespace相对引用、MIME、size、SHA，不是 local absolute path；generic core复核文件 containment/bytes/size/hash。summary bounded、verification仅数值/布尔 metadata；拒绝额外 stdout/env字段、危险/畸形 HTML及 malformed preflight。不返回 stdout/stderr、full article/title/digest/base64、stack trace、env dump或 runtime path。

错误保留并分别返回：SKILL_REVISION_NOT_FOUND、SKILL_RUNTIME_NOT_READY、SKILL_ACTION_NOT_ALLOWED、SKILL_EXECUTION_FAILED、SKILL_RESULT_INVALID；另有 SKILL_INPUT_INVALID。MCP CallToolResult.isError与 status一致，不统一吞成 tool execution failed。

每次可信 Task invocation经原 `store.log_event` 写 `skill.execution`，并在已建 workspace保存 safe receipt：Tenant、Agent、Task/Run、Skill/UUID/version/artifact、dispatch Source、runtime/workspace identity、start/end、exit status、result refs或 failure code/phase。未能建立可信 Task时不伪造 Tenant receipt。失败审计/写receipt也 fail closed。

`CodexRuntimeProvider` 对新 tool的 Run trace只保留 action、canonical Revision UUID、skill-key hash；article/assets被排除，失效 identity不原文记录。原产品 user message持久化规则未改，不把业务聊天存储误称成新 Skill审计。

artifact refs目前是现有 Agent workspace可消费引用，不承诺已提供新的浏览器 artifact download UI/API；该 UX交付层不属于本轮。

## 9. Tests and truthful boundaries

新 **31 PASS / 0 failed / 0 errors / 0 skipped**。使用已存在 Windows Python3.13.0 + MCP1.30.0、真实 FastMCP tool registration/schema/Context调用、真实 SQLite Task/Run/AgentService/TaskService/Registry/Binding/Token/permission/result persistence。Provider/Model、revision-runtime boundary和 child process I/O为明确 mocks，无 HTTP listener/actual App Server。

完整集成 case证明：TaskService → AgentService → signed Task scope → 实际注册 MCPtool → PlatformMCPService → exact Binding/Revision → runtime mock → 固定 PREPARE argv → scoped outputs/receipt → normalized result → Agent继续回复 →正式消息/Task completion。另测试实际 Codex session header代码和 Run trace归一化（AST加载实际 provider class，无重写方法）。不是独立Python script冒充Agent smoke。

覆盖用户18项要求；额外包含跨Tenant签名、child env scrub、取消后不得交付、trace脱敏、实际 header签名、disabled instance、第二 Skill复用、Production factory、unsafe HTML、可写 Source拒绝、generic result额外字段拒绝及 malformed signed header。

本地 MCP interpreter未安装无关 DOCX模块，最初 import/完整persist测试遇到该环境限制；fixture只隔离 Settings secret loader和无关 ActivityPlanContent符号，若触发DOCX action立即失败。真实 completion evidence未 mock/放宽。修正后完整case通过。不把这视为PRIMARY安装结果或完整产品环境验收。

原 adapted **66 PASS**、原 revision runtime **29 PASS**（Windows Python3.12.14），合计 **126 unique cases**；66含17 retained original cases，不重复统计。旧Runtime fixtures改为读取明确 e7 Git blobs，避免新 app代码与旧41-file seal混用；不是重写旧 descriptor让测试通过。

未 Full pytest/Stage2/Candidate Build、未 WSL/PRIMARY。需要下一轮 Linux native/permission/LLM dispatch证据，不能用本轮 mock proof关闭这些live gates。

## 10. 06 handoff / no installation this turn

1. Fresh attestation及新 dispatch Source的版本化 Test批准；不改旧sealed v1/v2或 Production合同。
2. fetch exact DISPATCH_SOURCE/tree；生成/核对 post-commit dispatch seal。保留 separate exact e7 Runtime checkout及其旧seal，核对Native13、41 files、lock；不覆盖旧Source/receipt。
3. 通过正式 Registry/authoring路径取得真实 published Skill UUID和已存在公众号 Agent的显式 draft。无UUID不得猜测/手工SQL/创建重复Agent。
4. 下一轮有授权时用旧e7 installer建设/验证 revision venv；独立 pip check/imports/17/66/29/31 tests及 readonly Native Source/Registry/profile安装验证。此步骤本轮未执行。
5. 写服务级 host binding（无Secret），以新Source启动/加载批准的 Test MCP/Worker code；任何 Source激活、service/env变更均需该轮权限与identity验收，不隐含在本轮READY中。
6. 原Skill/action绑定之外，用现有draft-only工具绑定添加 optional skill_action_execute，并走既有validate/Test publish/instance流程；不改live Production。
7. 真实 Test Agent PREPARE验证 signed Task/Run、exact UUID、actual venv Python、只读inputs/无外网、artifacts/receipt、MCP result与Agent回复/persistence。若仅script smoke/discovery/auth即STOP/BLOCK。
8. CREATE_DRAFT/Secret/WeChat全部继续disabled，不为补skip创建Provider任务或借用Production credential；完成后STOP。

## 11. Safety / changed files

新增 skill_dispatch.py、skill_dispatch_config.py、wechat_prepare_action.py、dispatch contract、verify/seal scripts、31 tests及本报告。最小改动：catalog capability、Settings只读binding引用、Platform MCP/service、TaskService→AgentService Task ID传递、Codex trace字段脱敏、旧29fixture精确Source读取。无 Skill/Prompt/Model/Schema/Migration或旧 runtime artifact identity改变。

PRIMARY changes=0。Production changes=0。Provider/Image/WeChat=0/0/0。无 wheel/venv install、live Binding activation、真实Admin/Agent provisioning、服务 restart/switch或DB/Redis mutation。

**PRODUCTION_UNCHANGED**。完成后 STOP。

<!-- TEST_CONTROLLED_SKILL_ACTION_V1_BEGIN -->
## Test-only Controlled Skill Action Entry V1 follow-up

`TEST_CONTROLLED_SKILL_ACTION_READY` is Source/offline readiness, not PRIMARY
installation or acceptance. The existing AgentRuntimeTest internal entry now
passes an authenticated native Test authority ticket through formal
ProductStore/TaskService/AgentService -> existing sealed MCP dispatcher, without
accessing RuntimeProvider. No public endpoint or second runner is introduced.

33 new +31 dispatch +66 adapted +29 runtime =159 unique offline PASS;
0 failures/errors/skips; actual Provider/Image/WeChat=0/0/0. Settings/HTTP/runtime
process are explicitly isolated/mocked; no real PREPARE is executed.

Original b0 dispatch and e7 runtime seals stay unchanged. The new API/Worker
Source must be separately approved and freshly attested with the existing b0
MCP. Ordinary eligible Agent gates remain; no deterministic Skill result is
fake Codex quality evidence. Configured/draft bootstrap remains out of V1 scope.
Details/06 prerequisites: `TEST_CONTROLLED_SKILL_ACTION_V1_REPORT.md`.

PRIMARY/Production unchanged; no install, provisioning or activation. STOP.
<!-- TEST_CONTROLLED_SKILL_ACTION_V1_END -->
