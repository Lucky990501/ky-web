# API易 Embedding Provider 验收报告

报告日期：2026-09-12

## 当前结论

**BLOCKED。** 独立候选 Provider、100次稳定性探针与固定40条只读 Eval runner 已进入本地实现阶段，但当前工作区尚无 API易专用 API Key，因此没有执行真实请求，也没有生成稳定性或兼容性结论。

## 验收范围

| 项目 | 值 |
|---|---|
| provider_id | `apiyi-candidate` |
| 官方 Endpoint | `https://api.apiyi.com/v1/embeddings` |
| model | `text-embedding-3-small` |
| dimension | 1536 |
| 文档索引 | 现有 `rag-index-v2` |
| Reindex | 禁止 |
| 生产配置切换 | 禁止 |

API易官方文档列出 `text-embedding-3-small`、1536 维及 OpenAI-compatible `/v1/embeddings` 调用方式：[文本向量化](https://docs.apiyi.com/api-capabilities/text-embedding)、[Create Embeddings](https://docs.apiyi.com/api-reference/embeddings/create-embeddings)。

## 安全设计

- API Key 仅从 `APIYI_EMBEDDING_API_KEY` 临时注入；
- Endpoint 强制为 `https://api.apiyi.com/v1`，拒绝其他 Gateway；
- 候选 Settings 为进程内不可变副本，不覆盖全局或生产环境；
- 生产文档向量仍要求 `openai-compatible / text-embedding-3-small / 1536 / rag-index-v2`；
- Eval 只替换 Query Embedding 请求的 Endpoint 与 Key；
- 不记录 API Key、Authorization Header 或完整向量；
- 不 Reindex，不修改 pgvector，不修改冻结的 RAG V1.4 策略。

## 稳定性 Gate

计划连续执行100次真实请求。PASS 条件：成功率不低于99%、所有成功响应始终为1536维，且不得出现连续3次或以上的 HTTP 500/502/503/timeout。

## 固定40条 Eval

仅在稳定性 PASS 后执行。继续使用 `rag-v1.3`、`rag-section-aliases-v1`、现有 Query Guard、Confidence Gate、Hybrid 权重、Metadata Boost、threshold 与 result margin；不重新生成任何文档向量。

## 待提供输入

需要 API易专用 API Key。建议保存为工作区内忽略文件 `enterprise_agent_poc/env.apiyi`：

```text
APIYI_EMBEDDING_API_KEY=<仅本地填写>
APIYI_EMBEDDING_BASE_URL=https://api.apiyi.com/v1
```

凭据就绪前，真实稳定性与兼容性 Gate 均保持 BLOCKED。
