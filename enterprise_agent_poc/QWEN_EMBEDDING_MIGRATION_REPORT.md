# Qwen Embedding 迁移评估报告

报告日期：2026-09-11

目标 Provider：`aliyun-bailian`

目标模型：`qwen3.7-text-embedding`

目标维度：1536

目标索引版本：`rag-index-v3-qwen`

## 当前结论

**BLOCKED。** Stage 1 独立 Provider 配置与 Stage 2 北京官方 Endpoint 稳定性探针均已通过；Stage 3 的隔离 Profile/Index 工程实现已通过本地测试，但尚未执行生产 migration。287 Chunk Re-Embedding、固定 40 条 Eval、Agent Grounding 与生产切换均未完成，因此不能给出最终 PASS，也不会切换 active index。

## 生产保护状态

| 项目 | 当前状态 |
|---|---|
| active provider | `openai-compatible`，未修改 |
| active model | `text-embedding-3-small`，未修改 |
| active dimension | 1536，未修改 |
| active index | `rag-index-v2`，未修改 |
| pgvector | 未修改 |
| Reindex | 未执行 |
| RAG V1.4 策略 | 未修改 |
| Worker 配置 | 未修改 |
| 旧索引删除 | 未执行 |

## Stage 1｜独立 Provider 配置

状态：`PASS`

已新增独立、未激活的迁移评估 Profile：

```text
provider = aliyun-bailian
model = qwen3.7-text-embedding
dimension = 1536
index_version = rag-index-v3-qwen
```

实现内容：

- 新增独立 `EmbeddingProfile`，不复用或覆盖当前生产 `EMBEDDING_*`；
- 新增 `AliyunBailianEmbeddingProvider`，通过 OpenAI-compatible `/embeddings` API 调用；
- 每次请求显式发送 `dimensions=1536`；
- 强制模型为 `qwen3.7-text-embedding`；
- 强制索引版本为 `rag-index-v3-qwen`；
- 强制使用 HTTPS、华北2（北京）区域、带 Workspace ID 的官方 `*.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` Endpoint；
- `llm-api.net` 等聚合 Gateway 会被配置校验拒绝；
- 新 Provider 尚未接入当前 `embedding_provider_for(settings)`，不会改变线上查询与文档向量生成；
- 新增 50 次连续稳定性探针脚本，结果仅包含脱敏域名、逐次状态、HTTP 状态、延迟、维度和错误类型；
- 探针不记录 API Key、Authorization Header、响应向量或企业数据。

配置变量：

```text
ALIYUN_BAILIAN_EMBEDDING_PROVIDER=aliyun-bailian
ALIYUN_BAILIAN_EMBEDDING_MODEL=qwen3.7-text-embedding
ALIYUN_BAILIAN_EMBEDDING_DIMENSION=1536
ALIYUN_BAILIAN_INDEX_VERSION=rag-index-v3-qwen
ALIYUN_BAILIAN_EMBEDDING_BASE_URL=
ALIYUN_BAILIAN_API_KEY=
```

以上变量与当前生产 `EMBEDDING_PROVIDER`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSION`、`EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY` 完全隔离。

## Stage 1 测试

| 检查项 | 结果 |
|---|---:|
| Python 静态编译 | PASS |
| 定向 Provider/Profile 测试 | 3 passed |
| 全量测试 | 59 passed |
| 官方 Endpoint 强校验 | PASS |
| 聚合 Gateway 拒绝 | PASS |
| 1536 维请求参数 | PASS |
| 非 1536 返回拒绝 | PASS |
| 50/50 Gate 判定 | PASS |

全量测试存在 1 条既有 Starlette/httpx 弃用警告，不影响本阶段结果。

## 官方规格依据

阿里云百炼官方文档确认：

- `qwen3.7-text-embedding` 支持 2560、2048、1536、1024、768、512、256 维；
- 该模型支持华北2（北京）区域；
- 北京 OpenAI-compatible Endpoint 使用 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`；
- API Key 与区域相关；
- OpenAI-compatible Embeddings 请求支持 `dimensions` 参数。

来源：[qwen3.7-text-embedding 模型说明](https://help.aliyun.com/zh/model-studio/qwen3-7-text-embedding)；[地域与接入域名](https://help.aliyun.com/zh/model-studio/regions/)

## Stage 2｜官方 Endpoint 稳定性探针

状态：`PASS`

执行位置：当前生产主机；调用目标：阿里云百炼华北2（北京）Workspace 官方 OpenAI-compatible Endpoint。

| 指标 | 结果 |
|---|---:|
| provider | `aliyun-bailian` |
| base_url_domain | `<workspace>.cn-beijing.maas.aliyuncs.com` |
| model | `qwen3.7-text-embedding` |
| requested_dimension | 1536 |
| total_requests | 50 |
| success_count | 50 |
| failure_count | 0 |
| success_rate | 100% |
| HTTP status distribution | 200×50 |
| timeout_count | 0 |
| latency_p50 | 140.06 ms |
| latency_p95 | 479.31 ms |
| min_latency | 102.62 ms |
| max_latency | 560.94 ms |
| returned_dimension | 1536 |
| dimension_consistent | true |
| error_summary | `{}` |

稳定性 Gate：`PASS`。50 次请求全部成功且全部返回 1536 维，可以进入新 Embedding Profile 与隔离索引的工程阶段。

凭据处理：用户提供的凭据文件仅临时传输至生产主机，权限设为 600；远端 `trap` 与客户端兜底清理均已执行。脱敏汇总重新读取并验证 50 条记录完整后删除。未记录 API Key、Authorization Header、完整 Workspace ID 或响应向量。

## 后续门禁

## Stage 3｜隔离 Profile / Index 工程实现

状态：`LOCAL PASS / PRODUCTION PENDING`

已完成的隔离设计：

- 新增 `embedding_index_profiles`，显式记录 provider、model、dimension、index_version 与 active/candidate/rollback 状态；
- 新增独立 `knowledge_chunk_embeddings`，Qwen 向量不覆盖 `knowledge_chunks.embedding`；
- 数据库外键强制 `tenant_id + chunk_id` 必须对应同一知识 Chunk，避免候选索引产生跨租户关联；
- `rag-index-v2` 登记为 active profile，`rag-index-v3-qwen` 仅登记为 candidate；
- Reindex 默认 dry-run，执行前要求旧索引恰好 287/287 完整，候选索引只能是 0 或完整 287，拒绝残缺状态；
- 全部 Qwen 向量先在内存验证数量和 1536 维一致性，再在单一数据库事务中写入候选表；
- 新增候选检索服务，查询只读取指定 tenant、provider、model、dimension 与 index_version；
- 候选检索复用现有 Query Guard、Confidence Gate、Canonical Metadata、Metadata Boost 与结果 margin，未接入生产 selector；
- 新增固定 `rag-v1.3` + `rag-section-aliases-v1` 候选 Eval runner，不修改数据集或策略。

本地验收：

| 检查项 | 结果 |
|---|---:|
| Python 静态编译 | PASS |
| Qwen 定向测试 | 10 passed |
| 项目全量测试 | 66 passed |
| 既有警告 | 1 条 Starlette/httpx 弃用警告 |
| active provider/index 代码接线 | 未修改 |
| 生产 migration 006 | 未执行 |
| 生产 Reindex | 未执行 |

本阶段只有在独立 Release 上完成 migration 预检、受控发布和生产 dry-run 后，才可从 `PRODUCTION PENDING` 升级为生产 PASS。

以下阶段均尚未执行：

1. 建立并持久化 `rag-index-v3-qwen` Profile；
2. 为当前 287 个 Chunk 全量生成 Qwen 1536 维向量；
3. 保留 `rag-index-v2` 并验证新旧向量完全隔离；
4. 使用固定 `rag-v1.3` 40 条数据集进行不改策略的对照 Eval；
5. 三个 Agent Grounding 回归；
6. 满足全部切换条件后等待人工确认；
7. 人工确认前不切换 active index，不删除旧索引。

## 下一阶段

设计并验证新旧 Embedding Profile/Index 的物理隔离。现有 `knowledge_chunks.embedding` 继续保留 `rag-index-v2`；新 Qwen 向量必须写入独立、可回滚的数据结构，禁止覆盖旧向量，也禁止在检索中混合 OpenAI document vector 与 Qwen query vector。
