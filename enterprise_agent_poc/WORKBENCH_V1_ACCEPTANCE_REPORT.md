# Workbench V1 Gate 3 产品级验收报告

日期：2026-09-08  
结论：**BLOCKED**

本报告以实际运行证据为准。未因代码、单元测试或 Gate 2 结果而替代产品端到端验收。

## 1. 系统启动结果

| 组件 | 结果 | 证据 / 说明 |
| --- | --- | --- |
| Frontend + FastAPI | 通过（本地） | `http://127.0.0.1:8090/login` 可打开并完成登录 |
| Platform MCP | 通过（本地） | `/mcp` 返回 HTTP 406，符合未建立 MCP session 的服务可达状态 |
| Codex Runtime | 阻塞 | 两次浏览器任务均在 DeepSeek Responses 流阶段失败 |
| Image Gateway | 未完成端到端 | 因 Runtime 未完成 MCP image_generation，未能验证 |
| Object Storage | 部分通过 | 已有 LocalStorage 适配器和受权读取接口；未取得真实图片验证 |
| PostgreSQL | 未启动 | `psql` 不可用，Docker daemon 未运行 |
| Redis / Task Worker | 未实现/未启动 | 当前使用进程内 `asyncio.create_task`，没有 Redis 队列或独立 Worker |
| Knowledge Service | 部分通过 | SQLite 文本知识记录可用；文件上传、解析、索引服务未完成 |

本地启动方式（不得提交真实密钥）：

```powershell
cd enterprise_agent_poc
$env:DEEPSEEK_API_KEY = "<secret>"
$env:GATEWAY_API_TOKEN = "<secret>"
python -m app.platform_mcp.server
python -m uvicorn app.main:app --port 8090
```

主要安全配置名：`ENTERPRISE_POC_TOKEN_SECRET`、`ENTERPRISE_POC_MODEL_PROVIDER_ID=deepseek`、`ENTERPRISE_POC_MODEL_ID=deepseek-v4-pro`、`ENTERPRISE_POC_MCP_URL`、`ENTERPRISE_POC_OBJECT_STORAGE_DIR`。真实值未写入报告。

## 2. 前端页面与 API

已可打开：`/login`、`/workspace`、`/agents/image`、`/conversations`、`/generations`、`/enterprise-config`、`/knowledge`、`/assets`。

已验证 API 族：认证、工作台、`POST /api/v1/agents/{agent_id}/runs`、Task 查询、会话、生成记录、企业配置、知识文本、素材、对象存储读取。管理员接口由服务端 Session 的 `role` 校验；成员访问企业配置 API 返回 403（自动化验证）。

## 3. 浏览器端验收

浏览器实际步骤：

1. 以开发验收账号登录 Tenant A；工作台显示“启明教育”、图片 Agent 和 200 积分；
2. 打开图片 Agent，输入 `帮我做一张秋季招生海报`；
3. 页面立即显示 `generating`，HTTP 请求未阻塞；
4. Task 最终进入 `failed`，页面可展示失败状态。

两次真实浏览器 Run 对应的 Run Trace 错误均为：`stream disconnected before completion: error sending request for url (https://api.deepseek.com/responses)`。因此未能验收图片结果、多轮继续、生成记录、保存素材、成功扣积分。

## 4. Agent、Task、积分与图片资产

| 项目 | 结果 |
| --- | --- |
| Agent 闭环 | 阻塞于 DeepSeek；未获得本次 Workbench 成功 Run 证据 |
| Task 异步 | 部分通过：观察到 `queued → running/loading_context → generating → failed`，非阻塞 HTTP |
| Task 恢复 | 代码存在重新排队逻辑；未在 Redis/Worker 架构下验证 |
| 积分成功扣减 | 未验证；失败任务未产生扣费流水，符合失败不扣费策略 |
| 余额不足 | API 有 402 路径，未做浏览器实测 |
| 图片持久化 | 代码设计为 Provider URL 下载至平台存储后生成 generation；因本次没有成功图片而未实测 |
| 下载/删除/保存素材 | 已有 UI/API，未有成功 generation 可完成产品端实测 |

## 5. Tenant 与企业数据

Tenant A/B 仍是 Gate 2 合成数据。要求的 `tenant_real_test`、正式资料上传与真实企业管理员账号创建没有产品化入口，未建立。

因此以下 Gate 3 条目未通过：真实验收 Tenant、Logo/资料文件上传、两个真实 Tenant 浏览器隔离、真实素材上传、完整知识文件解析和索引。

现有隔离证据：Session 绑定 tenant；成员不能访问管理员 API；Gate 2 已覆盖 MCP Token scope、跨 Tenant、Prompt Injection 和未授权 Skill。但这不能替代 Gate 3 的两个真实 Tenant 浏览器验收。

## 6. 企业配置、知识与素材

- 企业配置页面已在浏览器打开，展示品牌名、主/辅色和 Slogan；API 可保存，后续 Runtime 会经 `enterprise_config_get` 读取。
- 知识库目前只支持输入文本创建 `ready` 记录、列表及删除；没有文件上传、`uploaded/parsing/indexing` 阶段和重试处理。
- 素材库支持 URL 型添加、分类、描述、删除；不支持浏览器上传二进制文件。

## 7. 数据库迁移、Runtime 与安全

- 已提供 PostgreSQL SQL 基线，但当前运行的是 SQLite，正式迁移未执行。
- Gate 2 Run Trace、MCP scope 和 Conversation ↔ Thread 逻辑被复用，未重写。
- `python -m pytest -q`：**8 passed**（另有第三方 Starlette 弃用警告）。
- Gate 2 安全回归的历史报告存在，但本次 Workbench 因 Runtime 不可用，无法重新执行真实 Thread Resume、规则冲突、知识不足与 Prompt Injection 全链路。

## 8. 性能与已知问题

| 指标 | 本次值 |
| --- | --- |
| FastAPI health | 本地成功 |
| 浏览器登录/页面渲染 | 本地成功、即时 |
| 图片 Task HTTP | 异步返回、页面进入 generating |
| 图片 Task 完成耗时 | 无成功样本；两次均失败 |

当前 Bug / 阻塞：

1. Workbench Runtime 到 DeepSeek 的真实 Responses 流连接失败；
2. 无 PostgreSQL、Redis 和独立 Worker，不能满足产品级持久任务要求；
3. 没有 Tenant 创建/成员管理/真实验收 Tenant 工作流；
4. 知识与素材只有文本/URL 入口，没有文件上传、处理与失败重试；
5. 本次未成功生成图片，故对象存储、generation、下载和保存素材缺少真实端到端证据。

技术债：进程内 task runner、SQLite 开发适配、手写 Session、图片 metadata 从 Trace 摘要提取、缺少 SSE/WebSocket 状态推送和生产可观测性。

## 9. 试运行判定

**不达到真实客户试运行标准。**

解除 BLOCKED 的最小条件：在生产式网络环境中稳定完成 DeepSeek + MCP + Image Gateway + Object Storage 的浏览器端成功任务；执行 PostgreSQL 与 Redis/Worker；提供 `tenant_real_test` 的管理员创建和文件上传路径；补齐两租户浏览器隔离、积分成功/不足、图片资产与多轮恢复的真实验收证据。
