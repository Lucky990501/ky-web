# Enterprise Knowledge Phase A 最终生产验收

验收日期：2026-09-10  
验收范围：`Enterprise Knowledge｜Phase A Final Production Gate`  
生产租户：`zhiy-e-intelligence`

## 最终结论

**BLOCKED**

RAG Retrieval V1.4 的生产验收仍有效：固定 40 条评测的 Section Recall@K 为 0.8667、Top-1 Section Accuracy 为 0.6667、Grounded Precision 为 0.8667、No-answer Rejection Rate 为 1.0000，且 `rag-index-v2 / knowledge-metadata-v1` 已在 287 个 Chunk 上完成重建。

但 Phase A 的最终门禁要求 Tenant A/B、正式浏览器 E2E、三个 Agent Grounding、无知识回归、Thread Resume 和可复现 Production Release 均通过。当前至少两项核心门禁没有可复核的本轮生产证据，因此不能将 Enterprise Knowledge 标为 STABLE，也不能进入 Skill Registry V1。

## 1. 已确认通过的前置项

| 项目 | 证据 | 状态 |
| --- | --- | --- |
| RAG V1.4 固定 40 条复评 | `section_recall_at_k=0.8667`，`top_1_section_accuracy=0.6667`，`grounded_precision=0.8667`，`no_answer_rejection_rate=1.0000` | PASS |
| Canonical 诊断 | 30/30 正例 Top-5、Top-1 canonical_section 正确；10/10 无知识反例拒答 | PASS |
| 生产索引 | 287/287 为 `rag-index-v2` / `knowledge-metadata-v1` | PASS |
| 服务健康 | API、MCP、Worker 均 active；`/api/health` 为 `status: ok`、`knowledge: ok` | PASS |
| 本地回归 | 41 passed；仅有既有 Starlette TestClient 弃用告警 | PASS |
| 本地编译 | `python -m compileall -q app scripts tests` | PASS |

相关证据：

- `RAG_RETRIEVAL_QUALITY_V1_4_REPORT.md`
- `RAG_V1_4_PRODUCTION_REINDEX_ACCEPTANCE_2026-09-10.md`
- `evals/results/rag_v1_4_production_2026-09-10.json`

## 2. Tenant A/B 生产隔离

**未执行，不能判定 PASS。**

文档要求创建临时 Tenant B `rag-isolation-test`，分别以产品路径上传不同文件，并验证 Knowledge Retrieval、Platform MCP knowledge_search、Agent Grounding、Session、Runtime Bearer Token、MCP Scope 与 Codex Runtime 的隔离，完成后删除 Tenant B 的用户、文件、Chunk、向量、OSS 对象、Runtime Profile 与 Token。

当前生产 API 没有 Tenant Provision 或 Tenant Cleanup 管理入口。直接向生产数据库插入/删除 Tenant、用户和知识记录无法替代要求的产品级路径，也不能安全证明 OSS 对象、Runtime 目录和 Token 已全部清理。因此本轮没有创建 `rag-isolation-test`，避免留下无法由受控产品流程回收的数据。

## 3. Browser → Knowledge → Agent E2E

**未执行，不能判定 PASS。**

已使用正式工作台地址启动浏览器验收，但浏览器自动化在读取工作台标签时连续两次超时并重置，未获得可验证的登录或页面状态。没有以 curl、内部 Service 调用或手动触发 Worker 替代该门禁。

因此以下链路均没有本轮浏览器证据：登录、知识库上传、OSS、Redis、Worker、Parse、Chunk、Embedding、pgvector ready、检索测试、Agent 问答、knowledge_search 与最终回复。

## 4. 三个 Agent Grounding、无知识回归与 Thread Resume

**未执行，不能判定 PASS。**

仓库存在较早阶段的 Agent 和 Resume 报告，但 Phase A 需要基于当前生产版本重新验证：

| 门禁 | 当前状态 |
| --- | --- |
| 文案 Agent：config → knowledge → asset → final | 未验证 |
| 活动策划 Agent：config → knowledge → asset → final | 未验证 |
| 图片 Agent：config → knowledge → asset → image → final | 未验证 |
| 三个 Agent 无知识不编造 | 未验证 |
| 三个 Agent 多轮、Runtime Restart/Resume | 未验证 |
| 不能跨 Agent Resume | 仅有代码与旧测试证据；本轮生产未验证 |

这些测试会创建生产任务、Run Trace、可能的生成资产与 OSS 对象；应在可清理的临时 Tenant 中完成，并由 Trace 记录工具调用与 Thread/Profile 一致性。

## 5. Production Release 收口

**BLOCKED。**

本轮生产只读审计结果：

| 项目 | 实测结果 |
| --- | --- |
| 生产 Git HEAD | `a2a6609` |
| 当前本地 V1.4 提交 | `2bee63d`、`d015bc4`、`c4fdde0`、`bef9cdb` |
| 生产工作树 | 10 个已修改应用文件，及 `app/codex_provider.py`、`app/knowledge_metadata.py`、`app/server.py`、`build/`、`evals/`、多个 scripts 等未跟踪文件 |
| 服务状态 | API、MCP、Worker 均 active |
| GitHub 同步 | 本地 `master` 领先 `origin/master`；多次正常 push 因连接重置失败 |
| Migration Runner | 未发现可记录/应用 `001` 至 `004` 的统一 runner 或 `schema_migrations` 状态表 |

因此当前生产运行状态依赖历史工作树叠加，而不是一个可从 Git Commit、已应用 Migration 与脱敏环境配置重建的正式 Release。该状态不能创建 `workbench-core-v1.0` 标签，也不能标记 Production Release PASS。

## 6. 冻结确认

本轮没有修改下列 RAG 项：Embedding、Chunk Size、Hybrid Weight、Query Guard、Confidence Gate、Canonical Taxonomy、Metadata Boost、Section Alias。

本轮没有引入 Reranker、Query Rewrite、GraphRAG、Parent-Child Retrieval、Multi-Agent 或新 RAG 算法；也没有提前实现 Skill Registry。

## 7. 恢复条件与下一步

1. 建立最小、可审计的管理员 Tenant Provision/Cleanup 工具或运维 Runbook：只允许目标 `rag-isolation-test`，显式列出并验证数据库行、OSS keys、Runtime/Profile 目录、短期 Token 和 Trace 的创建/删除。
2. 恢复可用的正式浏览器自动化会话，或由人工在 Workbench 中以临时验收账号执行上传和 Agent 流程；浏览器结果需保留脱敏截图/任务 ID/Run Trace 证据。
3. 在临时 Tenant A/B 完成隔离、三 Agent Grounding、无知识、三 Agent Resume 和跨 Agent Resume 拒绝测试；先验证清理清单，再删除 Tenant B。
4. 创建干净的受控 Release：从明确 commit 建立独立 release 目录，执行受版本登记的 migrations，注入脱敏环境配置，切换服务并完成健康检查；不得覆盖当前历史工作树。
5. 正常同步本地 `master` 至 GitHub `origin/master`。当前网络连接重置未解决前，不应宣称 Git Release 已发布。
6. 完成上述项目后重跑 Phase A 门禁，并将结论更新为 PASS 或 PASS WITH ISSUES；只有届时才可写入：`Enterprise Knowledge: STABLE`、`RAG Retrieval V1.4: FROZEN`、`Next Phase: Skill Registry V1`。

## 8. 当前状态声明

```text
Enterprise Knowledge: NOT STABLE
RAG Retrieval V1.4: FROZEN (precondition passed)
Next Phase: BLOCKED pending Phase A final production gates
```
