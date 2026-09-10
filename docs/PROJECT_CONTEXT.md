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
| 知识库 | PDF/DOCX/TXT/MD 解析、OpenAI-compatible Embedding、pgvector；本地可用 deterministic local-hash adapter |
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
│  ├─ evals/                                   # RAG 固定评测数据（当前未跟踪）
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

### Git 快照（2026-09-10）

- 当前分支：`master`，与 `origin/master` 同步。
- 最新已提交版本：`a2a6609 fix: degrade health when embedding probe fails`。
- 暂存区为空。
- 存在未提交的 RAG/Trace 改动和未跟踪的评测材料；在未得到明确指令前不得丢弃、覆盖或重置它们。

### 当前未提交主线

未提交改动集中于 Enterprise Knowledge V1.2/V1.3：

- `RetrievalConfidencePolicy`：用总分、向量分、关键词匹配和 margin 拒绝弱相关结果。
- FastMCP 的同步检索被移到线程，避免阻塞 Streamable HTTP 事件循环。
- SDK MCP 状态归一化为稳定词汇。
- Turn Trace 增加 `turn_started`、工具开始/完成、模型恢复、最终回复收到、Turn 完成等生命周期事件。
- 没有最终正文的 Turn 会被标记失败，同时保留可关联的 `run_id` 和 `conversation_id`。
- 未跟踪的 `evals/rag_v1_3_dataset.json` 含 40 条固定 RAG 用例；`scripts/run_rag_v1_3_eval.py` 是针对一个 Tenant 的只读评测脚本。
- 2026-09-10 的真实生产 40 条评测已完成，但章节 Grounding 和拒答指标未达标；详见 `enterprise_agent_poc/ENTERPRISE_KNOWLEDGE_PRODUCTION_FINAL_REPORT.md`。其后已在本地增加 Query 拒答策略、人工审核小节别名入口和无明文配置值的漂移诊断；尚未部署和重新生产复验。Phase A 仍为 BLOCKED，不得进入 Skill Registry 或 Agent Expansion。

## 9. 待办事项

### RAG 验收与安全

1. 由知识内容负责人审核 `evals/rag_v1_3_section_aliases.template.json`，形成受控的实际小节别名映射；不得从评测输出自动生成。
2. 将 Query 拒答策略和配置漂移诊断部署到受控发布目录；先通过配置指纹对比，再重新运行 40 条评测并归档。
3. 创建并清理临时 Tenant B，完成上传、检索、MCP Token 和 Agent Grounding 的隔离验收。
4. 将脱敏后的 query、Chunk/File ID、评分、接受/拒绝原因和检索耗时持久化到 Run Trace。
5. 为 Embedding 重建、模型/维度版本变化建立明确 reindex 流程。

### 产品能力

1. 成员列表、邀请、角色编辑、禁用/启用。
2. 密码修改、会话失效、安全审计。
3. 积分流水、充值、订单、套餐、发票与使用统计。
4. 素材二进制上传、编辑、预览和服务端筛选。
5. 支持 XLSX 或提供受控的表格转换摄取流程。
6. 生产 Tenant 的创建、配置和 Agent 启停管理入口。

### 工程与部署

1. 增加统一 migration runner，实际应用 PostgreSQL `001` 至 `004`。
2. 为 Docker Compose 明确 pgvector 镜像/安装方案。
3. 增加 `.env.production.example`，并保持不含真实密钥。
4. 统一 Docker Compose 与当前 systemd 生产试运行的发布标准。
5. 接入可审计的模型使用量和价格映射，避免猜测成本。

## 10. 重要设计决策

- **Tenant 从服务端 Token 识别，不信任模型入参。** 这是 MCP 隔离的关键安全边界。
- **Runtime Profile 是隔离单元。** Profile 改变（Tenant、Skill、模型、Sandbox 等）即产生新 Runtime 标识，旧 Thread 不可跨 Profile 复用。
- **只记录可观察事件，不记录隐藏推理。** Trace 记录工具摘要、状态、延迟和结果；不要增加 Chain-of-Thought 持久化。
- **生产禁用语义检索 fallback。** 生产且 `KNOWLEDGE_ALLOW_FALLBACK=false` 时，缺 pgvector、正式 Embedding 或连通性会使健康状态降级并拒绝语义处理/检索。
- **私有生成文件经应用鉴权读取。** 不将 Provider 临时 URL 当作最终用户资产。
- **任务成功后才扣积分。** 失败任务不扣费；已有去重检查防止同一 Task 重复扣款。
- **旧工作台保持工具全禁用。** 不应把旧 DSH Runtime 与新 Platform MCP/Codex Runtime 混用。
- **源码、运行数据与部署密钥分离。** `.runtime-data`、`.env` 和部署秘密不可提交。

## 11. 已知问题

- V1.3 生产评测结果已归档，但其抽象 `expected_section` 与真实索引小节名没有人工审核的映射，因此不能将当前 `section_recall_at_k=0` 直接解释为向量召回失败。
- 本地已有绝对承诺、无证据价格和显式不存在实体的 Query 拒答策略；部署前不要把本地测试结果当作生产安全验收。
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

1. **冻结并审查现有未提交 RAG 改动**：先提交前进行针对性测试与评审，不要覆盖。
2. **完成 RAG V1.3 评测可信度**：人工审核小节别名，部署拒答策略与配置指纹，然后运行 40 条集，记录章节召回、Top-1 章节准确率、grounded precision、拒答率与时延。
3. **完成双 Tenant 验收**：在授权环境中创建临时 Tenant B，验证上传、检索、MCP Scope、Agent 输出，随后清理数据。
4. **收口 Trace 与数据治理**：持久化必要的脱敏检索证据，制定内容保留策略。
5. **收口部署可复现性**：migration runner、pgvector Compose、生产环境模板、健康检查和回滚流程。
6. **补齐产品管理面**：成员、Tenant/Agent 管理、素材上传、账户安全和计费。

## 接管规则

后续 Codex 开始工作时，应先阅读本文、`enterprise_agent_poc/README.md`、当前 `git status`、最近 Git 提交和与任务相关的验收报告。任何线上状态应重新验证；不要仅根据历史报告宣称当前环境仍然可用。
