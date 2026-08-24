# 工作台控制面约定

官网和工作台共享用户数据库，但不共享前端代码。

1. 官网登录用户请求 `POST /api/auth/workspace/session`。
2. 官网以服务器端共享密钥签发五分钟有效的 `kunyuan_workspace_sso` Cookie，域为 `.luckio.cn`。
3. `ai.luckio.cn` 的 Nginx `auth_request` 校验 Cookie 后，才将 `/api/workspace/*` 请求转发给工作台控制面。
4. 控制面按 Cookie 中的 `user_id` 读写 `workspace_agents`、`workspace_conversations`、`workspace_messages` 和 `workspace_runs`。每个查询都必须带 `user_id` 条件。
5. 控制面通过 `runtime/kunyuan-agent-run` 调用 Harness。该启动器使用独立 `DSH_HOME`，并加载 `runtime/harness-safe.patch.yml`。

当前用户可见 API：

- `GET /api/workspace/bootstrap`
- `GET, POST /api/workspace/agents`
- `GET, POST /api/workspace/agents/{id}/messages`

响应不得包含模型 API Key、服务器文件路径、Harness 原始会话或工具调用细节。
