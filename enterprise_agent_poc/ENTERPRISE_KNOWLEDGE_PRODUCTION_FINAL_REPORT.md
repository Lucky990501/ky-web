# Enterprise Knowledge｜生产最终验收报告

验收日期：2026-09-10（Asia/Shanghai）<br>
环境：`workbench.luckio.cn` 生产 Workbench<br>
结论：**BLOCKED**

## 1. 验收范围与边界

本阶段按 Phase A 执行。实际运行的是生产 API 进程当前使用的环境，避免读取磁盘 `.env.production` 时发现的配置漂移影响结果。

已完成：

- 40 条生产 RAG 检索评测（20 条相关、10 条语义改写、10 条无知识/越界）。
- pgvector、正式 OpenAI-compatible Embedding、严格生产模式的进程内诊断。
- 脱敏结果存档。
- API、Platform MCP、Worker、Redis 和 PostgreSQL 可用性检查。

未完成：Tenant B 隔离、浏览器→上传→OSS→Redis→Worker→Codex 的完整 E2E、独立的 Embedding/vector/hybrid/Codex 时延分段，以及 Token/成本验收。因此本报告不将基础架构标记为 STABLE。

## 2. 生产基础设施证据

| 项目 | 实测状态 |
| --- | --- |
| API | `enterprise-agent-api.service` active；`/api/health` 返回 `status: ok`、`knowledge: ok` |
| Platform MCP | `enterprise-agent-mcp.service` active |
| Worker | `enterprise-agent-worker.service` active |
| Redis | 服务 active；本轮未输出或保存认证信息 |
| PostgreSQL | `pg_isready` 返回 accepting connections |
| 独立生产诊断 | PostgreSQL、`openai-compatible`、1536 维、严格模式、pgvector 均为 `ok` |

### 配置漂移发现

磁盘 `.env.production` 与正在运行的 API 进程在数据库、Embedding Provider/Model/Dimension/Base URL、`APP_ENV` 和 fallback 开关等关键变量上均不一致。直接按磁盘环境执行评测时，诊断错误地显示 pgvector 不可用；使用 API 进程环境后诊断恢复为 `ok`。这构成可复现性风险，需在下一轮修复并记录配置来源。

## 3. 40 条生产 RAG 评测

结果文件：[`evals/results/rag_v1_3_production_2026-09-10.json`](evals/results/rag_v1_3_production_2026-09-10.json)

该文件包含 40 个 case ID、预期章节、返回 Chunk/File ID、章节名、分数、接受状态、拒绝原因和时延；不包含原始 query、企业正文、API Key、Token 或隐藏推理。

| 指标 | 实测值 |
| --- | ---: |
| `answerable_acceptance_recall` | 1.000 |
| `section_recall_at_k` | 0.000 |
| `top_1_section_accuracy` | 0.000 |
| `grounded_precision` | 0.000 |
| `no_answer_rejection_rate` | 0.700 |
| `case_pass_rate` | 0.175 |

### 时延

下表是一次 `KnowledgeRetrievalService.search` 的端到端检索时延，包含正式 Embedding 查询、向量/混合检索与置信度过滤；本轮没有将其错误拆分为未测得的子阶段时延。

| 分组 | 数量 | P50 | P95 | 均值 | 最大值 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 全部 | 40 | 1,788.6 ms | 3,000.1 ms | 1,880.7 ms | 3,468.0 ms |
| 相关 | 20 | 1,770.5 ms | 2,477.7 ms | 1,780.9 ms | 2,784.3 ms |
| 语义改写 | 10 | 1,710.6 ms | 3,468.0 ms | 1,974.4 ms | 3,468.0 ms |
| 无知识/越界 | 10 | 1,858.9 ms | 3,026.0 ms | 1,986.6 ms | 3,026.0 ms |

## 4. 失败分析

### 章节 Grounding 失败

30 个正例均返回了候选结果，但没有一个候选章节与评测集的 `expected_section` 精确匹配。

原因不是“没有检索结果”：评测集使用了 `活动场次`、`嘉宾档案`、`推荐阅读`、`历史日期索引` 等抽象章节名，而实际索引中保存的是更细粒度标题，例如 `2023-2024秋季｜活动时间`、具体嘉宾姓名、具体书名和 `2017年历史场次日期索引`。当前评测采用精确字符串比较，因此不能把这些候选自动视为 Grounded 命中。

在建立经人工确认的 canonical section ID 或别名映射前，不能把 `section_recall_at_k=0` 解释为纯粹的向量召回失败，也不能以 `answerable_acceptance_recall=1` 宣称 RAG 通过。

### 无知识/越界误召回

10 个反例中有 3 个返回候选：

- `n03`：绝对化效果承诺问题；
- `n06`：未证实课程价格问题；
- `n07`：不存在课程的师资/名额问题。

这使拒答率为 0.7。虽然 Agent 指令仍要求不得编造企业事实，但检索层仍需要结合知识可答范围、敏感/承诺/价格类意图和更严格的拒答策略。

## 5. 未完成 Gate 项

1. 临时 `rag-isolation-test` Tenant B 尚未创建；浏览器/API、检索服务、MCP Token、Agent Grounding 的 A/B 隔离尚未实测。
2. 未使用真实企业文件重新完成 OSS → Redis → Worker → Parser → Chunk → Embedding → pgvector → Codex 的浏览器端 E2E。
3. 未分别记录 Embedding、向量搜索、Hybrid Retrieval、MCP 和 Codex Run 的独立时延。
4. DeepSeek/Codex usage、Token 和成本仍未完成可靠验收；不得估算或伪造。
5. 当前生产工作树含未提交文件；部署版本为 `a2a6609`，不满足可复现发布的要求。

## 6. 必须完成的修复与复验

1. 为知识摄取写入稳定的 canonical section ID，或为固定评测集建立人工审核的章节别名映射；重新运行 40 条评测。
2. 为价格、绝对化承诺、未证实师资/名额等不可答意图增加检索前/后拒答策略，并复验 10 条反例。
3. 消除 systemd 进程环境与 `.env.production` 的配置漂移，使用单一受控配置来源；再执行健康和独立评测。
4. 创建、验证并删除 `rag-isolation-test` Tenant B，保存不含正文的隔离证据。
5. 在真实浏览器上传路径中完成 OSS、Redis、Worker、pgvector、MCP 和 Agent 最终回复的全链路验收。

## 7. 阶段决策

### 本地修复已完成，尚未部署/复验（2026-09-10）

- 新增 `QueryAnswerabilityPolicy`：对绝对化承诺、显式不存在实体直接拒答；价格/收费类查询必须有价格证据才返回候选。
- 新增 `scripts/verify_runtime_config.py` 与管理员诊断中的配置指纹：仅比较关键运行变量是否一致，不记录配置值或密钥。
- 评测器支持经人工审核的实际小节别名文件；模板为 `evals/rag_v1_3_section_aliases.template.json`。模板没有预填别名，不能将这次检索结果自动当作真值。

这些改动只经过本地回归（`27 passed, 1 warning`），尚未部署到生产，也未重新运行 40 条评测；本报告的 BLOCKED 结论不变。

**Phase A 为 BLOCKED。**

依据阶段规则，停止进入 Phase B（Skill Registry V1）、Phase C（Agent Expansion V1）和 Phase D。只有完成上述修复并重新获得非阻塞 `PASS` 或 `PASS WITH ISSUES` 后，才能将 Enterprise Knowledge 基础架构标记为 STABLE。
