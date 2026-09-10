# 本轮测试报告

日期：2026-09-10<br>
范围：RAG V1.3 评测可信度、检索 Trace 脱敏、MCP 审计与现有回归测试。

## 结论

**通过。**

在隔离的临时 SQLite 数据目录执行完整 pytest 回归，结果如下：

```text
23 passed, 1 warning in 5.43s
```

唯一警告来自 Starlette `TestClient` 对当前 `httpx` 版本的弃用提示；本轮没有失败、跳过或错误用例。

## 执行方式

```powershell
python -m pytest -q -p no:cacheprovider
```

测试进程配置了临时的 `ENTERPRISE_POC_DATA_DIR` 与 `ENTERPRISE_POC_DATABASE_URL`，未使用项目运行数据库作为测试目标；同时设置 `PYTHONDONTWRITEBYTECODE=1`，避免生成源码缓存文件。

## 本轮新增覆盖

### RAG V1.3 评测

- 正例只有在检索结果包含 `expected_section` 时才通过。
- 返回了错误章节时，虽然 `accepted=true`，但 `section_match_at_k=false` 且用例失败。
- 反例只有在没有返回检索结果时才通过。
- 指标分别计算：
  - `answerable_acceptance_recall`
  - `section_recall_at_k`
  - `top_1_section_accuracy`
  - `grounded_precision`
  - `no_answer_rejection_rate`
  - `case_pass_rate`

### 检索 Trace 与 MCP 审计

- 检索 Trace 仅保存查询 SHA-256、查询长度、Chunk/File ID、评分、接受状态和拒绝原因。
- 测试确认 Trace 不包含原始 query 或 Chunk 正文。
- `knowledge_search` 只有在底层检索成功后才记录 `completed`；检索异常时记录 `failed`。

### 既有回归范围

- Cookie 登录、角色权限与工作台访问。
- Tenant/用户边界与会话归属。
- 知识文件解析、分块、索引与 Tenant 隔离检索。
- 生产模式下 pgvector/正式 Embedding readiness 限制。
- Runtime Profile、MCP Token 签名和 Scope 校验。
- 仅部署当前 Profile Skill。
- Run Trace、任务状态和最终回复校验。

## 未执行项目

以下项目需要受控生产环境、获授权企业数据或基础设施权限，因此本轮没有执行：

1. 真实生产 Tenant 的 40 条 RAG V1.3 数据集评测与指标归档。
2. 临时 Tenant B 的文件上传、检索、MCP Token、Agent Grounding 隔离验证及测试数据清理。
3. Docker Compose、PostgreSQL pgvector、Redis Worker 和 OSS 的端到端集成验证。
4. 真实 DeepSeek/Codex Token 用量、成本和延迟验收。

## 后续建议

在获准访问生产验收环境后，先运行 `enterprise_agent_poc/scripts/run_rag_v1_3_eval.py`，保存不含正文和密钥的 JSON 结果；随后执行双 Tenant 隔离回归，并将最终指标补充到 Enterprise Knowledge 验收报告。
