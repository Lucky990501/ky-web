# Enterprise Knowledge V1.1｜生产级语义检索验收

日期：2026-09-09  
结论：**BLOCKED**

## 实际基础设施证据

- PostgreSQL：16.14（Ubuntu 16.14-0ubuntu0.24.04.1）。
- 当前数据库账号：`enterprise_agent`；不是 superuser，不能创建数据库或角色。
- `pg_available_extensions` 未返回 `vector`；执行 `CREATE EXTENSION IF NOT EXISTS vector` 的实际结果为 `extension "vector" is not available`。
- 因此阻塞原因是当前 PostgreSQL 服务未安装/未提供 pgvector 扩展包，而不仅是应用权限问题。

## Embedding 配置证据

- 当前 Provider：`local-hash`。
- 当前 Model：`local-hash-v1`。
- 当前 Dimension：128。
- `EMBEDDING_API_KEY` 未配置。

这不是正式 Embedding Provider，不满足 V1.1 的“真实 Embedding + pgvector”要求。

## 本阶段完成的防误报保护

- 新增 `EMBEDDING_API_KEY` 和 `KNOWLEDGE_ALLOW_FALLBACK` 配置读取；生产环境默认禁止 fallback。
- 新增 Knowledge Runtime 诊断：检查 PostgreSQL、已安装 pgvector、Embedding Provider 与 API Key，不输出密钥。
- 生产模式且不允许 fallback 时，处理与检索会明确拒绝执行；不会把 local-hash 静默当作正式语义检索。
- `/api/health` 会返回 `status: degraded` 和 `knowledge: degraded`；管理员可通过 `GET /api/v1/knowledge/diagnostics` 查看诊断详情。
- 自动化测试覆盖生产环境禁用 local-hash fallback，当前测试 15 项通过。

## 未执行的验收项

以下项没有真实基础设施前不能伪造执行或标记 PASS：

- vector 类型迁移、HNSW/IVFFlat 索引；
- 正式 Embedding 写入和版本隔离 reindex；
- pgvector similarity / hybrid score 真实向量分；
- 获授权真实企业文件的 OSS → Redis Worker → Embedding → pgvector → 检索 → Agent MCP 完整闭环；
- 语义改写、无知识、Tenant A/B、三个 Agent 与性能数据。

## 解除条件

1. 在当前 PostgreSQL 实例安装并允许 `vector` 扩展，或迁移到支持 pgvector 的 PostgreSQL 服务。
2. 由平台配置正式 `EMBEDDING_PROVIDER`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSION` 与 `EMBEDDING_API_KEY`（仅写入服务器环境变量）。
3. 完成向量迁移、重新索引，再使用一份获授权真实企业文件进行端到端验收。

在以上三项完成前，Enterprise Knowledge V1.1 必须保持 **BLOCKED**，不得称为生产级语义检索已通过。
