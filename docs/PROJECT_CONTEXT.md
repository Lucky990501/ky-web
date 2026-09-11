# 项目长期上下文

> 供后续 Codex 接管本仓库时优先阅读。最后整理：2026-09-10。
>
> 本文以当前代码、Git 历史和仓库内验收报告为依据。报告中的线上验证属于历史记录；若与当前代码冲突，以当前代码为准。密钥、真实租户数据和运行时数据库均不写入本文。

## 1. 项目目标

本仓库同时保留两条产品线：

1. **锟元 AI 官网与旧工作台**：面向官网访客、留资、账号、旧版安全文本工作台和后台内容管理。
2. **企业 AI Agent 工作台 POC**（`enterprise_agent_poc/`）：验证并产品化多租户企业 Agent Runtime。目标是在同一套 Skill 下，让每个企业仅能访问自己的品牌配置、知识库和素材，并由 Codex Runtime + Platform MCP 完成文案、活动策划和图片生成。

当前 Git 最近开发的主线是第二条。它的 README 明确说明：该 POC 独立于旧 DSH 工作台，尚未替换旧线上运行器。

## 2. 系统架构

```text
浏览器工作台（enterprise_agent_poc/app/static，原生 HTML/CSS/JS）
    │ HttpOnly Cookie
    ▼
FastAPI API（main.py）
    ├─ 产品数据与访问控制（ProductStore / POCStore）
    ├─ 异步任务（本地 asyncio 或 Redis 队列 + Worker）
    ├─ PostgreSQL（生产）/ SQLite（本地）
    └─ OSS（生产）/ 本地对象存储（本地）
    │
    ▼
AgentService → RuntimeProfile → CodexRuntimeProvider
    │ 每 Tenant + Agent + 模型 + Skill + Sandbox 独立 Runtime Profile
    │ 短期 HMAC Runtime MCP Token
    ▼
Platform MCP（streamable HTTP）
    ├─ enterprise_config_get
    ├─ knowledge_search
    ├─ asset_search
    └─ image_generation → 图片网关 → 私有对象存储
```

旧系统的架构独立：根目录官网静态页面经 Nginx 转发到 `admin_backend/server.py`；该服务处理账号、留资、旧工作台 SSO、额度和图片网关 Key。旧工作台通过 `runtime/kunyuan-agent-run` 调用工具全禁用的 DSH Profile。

## 3. 技术栈

| 范围 | 技术 |
| --- | --- |
| 新工作台 API | Python 3.11、FastAPI、Uvicorn、Pydantic |
| Agent Runtime | `openai-codex==0.147.0`、DeepSeek Responses API、Runtime Profile |
| MCP | `mcp` / FastMCP、Streamable HTTP、HMAC Bearer Token |
| 知识库 | PDF/DOCX/TXT/MD 解析、OpenAI-compatible Embedding、pgvector、canonical metadata、Lightweight Query Intent、Hybrid + Metadata Boost；本地可用 deterministic local-hash adapter |
| 数据 | PostgreSQL + pgvector（生产）、SQLite（开发） |
| 异步任务 | Redis + 独立 Worker（生产），`asyncio.create_task`（本地） |
| 对象存储 | 阿里云 OSS（生产）、本地文件系统（开发） |
| 前端 | 原生 HTML、CSS、JavaScript、内置 Lucide；无 Node/React/Next.js |
| 旧后台 | Python 标准库 HTTP Server、PostgreSQL/SQLite、scrypt |
| 部署 | 新 POC 提供 Docker Compose；历史生产报告记录 systemd + Nginx + PostgreSQL + Redis 试运行 |

没有根级 `package.json`、根级 `README.md`、`CLAUDE.md` 或 Node 前端工程。新 POC 的依赖定义在 `enterprise_agent_poc/pyproject.toml`。

## 4. 目录结构

```text
.
├─ index.html / script.js / styles.css         # 官网静态前端
├─ workspace.html / workspace.js               # 旧工作台静态前端
├─ admin_backend/                              # 旧官网、账号、后台 API
├─ runtime/                                    # 旧 DSH 安全运行器与禁用工具补丁
├─ ai-platform-staging/                        # 旧工作台控制面设计/静态副本
├─ deployment/                                 # 静态官网发布框架
├─ design-system/                              # 官网设计规范
├─ enterprise_agent_poc/                       # 当前主要开发对象
│  ├─ app/
│  │  ├─ main.py                               # FastAPI 入口与 HTTP API
│  │  ├─ service.py / product_service.py       # Agent 与任务编排
│  │  ├─ product_store.py / store.py           # 产品表与 DB 适配层
│  │  ├─ knowledge.py                          # 摄取、Embedding、检索、置信度
│  │  ├─ agent_catalog.py / skills.py          # Agent Catalog 与 Skill 部署
│  │  ├─ runtime/                              # Codex Runtime 抽象与实现
│  │  ├─ platform_mcp/                         # MCP Server 与业务工具
│  │  ├─ task_queue.py / worker.py             # Redis 异步执行
│  │  ├─ storage.py / auth.py / security.py    # 存储、用户会话、MCP Token
│  │  └─ static/                               # 新工作台原生前端
│  ├─ skill_packages/                          # 版本化 Skills
│  ├─ migrations/postgres/                     # 生产数据库 SQL
│  ├─ tests/                                   # pytest 回归
│  ├─ evals/                                   # RAG 固定评测数据、审核 Alias 与生产结果
│  ├─ scripts/                                 # 验收和评测脚本
│  ├─ deploy/ / nginx/                         # POC 部署说明与代理配置
│  └─ *_REPORT.md                              # 历史验收与状态报告
└─ docs/PROJECT_CONTEXT.md                     # 本文
```

`.runtime-data/` 为未跟踪的本地运行数据（含 SQLite 数据库），不是可提交源码。`.env`、`.env.*`、`.deploy-secrets.local` 和 `.secrets-backups/` 是敏感本地配置，不应读取、提交或在报告中输出。

## 5. 核心模块

### HTTP 与产品层

- `app/main.py`：登录、工作台、Agent 任务、SSE、会话、生成记录、企业配置、知识库、素材、存储读取、运行时诊断和 POC API。
- `app/product_store.py`：用户、积分、任务、会话、消息、生成记录、知识文件、素材和 Agent Template；所有用户可见查询均带 Tenant/User 边界。
- `app/product_service.py`：将异步 Task 状态推进为加载上下文、启动 Runtime、完成或失败；成功才扣积分。

### 运行时与会话

- `app/service.py`：由 Tenant + Agent 构建 Runtime Profile；新会话创建 Codex Thread，旧会话按已保存的 Thread ID resume。
- `app/runtime/codex_provider.py`：准备独立的 `CODEX_HOME`、工作区、模型配置、MCP 配置和短期 Token；提取工具观察和最终回复，不保留隐藏推理。
- Conversation 只能被其所属 Tenant、用户、Agent 和相同 Runtime Profile 续用，避免跨 Agent 或跨配置复用 Thread。

### 数据、任务和存储

- `app/store.py`：SQLite/PostgreSQL DB-API 兼容层、基础租户和 Run Trace。
- `app/task_queue.py`、`app/worker.py`：Redis 队列的任务/知识文件消费；本地开发可切换为进程内任务。
- `app/storage.py`：本地文件系统与 OSS 适配器；生成图下载需要 Tenant + 用户归属校验。

### 旧系统

- `admin_backend/server.py`：官网 API、注册/登录、会话、留资、管理员内容管理、旧工作台 SSO、旧 Agent 会话、图片网关用户 Key 和积分。
- `runtime/harness-safe.patch.yml`：旧 DSH Runtime 禁用 Shell、文件、网络、Skill、子 Agent 等能力。

## 6. Agent / Skill / MCP 架构

### Agent Catalog

`app/agent_catalog.py` 当前定义三个 Agent：

| Agent | Skills | 积分 | MCP 权限 |
| --- | --- | ---: | --- |
| `image-agent` | `poster-design@1.0.0` | 20 | 企业配置、知识、素材、图片生成 |
| `copywriting-agent` | `marketing-copywriting@1.0.0`、`social-copywriting@1.0.0` | 3 | 企业配置、知识、素材 |
| `campaign-agent` | `campaign-planning@1.0.0`、`event-copywriting@1.0.0` | 8 | 企业配置、知识、素材 |

### 隔离原则

1. Runtime Profile 的哈希包含 `tenant_id`、`agent_id`、模型、推理强度、Skill Manifest、Sandbox 和 Runtime 版本。
2. 每个 Runtime Profile 部署独立 Skill 目录；不在 manifest 中的 Skill 会被删除。
3. Runtime Token 只包含服务器签发的 Tenant、Agent、Profile、Scope 和过期时间；模型不能传入或篡改 `tenant_id`。
4. Platform MCP 从 Token 得到 Tenant 后再查询数据；工具入参本身没有 `tenant_id`。
5. 图片生成权限只授予 `image-agent`。

### MCP 工具

- `enterprise_config_get`：读取当前企业配置。
- `knowledge_search`：读取当前企业检索结果。
- `asset_search`：读取当前企业素材元数据。
- `image_generation`：调用图片网关、将生成结果持久化到平台对象存储。

Agent 指令要求首轮按企业配置 → 知识 → 素材的顺序调用工具；图片 Agent 在明确成图请求时再调用图片生成。企业规则优先于用户要求，资料不足时不得编造事实。

## 7. 已完成功能

代码可确认完成：

- Cookie 登录、管理员/成员权限、个人资料与头像。
- 多 Tenant、Tenant Agent Instance、Agent 启停校验、积分扣费。
- 图片、文案、活动策划 Agent 的 Catalog 与独立 Conversation/Thread。
- 任务创建、持久化阶段事件、SSE、恢复队列、会话历史、生成图库和素材保存。
- 企业配置和 URL 型素材管理。
- PDF/DOCX/TXT/MD 上传、解析、切块、Embedding、索引、重试、检索测试。
- 生产语义检索诊断：PostgreSQL、pgvector、Embedding 配置/探针和 fallback 状态。
- 本地/OSS 对象存储；生成图受 Tenant + 用户访问控制。
- Runtime Trace 记录模型、Skill、工具摘要、状态、延迟、最终结果与生命周期事件，刻意排除隐藏推理。
- 旧官网的留资、帐号、旧工作台和后台管理能力。

仓库报告记录过真实生产闭环和三种 Agent Grounding 验收。后续若需要以这些结论发布，应重新确认对应环境与当前分支一致。

## 8. 当前开发状态

### Git 与 Release 快照（2026-09-11）

- 当前分支：`master`；本地与 `origin/master` 已同步至验收文档 commit `6da5e65be86d52c8c0ba3c7042629ecc9af69cbc`。
- 当前生产代码 Release：`20260911-59d67ac`，代码 commit `59d67ac891a5aca57f1d2f41d9d0cf6cd0335bab`，Runtime `openai-codex==0.147.0`。
- PostgreSQL migrations `001` 至 `004` 已记录应用，pending = 0；API、Platform MCP、Worker 与 `/api/health` 正常。
- 工作树仍有 Phase A 以前已存在的无关修改和未跟踪文件；不得丢弃、覆盖或重置。

### Enterprise Knowledge V1.4 生产状态

- 生产租户：`zhiy-e-intelligence`；1 个知识文件、287 个 Chunk。
- 索引已全量重建为 `rag-index-v2`，Embedding 为 `text-embedding-3-small` / 1536，metadata schema 为 `knowledge-metadata-v1`。
- 287/287 Chunk 具有 canonical_section、record/entity type、来源、section、sheet、index/schema version；year、person_name、event_name 按适用记录允许为空。
- 数据库回滚快照：`rag_index_backup_v1_4_2bee63d_20260910`；代码备份：`/opt/enterprise-agent-workbench/backups/rag-v1-4-2bee63d-20260910`。
- 固定 40 条最终生产复评：answerable recall 1.00、section recall 0.8667、Top-1 0.6667、grounded precision 0.8667、no-answer rejection 1.00、case pass 0.90。
- 剩余失败：r09、r16、r19、p08；均为 physical section / alias 口径失败，但 Top-1 canonical_section 正确。正式指标仍按原评测口径判失败。
- API、MCP、Worker 均 active；`/api/health` 为 `status: ok`、`knowledge: ok`。
- Phase A 最终生产门禁（2026-09-11）为 `PASS WITH ISSUES`：Tenant A/B Isolation、Browser Knowledge E2E、三个 Agent Grounding、No-answer Regression、Thread Resume 和 Cleanup 全部 PASS。详见 `enterprise_agent_poc/WORKBENCH_CORE_V1_PHASE_A_FINAL_REPORT.md`。
- 稳定状态已经写入：Enterprise Knowledge `STABLE`、RAG Retrieval V1.4 `FROZEN`、Workbench Core V1 `STABLE`；允许下一阶段进入 Skill Registry V1，但不得把它与新的 RAG 调优混在同一变更中。

## 9. 待办事项

### RAG 验收与安全

1. 保持 RAG Retrieval V1.4 FROZEN；不要在 Skill Registry 阶段顺手调整检索算法、Alias 或评测口径。
2. 修复 Run Trace 对当前 MCP content wrapper 的结构化解析，使 `knowledge_retrievals` 正确保存 result count、Chunk/File ID、评分及接受/拒绝原因；不得保存正文或凭据。
3. Alias 变更仍必须由知识内容负责人独立审核，不能从评测输出自动生成。
4. 保留 Reindex 数据库快照，待人工确认稳定期与删除策略；不要自动删除。
5. 若未来重新创建验收 Tenant，必须继续使用受控 Provision/Cleanup 工具，不得手工插入或借用普通 Tenant。

### 产品能力

1. 成员列表、邀请、角色编辑、禁用/启用。
2. 密码修改、会话失效、安全审计。
3. 积分流水、充值、订单、套餐、发票与使用统计。
4. 素材二进制上传、编辑、预览和服务端筛选。
5. 支持 XLSX 或提供受控的表格转换摄取流程。
6. 生产 Tenant 的创建、配置和 Agent 启停管理入口。

### 工程与部署

1. 建立包含 `openai-codex==0.147.0` 的内部 wheelhouse，减少生产发布对复用 venv 的依赖。
2. 在低风险窗口补做“已有 previous release 时”的故障注入 rollback 演练。
3. 为 Docker Compose 明确 pgvector 镜像/安装方案。
4. 增加 `.env.production.example`，并保持不含真实密钥。
5. 接入可审计的模型使用量和价格映射，避免猜测成本。

## 10. 重要设计决策

- **Tenant 从服务端 Token 识别，不信任模型入参。** 这是 MCP 隔离的关键安全边界。
- **Runtime Profile 是隔离单元。** Profile 改变（Tenant、Skill、模型、Sandbox 等）即产生新 Runtime 标识，旧 Thread 不可跨 Profile 复用。
- **只记录可观察事件，不记录隐藏推理。** Trace 记录工具摘要、状态、延迟和结果；不要增加 Chain-of-Thought 持久化。
- **生产禁用语义检索 fallback。** 生产且 `KNOWLEDGE_ALLOW_FALLBACK=false` 时，缺 pgvector、正式 Embedding 或连通性会使健康状态降级并拒绝语义处理/检索。
- **Canonical metadata 与 Eval Alias 分离。** canonical_section 只能由知识源结构生成；Alias 仅用于经人工审核的评测等价判断，不能反向写入生产分类逻辑。
- **Metadata 只做 Boost，不做 Hard Filter。** Query Intent 可能误判，首版通过有限加权改善排序并保持 Recall。
- **私有生成文件经应用鉴权读取。** 不将 Provider 临时 URL 当作最终用户资产。
- **任务成功后才扣积分。** 失败任务不扣费；已有去重检查防止同一 Task 重复扣款。
- **旧工作台保持工具全禁用。** 不应把旧 DSH Runtime 与新 Platform MCP/Codex Runtime 混用。
- **源码、运行数据与部署密钥分离。** `.runtime-data`、`.env` 和部署秘密不可提交。

## 11. 已知问题

- V1.4 仍有 r09、r16、r19、p08 四个 physical section / alias 失败；其 canonical Top-K 正确，不应把它们简单解释为类别召回失败。
- 旧 Markdown 转换仍产生 124 个“记录 N”弱标题；V1.4 metadata 已恢复类别和年份，但展示 section 仍弱。
- p08 仍受“源文件差异 / 数据冲突 / 切片策略”等维护类 section 排序影响。
- Run Trace 的结构化 `knowledge_retrievals` 暂时无法解析当前 MCP content wrapper，会出现 `result_count=null` / 空 results；原始正文不应为解决该问题而扩大持久化范围。
- Codex 内置浏览器控制桥曾持续返回 `nodeRepl.fetch request failed`；Phase A 最终 Browser Gate 已使用人工浏览器截图和生产 Trace 完成验收。
- 生产发布仍复用受控 venv，因为镜像源没有 `openai-codex==0.147.0`；发布脚本会验证依赖和 `pip check`。
- 有 previous release 时的深度故障注入 rollback 尚未在真实生产数据环境刻意演练；基础切换与自动恢复已验证。
- `scripts/verify_runtime_config.py` 和管理员诊断的 `runtime_config` 仅返回 SHA-256 指纹和配置状态，可用于阻断 `.env.production` 与服务进程漂移，不能代替受控发布流程。
- `knowledge_search` 已在检索实际成功后才记录 `completed`，失败时记录 `failed`。
- `KnowledgeRetrievalService` 在无 Chunk 结果时会回退到旧 `knowledge_documents` 文本检索路径；它仍带 Tenant 条件，但需要明确其是否应参与严格生产 RAG。
- Run Trace 会保存最终回复和最多约 600 字符的工具摘要；需明确企业内容的保留期、访问控制和脱敏策略。
- `workbench.js` 留有已不走路由的旧 `knowledge()` / `textKnowledge()` 实现；实际页面使用 `knowledgeV2()`。
- `BACKEND_API_GAPS.md` 部分内容落后于当前代码，例如它仍称知识文件上传未实现。
- Docker Compose 使用普通 `postgres:16-alpine`，仓库未提供 pgvector 安装步骤；严格生产 RAG 是否可直接启动必须实测。
- `.env.example` 的默认本地环境仍是 SQLite + local-hash；生产配置依赖服务器环境变量，不能将本地默认视为生产基线。

## 12. 下一阶段开发计划

建议按以下顺序推进：

1. **进入 Skill Registry V1**：先冻结范围、数据模型、版本/启停/回滚规则和验收门禁；保持 RAG Retrieval V1.4 FROZEN。
2. **修复 Trace 可观测性解析**：只持久化脱敏检索证据，补充测试并确认不记录正文、Token 或隐藏推理。
3. **补强发布工程**：建立内部 wheelhouse，并安排 previous-release 故障注入 rollback 演练。
4. **补齐产品管理面**：成员、Tenant/Agent 管理、素材上传、账户安全和计费。
5. **继续执行阶段验收纪律**：每完成一个独立阶段即更新 Markdown 验收文档；生产结论必须以当前 Release 与真实证据为准。

## 接管规则

- 每次完成一个可独立验收的阶段，必须新增或更新对应 Markdown 验收文档，至少记录范围、版本/环境、验证证据、结论、未完成项和下一步；不可将 BLOCKED 或本地结果写成生产通过。

后续 Codex 开始工作时，应先阅读本文、`enterprise_agent_poc/README.md`、当前 `git status`、最近 Git 提交和与任务相关的验收报告。任何线上状态应重新验证；不要仅根据历史报告宣称当前环境仍然可用。
