# RAG 生产复评验收报告

验收日期：2026-09-10（Asia/Shanghai）<br>
环境：`workbench.luckio.cn` 生产 Workbench<br>
评测入口：`scripts/run_rag_v1_3_production.sh zhiy-e-intelligence`<br>
结论：**BLOCKED**

## 1. 评测运行证据

安全 Runner 已完成固定 `rag-v1.3` 数据集的真实生产检索：20 条知识正例、10 条语义改写、10 条无知识/越界问题，共 40 条。

输出中仅包含 case ID、query SHA-256、预期章节、Chunk/File ID、章节名、分数、接受状态与时延；未包含原始 Query、企业正文、API Key、Bearer Token 或隐藏推理。

| 项目 | 结果 |
| --- | --- |
| `status` | `completed` |
| Tenant | `zhiy-e-intelligence` |
| 评测集版本 | `rag-v1.3` |
| 章节别名版本 | `none` |
| 用例数 | 40 |
| 生产配置一致性 | 已在部署验收中确认 `matches: true` |

## 2. 核心指标

| 指标 | 实测值 |
| --- | ---: |
| `answerable_acceptance_recall` | 1.000 |
| `section_recall_at_k` | 0.000 |
| `top_1_section_accuracy` | 0.000 |
| `grounded_precision` | 0.000 |
| `no_answer_rejection_rate` | 0.100 |
| `case_pass_rate` | 0.025 |

## 3. 结果判读

### 正例

30 条正例都得到候选结果，故 `answerable_acceptance_recall=1.0`。但当前别名版本为 `none`，评测集的抽象章节名与索引中的实际细粒度小节名尚未完成审核映射；因此章节命中、Top-1 和 grounded precision 均为 0。该数值不能单独证明向量召回完全失效，也不能作为 Grounding 通过依据。

### 无知识/越界

10 条无知识用例中仅 1 条得到拒绝，另外 9 条仍返回候选 Chunk，故 `no_answer_rejection_rate=0.1`。这不满足最终 Gate 对“无可靠企业资料时必须拒答”的要求。

其中，已在结果中确认绝对承诺、价格/课程、私密信息、未公开信息和明显越界类别仍可能返回候选。需要先核对生产实际运行的 Query Guard 版本与配置，再修复并复验；不得仅通过调整评测映射掩盖该失败。

## 4. 本阶段结论

**生产 40 条 Eval 已真实完成，但 Phase A 不通过。**

未满足进入 Skill Registry 的必需条件：

1. 章节别名尚未经人工审核并固定为 `rag-section-aliases-v1`；
2. 无知识最终验收失败；
3. Tenant A/B 生产隔离尚未完成；
4. 浏览器知识库 E2E 尚未完成；
5. 三个 Agent Grounding 最终回归尚未完成；
6. 生产仓库历史未提交改动尚未完成可复现 Release 收口。

因此 Enterprise Knowledge 不能标记为 STABLE，不得进入 Skill Registry、Agent Expansion 或其他后续阶段。

## 5. 下一步

1. 只读核对生产 `app/knowledge.py` 和运行环境中 `KNOWLEDGE_QUERY_GUARD_ENABLED` 的实际生效状态，以定位为何 9 条反例未拒绝。
2. 导出不含企业正文的 `RAG_SECTION_ALIAS_REVIEW.md`，由知识内容负责人审核并发布 `rag-section-aliases-v1`。
3. 在 Query Guard 修复与别名审核后重新运行相同 40 条生产评测，生成下一份复评报告。
4. 之后完成 Tenant A/B、浏览器 E2E、三个 Agent Grounding 与生产 Git 收口，才可进行 Phase A 最终判断。

## 6. 可观测性限制

当前 Codex/DeepSeek 未返回可可靠审计的 Token Usage；本阶段保持 `token_usage = null`、`estimated_cost = null`，不以此阻断 Phase A，也不进行估算。
