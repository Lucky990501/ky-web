# Workbench Core V1｜Phase A P0 图片 Runtime 验收

日期：2026-09-10

## Release Identity

- Release：`20260910-50ec83d`
- Git commit：`50ec83d6ae6dc9d10da8f978cf58d13e164d782c`
- 本地回归：`42 passed`（仅有 FastAPI TestClient 弃用警告）
- 发布前迁移：`applied: []`
- 发布后：API / MCP / Worker active，`/api/health` 通过。

## 修复内容

原失败任务 `dcca89ea-f30c-4596-9082-f341c36f8983` 在旧 Release 中于 `starting_runtime` 后失败，且因 run trace 创建时机晚于 Runtime 启动，未留下可定位的 run / 生命周期记录。

本次修复在启动 Runtime 前创建最小 run trace；没有获得 thread 时仅记录 `pending`，不伪造 `codex_thread_id`。新增的安全生命周期事件为：

- `runtime_start_requested`
- `runtime_process_created`
- `app_server_ready`
- `thread_start_requested`
- `thread_started`
- `runtime_start_failed`（仅带受控 stage）

事件不保存 API Key、Runtime Bearer Token、MCP Token 或模型思维内容。启动失败测试验证会返回受控错误摘要。

## 真实生产复验

| 项目 | 结果 |
| --- | --- |
| Product task | `f919283f-6f7b-45f3-b53b-3726ba594bde` |
| Run | `65f9415b-4937-48c4-a602-71ba4753609b` |
| Conversation | `46f5eaa2-b744-4d5d-9c53-d1193529260f` |
| Codex thread | `01a08b82-4627-7790-87f1-24a434b37d0a` |
| Task status | `completed` |
| 启动链路 | 五个成功启动事件均已记录 |
| Required MCP | `enterprise_config_get`、`asset_search`、`knowledge_search`、`image_generation` 均为 `completed` |
| Final response | `final_response_received = true` |
| Artifact | 图片生成工具完成；产品任务完成并绑定 run / conversation |

## 结论

**PASS**。P0 启动可观测性已上线并通过真实图片 Agent 任务复验；未修改 RAG Retrieval V1.4、Skill Registry 或 Agent 范围。
