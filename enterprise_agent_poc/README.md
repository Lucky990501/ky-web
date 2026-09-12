# 企业 AI Agent 工作台

`enterprise_agent_poc/` 起源于多租户 Runtime 隔离 POC，现已演进为可在生产运行的企业 AI Agent 工作台。目录名和 `/api/v1/poc/*` 兼容接口仍保留，但当前能力已经包含登录、Agent 项目、异步任务、历史恢复、知识库、Skill Registry、生成图片持久化、PostgreSQL migration 与受控 Release。

它与仓库中的旧 DSH 工作台相互独立；当前生产 Release 运行本目录的 FastAPI API、Platform MCP 和 Worker，不表示旧工作台代码已被替换或删除。

## 当前基线（2026-09-12）

| 项目 | 当前值 |
| --- | --- |
| 本轮上下文刷新基线 commit | `2dc56932f1775575bf4c0b429f698657c240d6ad` |
| 生产 Release | `20260912-0f18a23` |
| 生产 source commit | `0f18a233111c95442dbbadee4df51841c51e019d` |
| Runtime | `openai-codex==0.147.0` |
| PostgreSQL migrations | `001`–`007` 已应用，pending = 0 |
| Embedding | `text-embedding-3-small` / 1536 |
| Active RAG index | `rag-index-v2` |
| 生产健康 | API、Platform MCP、Worker 健康；验收记录的 `/api/health` 为 `status=ok`、`knowledge=ok`、`environment=production` |

本轮上下文刷新完成后的 docs-only commit 会推进 `origin/master`，但不会改变当前生产 Release 或生产 source commit；Git 文档 HEAD 不等同于已部署的生产 source。本轮历史优化没有修改 Embedding、RAG、pgvector，也没有执行 Reindex。

## 系统结构

```text
浏览器工作台（HttpOnly Session Cookie）
    │
    ▼
FastAPI API
    ├─ ProductStore：Tenant/User 权限、项目、任务、消息、生成记录
    ├─ PostgreSQL（生产）/ SQLite（本地）
    ├─ Redis Queue + Worker（生产）/ asyncio（本地）
    ├─ Skill Registry：包、版本、发布状态、Agent 绑定
    └─ 私有 OSS（生产）/ 本地对象存储（本地）
    │
    ▼
AgentService → Runtime Profile → Codex Runtime
    │ 短期 HMAC MCP Token；Tenant 由服务端签发
    ▼
Platform MCP
    ├─ enterprise_config_get
    ├─ knowledge_search
    ├─ asset_search
    └─ image_generation
```

Runtime Profile 由 Tenant、Agent、模型、推理强度、Skill manifest、Sandbox 和 Runtime 版本共同确定。Conversation 只能在相同 Tenant、用户、Agent 和 Runtime Profile 下恢复，模型不能通过工具参数指定或篡改 `tenant_id`。

## 已实现能力

- Cookie 登录、企业管理员/成员权限、独立平台管理员权限；
- 图片生成、文案创作、活动策划三个 Agent，以及按 Tenant 启停的 Agent Instance；
- Conversation ↔ Codex Thread 映射、异步 Task、持久化阶段事件、SSE、Worker 恢复和成功后扣积分；
- 企业配置、素材、PDF/DOCX/TXT/MD 知识摄取、Embedding、pgvector 检索与生产健康诊断；
- Runtime Trace：保存可观察的模型、Skill、工具摘要、状态、耗时和最终结果，不保存隐藏推理；
- Skill Registry：原生 Skill ZIP 导入、校验、测试、发布、废弃、Agent 绑定/解除、版本升级/回滚；
- 历史项目归类、项目详情恢复、生成图片持久化和登录态鉴权渲染；
- 独立 Release 目录、migration runner、配置指纹、systemd 三服务切换和健康门禁。

## Skill Registry

平台管理员在 `/platform/skills` 管理原生 Codex Skill。Registry：

- 要求 ZIP 根目录包含 `SKILL.md`，允许 `agents/`、`references/`、`scripts/`、`assets/`；
- 校验安全路径、文件类型、包大小、UTF-8 `SKILL.md` 和 SHA-256；
- 使用 `draft`、`published`、`deprecated` 状态，禁止覆盖已发布或已废弃版本；
- 只允许 Agent 绑定已发布版本；新增绑定必须显式确认，现有绑定可以升级、回滚或解除；
- 启动时 bundled Skill 只补齐缺失绑定，不覆盖已经选择的版本；
- 从 Registry 解析 Agent manifest，只把指定版本同步到对应 Runtime Profile 的 `CODEX_HOME/skills`。

`005_skill_registry_v1.sql` 已在生产应用。现有报告确认生产发布与部分真实操作，但没有宣告 Upload → Publish → Bind → Runtime Sync → Real Turn → Upgrade/Rollback 全链路最终 PASS；引用状态时应保留这一区分。

Skill Registry 只管理 Skill 包与绑定，不承载企业知识。Enterprise Knowledge、RAG Retrieval V1.4、Tenant Isolation 与 Runtime Core 保持独立边界。

## 历史项目与图片链路

当前产品把一个 Conversation 作为一个创作项目，不新增独立 Project 表：

- 历史按 `image-agent`、`copywriting-agent`、`campaign-agent` 归类为图片生成、文案创作、活动策划项目；
- 通用旧标题按首条需求生成展示标题，并返回最近需求、任务状态、执行次数、图片数和最近活动时间；
- `/conversations`、Agent 项目页、刷新以及浏览器前进/后退恢复同一 Conversation；
- Conversation 详情合并消息、任务、Run 与 Generation；旧记录没有消息时可从任务结果恢复只读历史；
- 新图片任务优先从 Runtime MCP 结构化结果提取 allowlist `storage_key`，旧 Trace 摘要解析仅作为兼容恢复路径；
- Generation 保存 `storage_key` 与 MIME；列表、详情、任务终态和“我的生成”统一返回应用内 `content_url` / `image_url`；
- 浏览器通过 `/api/v1/storage/{storage_key}` 读取图片。API 先验证登录 Session，再校验 Tenant 与用户归属；保存为企业素材的图片允许同 Tenant 读取；
- API 从私有 OSS 或本地存储读取字节，响应使用已保存 MIME，并设置 `Cache-Control: private, no-store` 与 `X-Content-Type-Options: nosniff`。

上述历史分类、直接打开与刷新、历史及项目内图片渲染、项目页刷新保持 Conversation、浏览器后退/前进，已在当前生产 Release 的登录态 Chrome 中复验通过。脱敏证据见 `HISTORY_RECORD_OPTIMIZATION_ACCEPTANCE.md`。

## 本地开发

需要 Python 3.11+。默认示例环境使用 SQLite、本地文件存储、进程内任务和 deterministic local-hash Embedding；它不能代表生产基线。

```powershell
cd enterprise_agent_poc
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m app.seed
python -m pytest
uvicorn app.main:app --reload --port 8090
```

另开终端运行 Platform MCP：

```powershell
cd enterprise_agent_poc
python -m app.platform_mcp.server
```

真实 Codex Runtime 需要在进程环境中提供模型 Provider 凭据。不要把 API Key、Token、Cookie、数据库密码或 OSS 凭据写入 `.env`、日志、截图或 Git。

## 生产与 migration 边界

- 生产使用 PostgreSQL、Redis/Worker、私有 OSS、受管环境文件和 systemd 服务；本地 SQLite 与 Docker Compose 只能作为开发适配。
- `migrations/postgres/001`–`007` 均已应用。migration 是 append-only 历史，不修改已执行文件，也不因候选实验代码移除而回退 `006`。
- 当前生产 Release 必须以 `20260912-0f18a23` 和 source `0f18a233111c95442dbbadee4df51841c51e019d` 表述。
- 保持 `text-embedding-3-small` / 1536 与 `rag-index-v2`；未经独立授权和验收，不修改 Embedding、RAG、pgvector 或执行 Reindex。
- 生产事实优先引用当前 Git、`HISTORY_RECORD_OPTIMIZATION_ACCEPTANCE.md` 和重新执行的生产核验；较早阶段报告只代表其记录时点。
