# RAG Native Application 后继代码候选

本候选为 8ff 增加 Test-only Application 启动、受保护双身份加载和只读恢复决策接口。2026-10-11 Linux 隔离定向组件验证为 **149 passed / 0 failed / 0 errors / 0 skipped**，不是正式 Native 验收，不具备安装或切换资格。

## 版本与变更范围

- 原始 RAG Source：`8ff8ddc732d31bb648fa82227097e1b120c9ec64`。
- 原始 Tree：`63d705caa0952679d95502424e49a327cd967c49`。
- Exact Test Predecessor：`d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7`。
- Predecessor Tree：`6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5`。
- 分支：`codex/rag-native-application-successor-v1`，从精确 8ff 创建；封存提交须只有一个直接父提交 8ff。
- Application 与必要 Tooling 模块随同一新提交交付，但未来安装目录和文件清单必须分别校验。最终 Source/Tree 和 fresh remote MATCH 由独立封存证据记录，不能自行引用旧 8ff、28e 或 2c91 的审批。

变更文件：

| 文件 | 内容 |
| --- | --- |
| `enterprise_agent_poc/app/test_runtime_tooling.py` | `verify_checkout`、`load_pair`、`authority_root`、精确双身份和受信任 Loader |
| `enterprise_agent_poc/scripts/test_rag_native_successor.py` | Test-only 启动 Guard、知识数据扩展校验、只读 `recovery-plan` |
| `enterprise_agent_poc/app/main.py` | 业务导入前启动 Guard；Native 路径禁止隐式 bootstrap DDL |
| `enterprise_agent_poc/app/worker.py` | Worker 导入及队列处理前 Guard；Native 路径禁止初始化 DDL |
| `enterprise_agent_poc/app/platform_mcp/server.py` | MCP 导入前 Guard；Native 路径禁止初始化 DDL |
| `enterprise_agent_poc/tests/test_rag_native_successor.py` | 69 项正向与负向组件验证 |
| 本报告 | 证据、边界和安装依赖 |

未修改 RAG 业务实现、图片实现、Migration001–015、旧 d8a Guard/Approval、2c91 Trial 合同、f8b/7b6 或 Production 合同。原始 8ff 保留。普通非 Native 开发/组件路径保持原行为；本分支不得作为已批准的 Production Application 使用。

## 受保护 Application 接口

合同为 `RAG_EXACT_TEST_RUNTIME_PAIR_V1`。它不兼容旧 28e/2c91 审批，不自动授予 Admin、Provider、安装或发布权限。

`authority_root` 只接受固定安装根，不由环境变量或调用方提供任意脚本目录。固定 Test 安装目录会自动触发校验，不能用 `ENTERPRISE_POC_TEST_RAG_NATIVE_REQUIRED=false` 关闭。APP_ENV 必须为 test，Production authority 必须为 false，Provider/WeChat/Image budget 必须均为整数 0。

`load_pair` 要求独立 Root 只读审批和 Application/Tooling 各自的 Source、Tree、安装路径与完整文件清单。Application 必须是 8ff 的直接后继。`verify_checkout` 同时验证安装 SHA-256 清单与 Git tree 的 blob 身份，拒绝额外文件、错误 Source/Tree、非受信任路径或 Git 文件类型。

Git 执行前验证 `.git`、外部 gitdir、commondir 及其目录图的 Root ownership、权限和非 symlink 条件，拒绝 alternates。Git 仅使用本次命令精确路径的 safe.directory、隔离配置、禁用 hooks/fsmonitor；没有全局通配或历史信任修补。仅在双身份验证成功后加载白名单 Tooling 模块，拒绝外来缓存模块及 namespace。

API/MCP/Worker 均在业务导入前执行校验。Worker 导入 main 时仍需通过 API 校验，没有 worker-import 环境变量绕过。三服务配置 CWD、实际 PID CWD、入口模块及版本必须一致；ACTIVATING 的暂未启动状态不能冒充 COMMITTED 健康。实际健康验收仍需正式 Native 演练。

## Schema 与知识数据合同

Schema001–015、Migration NONE、Data Contract `member_account_status_v1`。引用 06 已接受的 Schema Pin `7b1ee4f00c1ba59c0ecd69ae3fdb194708ceca8b157e2879d16b77ae9b6f13b7`，PG16.6、pgvector0.8.6、`knowledge_chunks.embedding=vector(128)`。本轮没有重取 PRIMARY 数据库证据或执行 DDL。

Native 路径要求已有完整 Schema，禁止 API/MCP/Worker 隐式初始化，禁止 demo bootstrap，使用 Redis 队列及 local-hash128。Schema/version/dimension 检查只读；既有 Provider-disabled Test 环境约束继续保留。

新数据准入需要额外独立 Root 审批的 `RAG_NATIVE_D8A_DATA_CONTRACT_V1`，不是重用历史审批来授权新 Source。该合同声明原知识行 hash 基线、精确 Tenant/User 和完整历史校验代码清单：

- 所有原行必须继续存在且 hash 不变；修改或删除基线行 FAIL CLOSED。
- 新本地 RAG 数据只能属于批准的 `internal-test-staging-v1` 及真实同 Tenant 的批准 User。
- 新文件必须有批准上传者；chunk 的 file/base/owner 关系必须匹配；重复身份、跨租户、跨用户或孤立关系拒绝。
- 本次不授权 secondary-profile1536 embedding 数据扩展；该表保持基线。
- 未变的历史 Guard 只校验精确历史数据视图和所有非知识数据。新知识行由新独立合同完整校验。历史 Source 参数仅标识历史数据语法，不是新 Source 审批。该视图是内存副本，没有数据库删除或 synthetic cleanup。

历史审批、witness、加密 Secret 记录以及其它业务数据仍须通过原校验。当前 PRIMARY business state 不在本轮写入范围；新 Root Authority 未签发。

## 恢复接口边界

`recovery-plan` 是只读决策接口，不是部署或恢复执行器。正式受保护 Operator 仍须承担 Global Lock、quiesce、profiles、pointer、服务启动、健康、证据、失败恢复和混合版本拒绝。旧 2c91 Operator 不因本候选自动获得使用资格，DCA synthetic cleanup 不得用于此 RAG 数据。

Root 只读 release-state 必须绑定 pair hash、新 Source/Tree、d8a predecessor、知识基线 hash、phase 和布尔 `business_writes_admitted`。正式 Operator 必须在启动任何角色前**单向封存写入准入**，之后不能将它改回 false。未封存准入时，新启动 Guard 拒绝服务。

| 状态 | 只读决策 |
| --- | --- |
| 尚未准入业务写入，且数据精确等于基线 | `EXACT_D8A_RESTORE_REQUIRES_NATIVE_GATES`；仍需旧前驱完整 Native 准入和受保护 Operator |
| 已准入业务写入，或发现新增知识数据 | `FORWARD_SUCCESSOR_KEEP_DATA`；保留数据、使用精确兼容后继 |
| 身份、phase、基线、审批或数据图不明 | FAIL CLOSED，不猜测恢复、不清理数据 |

即使当前数据 hash 回到原值，只要已准入写入，仍走前向恢复。接口不执行 down、SQL mutation、code-only switch 或删除知识行。若计划选择 d8a，明确标记 `KNOWN_UNAVAILABLE_NOT_HEALTHY`，不会将旧 RAG 错误包装成健康。Root Operator 的单向封存与实际恢复链尚需正式审查及 Native 验证。

## Linux 定向验证

证据根：`/opt/enterprise-agent-workbench-test/release-evidence/rag-native-application-successor-v1-20261011-r1`。Python3.11.16，使用既有独立 qualification runtime。临时组件测试作业在 `enterprise-agent-test.slice` 中，以 lucky 执行，MemoryMax1G、CPUQuota100%、RuntimeMax180s；没有替换 PRIMARY API/MCP/Worker 或网站服务。

测试仅使用独立临时 SQLite、模拟 Authority/进程和受保护文件元数据。它们不是 Root 审批、真实 PG0.8.6 验收、Native 服务切换或恢复演练。

最终 `code-seal.xml`：**149 passed / 0 failed / 0 errors / 0 skipped**，pytest5.98s。

- 新合同组件69 PASS：Source/Tree、旧审批拒绝、Parent、受保护文件/Gitdir/commondir、Loader、Tenant/User、数据基线、恢复状态、准入标记、三服务一致性、无隐式 DDL/cleanup。
- 既有回归80 PASS：knowledge ingestion、RAG eval、runtime profile、task finalization、storage；包括隔离 TXT ingestion/retrieval，但不是 PG Native 证据。
- 正式 Full pytest、Stage2、Provider E2E：NOT_EXECUTED。
- PG0.8.6 实际上传/检索、正式服务启动/切换、中断及 Redis 恢复：NOT_VERIFIED。
- 01 的99项与06的 pgvector0.8.6 schema/query证据作为原始历史证据保留，不算新 Source 的 Native PASS。

最终测试源码传输包 `code-seal.tar` SHA-256：`61c32a7ac3625978e562fba0d30f8ca5967198f1336ad94396d09be30d985afd`。基础8ff传输包 SHA-256：`95add88323254ecacc0d89948c9a0e9a6fc8b07b6bedd244ddd6d291e8ce54d8`。两者均经远端核对，**不是正式 Release Candidate Archive**。

中间 r2 曾有143 PASS / 1 FAIL：新 namespace 负向用例未隔离已缓存 Tooling，被更早的 `RAG_FOREIGN_IMPORTED_TOOLING` 正确拒绝。修正的是新增测试的 module-cache fixture，并补独立缓存拒绝用例；没有放宽 Guard 或断言。`final-components.xml` 失败证据保留；r3及最终封存测试均 PASS，不隐瞒中间结果。

## Native 与实际安装剩余门禁

代码候选状态为 `CODE_READY_PENDING_NATIVE`，**不是 INSTALL_READY 或 RELEASE_READY**。

当前 Native 第一阻断点：`RAG_ISOLATED_NATIVE_ENVIRONMENT_AND_AUTHORITY_ABSENT`。fresh 查得 `/opt/enterprise-agent-rag-native-isolated`、`/etc/enterprise-agent-test-rag-successor-v1`、`/etc/enterprise-agent-rag-native-isolated-v1` 均 ABSENT。独立隔离数据合同没有签发，代码对该隔离路径默认拒绝，不重放 PRIMARY witness/Secret 到虚构 clone。

安装前还必须单独完成：

1. 为新最终 Application/Tooling Source/Tree、完整文件清单及安装目录签发受保护独立 Root Authority；本轮没有签发。
2. 审批历史数据复用边界、知识 append-only 基线、精确 Tenant/User、零外部预算及新 Guard。普通聊天授权不代替 Root Approval。
3. 通过已有正式 source transport 安装 Root-owned readonly Git metadata 图；不能指向当前 lucky-owned Gitdir 或修补历史信任。
4. 对正式 Operator 精确绑定新 pair/Guard/recovery-plan、单向写入准入记录、Global Lock、三角色原子一致性、Redis quiesce 与失败边界；不得调用 DCA cleanup。这里只交付 Application ABI 和只读 Tooling接口，没有签发或激活该 Operator。
5. 在另行批准的独立 PG16.6/pgvector0.8.6/Redis、服务与模拟 Secret 环境中验证 TXT/128维检索、租户隔离、Native启动、源身份/审批负向、写入后的数据保留及实际前向恢复。
6. 单独取得 Test installation/maintenance/switch 授权。该分支不能利用原77e、d8a、28e、2c91或 f8b/7b6的历史审批。

如适配正式 Operator 必须重建 Selector、放宽旧 Guard 或改变历史合同，应 STOP；本候选不批准该扩展。

## 原环境保持

fresh 2026-10-11T03:52:07Z：Test current 仍为 `/opt/enterprise-agent-workbench-test/releases/20261008-d8a7814-wechat-personal-v1`，Git Source/Tree仍为上列 d8a/6021 身份。API/MCP/Worker均active，PID分别853170/853150/853198，实际及配置 CWD保持该 d8a 的 `enterprise_agent_poc`。这不是新 Source 的健康验收。

历史 gitdir/common-dir仍为 `/opt/enterprise-agent-workbench-test/shared/source-identities/20261008-d8a7814-wechat-personal-v1`，lucky:lucky / 0555。lucky可读Git身份不代表它满足新接口的Root信任要求；此问题保留。没有使用safe.directory通配、修改owner/mode或覆盖现行Guard。

Production操作0；PRIMARY业务数据库、Redis、current及三服务配置/进程变更0。只新增独立代码测试证据和临时组件作业。真实Provider/WeChat/Image调用0。无真实Migration、DDL、Publish/Enable、Root审批签发或Secret操作。原用户工作树和历史候选/报告保留。
