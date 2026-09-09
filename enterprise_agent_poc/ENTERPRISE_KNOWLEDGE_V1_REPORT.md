# Enterprise Knowledge V1 验收记录

日期：2026-09-09

## 已完成并验证

- 文件上传接口 `POST /api/v1/knowledge/files` 仅从登录会话获取 Tenant，支持 PDF、DOCX、TXT、MD，限制单文件大小，并写入私有对象存储。
- 每个文件落入企业默认知识库；若请求指定知识库，会校验该知识库归属当前 Tenant。
- 后台处理状态为 `uploaded → queued → parsing → chunking → embedding → indexing → ready/failed`，失败可重试，删除会同时删除对象与索引记录。
- 解析、清洗、结构优先切分、重叠切分、嵌入 Provider 抽象与租户范围检索已实现。`knowledge_search` MCP 工具保持原工具名，对 Agent 无需改调用方式。
- 前端提供上传、处理状态、解析片段、失败原因/重试，以及检索测试入口；上传不会再退化为粘贴文本。
- 对话历史会显示已持久化的用户/智能体正文；旧会话若没有消息记录，会从当时保存的任务输入与最终结果只读恢复。所有会话仍绑定原 `codex_thread_id`，可继续对话。
- 任务进度使用 SSE 推送服务端已保存阶段，完成事件会先即时显示最终正文，再刷新持久化会话。没有显示或保存模型隐藏推理。

## 自动化与线上检查

- 本地：`python -m pytest tests -q`，14 项通过；包含 TXT 文件的对象存储 → 解析 → Chunk → 索引 → Tenant 检索隔离验证，以及旧会话上下文恢复验证。
- 本地：`python -m compileall -q app` 与 `node --check app/static/workbench.js` 通过。
- 线上：提交 `f7e708b` 已部署；API、Worker、Platform MCP 三项服务均为 `active`；`/api/health` 返回正常。
- 线上任务队列为 Redis，对象存储为 OSS，知识文件会由 Worker 异步处理。

## 未标记为通过的项目

线上 PostgreSQL 当前未提供 `pgvector` 扩展。因此，V1 现在使用可替换的 `local-hash` 嵌入与租户隔离的混合评分降级路径，不能将其宣称为 pgvector 的生产级语义检索验收通过。

在安装并启用 pgvector、配置正式 Embedding Provider 后，下一次验收应完成：

1. `CREATE EXTENSION vector` 与向量列/索引迁移；
2. 写入正式嵌入向量，并使用数据库向量距离参与召回；
3. 使用一次获授权的真实企业文件，验证 OSS 上传、Worker、检索测试与 Agent MCP `knowledge_search` 的完整闭环；
4. 测试后按企业策略保留或清理该测试文件及其 Chunk。

在上述条件完成前，建议把当前状态记为“文件知识库 V1 可用，生产语义检索待基础设施解除”。
