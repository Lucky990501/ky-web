# 企业 AI Agent 工作台：Runtime POC

这是《企业 AI Agent 工作台 V1.1》的第一阶段实现。它只验证一个闭环：同一份 `poster-design` Skill 在 Tenant A 和 Tenant B 下，经由 Codex Harness 与 Platform MCP 获得不同的企业配置、知识和素材，且服务端拒绝跨租户访问。

它与仓库中现有的 DSH 工作台独立，未替换任何线上运行器。

## 已实现

- `RuntimeProvider` 抽象、`CodexRuntimeProvider` 和按 Runtime Profile 复用的 `CodexRuntimeManager`；
- 以 `tenant_id + agent_id + model_provider_id + model_id + skill manifest + sandbox` 生成 Runtime Profile；
- 为每个 Tenant Agent Profile 创建独立 `CODEX_HOME`、Workspace、Skill 部署目录和 MCP bearer token；
- `poster-design` 原生 Codex Skill，以及只将允许 Skill 部署到对应 Runtime 的部署器；
- Platform MCP 的四个能力服务：企业配置、知识检索、素材检索和真实 OpenAI 图片生成；
- HMAC 签名的 Runtime MCP Token。工具从 token 确定 tenant，模型没有也不能传入 `tenant_id`；
- SQLite POC 数据库、Tenant A/B 种子数据、Conversation ↔ Codex Thread 映射和可观察执行事件；
- 每轮唯一 `run_id` 的 Run Trace：记录模型、Skill、MCP 输入/输出摘要、Token、耗时、最终结果和图片设计中间产物；不记录隐藏推理；
- 一个仅供 POC 使用的 FastAPI Agent API。其演示 API Key 被映射为服务端 principal，不把 tenant 身份交给模型。

## 快速开始

需要 Python 3.11+ 和 DeepSeek API Key。

```powershell
cd enterprise_agent_poc
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m app.seed
python -m pytest
uvicorn app.main:app --reload --port 8090
```

另开一个终端运行 Platform MCP：

```powershell
cd enterprise_agent_poc
python -m app.platform_mcp.server
```

`openai-codex==0.147.0` 已固定。生产环境应使用受管服务账号、密钥库及 PostgreSQL；示例中的 token secret 和本地 API key 均不能进入生产。

运行前在进程环境中配置一个实际可用的 DeepSeek API Key（不要写入 `.env` 或提交仓库）：

```powershell
$env:DEEPSEEK_API_KEY = "..."
```

当前 Runtime Baseline 是 `deepseek` / `https://api.deepseek.com/` / `responses` / `deepseek-v4-pro` / `high`。可通过 `GET /api/v1/poc/runtime-baseline` 查看不含秘密的配置。每次成功或失败的运行都会在 SQLite 的 `run_traces` 表留下记录。

## POC 调用

```powershell
$headers = @{ "X-POC-API-Key" = "tenant-a-local-key" }
$body = @{ agent_id = "image-agent"; message = "帮我做一张秋季招生海报" } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8090/api/v1/poc/runs -Method Post -Headers $headers -ContentType application/json -Body $body
```

将 key 换成 `tenant-b-local-key`，再用完全相同的输入，可验证两个租户获得各自的品牌与课程资料。真实 Codex 运行前，需保证 `ENTERPRISE_POC_MCP_URL` 是 Codex Runtime 可访问的地址。

## 边界

这不是完整 SaaS：尚未包含登录、后台、上传/RAG pipeline、积分、SSE 和外部客户 API。它是这些能力之前必须先通过的 Runtime 隔离验证。
