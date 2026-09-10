# RAG Phase A P0 本地实现验收

验收日期：2026-09-10  
代码提交：`a038d82`  
范围：仅 Query Guard、Confidence Gate 审计结果、生产安全诊断、固定 40 条评测执行器与 section alias 审核资产。

## 已完成

- Query Guard 已升级为 `query-guard-v2`，在生成向量前拦截：私密/凭据、未公开企业信息、明显越界事务、显式不存在实体、无依据绝对承诺。
- 价格类请求保留“有检索证据才允许”的后检索判断。
- `KnowledgeRetrievalService.search_outcome()` 返回结构化决策；被 Query Guard 拒绝时固定为 `accepted=false`、`rejection_reason=query_guard`、`results=[]`，同时以枚举型 `rejection_detail` 记录类别。
- 保留既有 `search()` 接口，避免影响运行时调用方。
- Confidence Gate 保留最终分、向量分、关键词证据，并增加无关键词情况下的 Top-1/Top-2 分差拒绝。
- 新增 `scripts/diagnose_rag_policy.py`，仅输出允许审计的 Guard 开关、策略版本、阈值和运行时配置指纹；不输出凭据或原始 URL。
- 固定 40 条评测执行器新增预检、安全失败输出、查询哈希和查询级拒绝结果。
- 新增 [RAG_SECTION_ALIAS_REVIEW.md](RAG_SECTION_ALIAS_REVIEW.md)，仅为待人工审核的候选清单，未发布正式 alias。

## 本地验证证据

| 检查 | 结果 |
| --- | --- |
| `python -m pytest enterprise_agent_poc/tests -q` | 32 passed，1 个第三方弃用警告 |
| `python -m compileall -q enterprise_agent_poc/app enterprise_agent_poc/scripts` | 通过 |
| `git diff --check` | 通过；仅有 Git CRLF 转换提示 |

## 未完成 / 阻塞

- 尚未将 `a038d82` 推送到远程 `master` 或部署至生产：该外部变更需要明确确认。
- 因此尚未运行 v2 生产 40 条复测，不能据此宣布 P0 生产通过。
- `rag-section-aliases-v1` 仍需知识库内容负责人审核并发布；未审核候选不得用于正式指标。

## 当前阶段结论

本地实现：**通过**。  
生产验收：**BLOCKED（等待明确的默认分支推送与生产部署确认）**。
