# Workbench 新增智能体前端与运行时收口

## 已上线

- Agent Template Catalog：图片、文案、活动策划三个模板，包含名称、Slug、图标、描述、默认 Runtime、Skill Manifest、积分和能力边界。
- Tenant Agent Instance：按租户启用；任务提交前校验当前企业实例状态。
- 统一 Agent Workspace：三个智能体共用创作页，但会话按 `agent_id` 隔离，禁止跨 Agent 复用 Conversation / Codex Thread。
- 工作台卡片由 `/api/v1/workspace` 返回的模板数据渲染，不再硬编码文案与活动策划“即将上线”。
- 权限边界：文案与活动 Token 仅具备企业配置、知识、素材读取 scope；`image:generate` 仅授予图片智能体。
- 成功扣费按 Agent Template 配置执行：图片 20、文案 3、活动策划 8 积分。

## 已验证

生产环境提交：`3dbc610`。API、Worker、Platform MCP 均为 active；合成 Tenant A/B 与全部运行 Trace 已清理（0 remaining）。

## 后续建议

1. 为 DeepSeek 接入可用 usage / pricing 映射，补齐成本统计。
2. 为检索工具增加同轮去重与上限，稳定长活动方案的延迟与调用成本。
3. 在产品数据库中增加可审计的 Agent 启用/停用管理界面，再开放给企业管理员。
