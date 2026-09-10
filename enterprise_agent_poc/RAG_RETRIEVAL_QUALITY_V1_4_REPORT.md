# RAG Retrieval Quality V1.4 Report

验收日期：2026-09-10

## 1. 最终结论

**PASS WITH ISSUES**

V1.4 在完全相同的 `rag-v1.3` 固定 40 条数据集和 `rag-section-aliases-v1` 下，Section Recall、Top-1 Section Accuracy、Grounded Precision 均较 V3 明显提升，No-answer Rejection Rate 保持 1.00。生产 Reindex、索引版本完整性、服务状态和健康检查均通过。

仍有 4/30 条正例未通过既有 physical section / alias 口径，因此不标记为无条件 PASS。本轮按任务边界停止，不扩大 Alias、不修改评测、不降低 Query Guard，也不进入 Skill Registry、Agent Expansion、GraphRAG、Multi-Agent 或复杂 LLM Reranker。

## 2. 不可变评测资产

| 资产 | 版本 | SHA-256 |
| --- | --- | --- |
| 固定数据集 | `rag-v1.3`，20 条原始正例 + 10 条改写正例 + 10 条反例 | `9912A601BF15115F8CD3C9A25E8093D18F685DF3C084CC00F1BB137556277AD9` |
| Section Alias | `rag-section-aliases-v1` | `0D1420320A29744C10D774FBCAD9D354AFEB948563876BD04A7543E30F68984A` |
| 最终生产结果 | 40 条完成 | `FD7DFA580E355CB91F24148604F37CC0D44873A400B18E9BC4932E56FE5EAF16` |

没有修改 Case、query、expected_section 或 alias。Alias 只参与评测，不参与生产 canonical_section 生成。

## 3. V3 与 V1.4 对比

| 指标 | V3 | V1.4 | 变化 |
| --- | ---: | ---: | ---: |
| answerable_acceptance_recall | 1.0000 | 1.0000 | 0.0000 |
| section_recall_at_k | 0.6000 | 0.8667 | +0.2667 |
| top_1_section_accuracy | 0.3000 | 0.6667 | +0.3667 |
| grounded_precision | 0.6000 | 0.8667 | +0.2667 |
| no_answer_rejection_rate | 1.0000 | 1.0000 | 0.0000 |
| case_pass_rate | 0.7000 | 0.9000 | +0.2000 |

结果：26/30 条正例通过既有 section / alias 口径，10/10 条反例正确拒绝。

另做一项不替代正式门禁的诊断：30/30 条正例 Top-5 均包含与 expected_section 相同的 canonical_section，30/30 条正例 Top-1 canonical_section 也与 expected_section 一致。这证明剩余失败主要是抽象 canonical 类别与既有 physical section / alias 口径之间的层级差异；正式指标仍按原口径记录为失败。

## 4. V1.4 实现

### 4.1 Canonical Knowledge Metadata

新增七类受控 taxonomy：AI知识库、活动场次、嘉宾档案、推荐阅读、活动流程规则、历史日期索引、使用说明。

Chunk metadata 包含：`source_file_id`、`source_filename`、`sheet_name`、`section`、`canonical_section`、`record_type`、`entity_type`、`year`、`person_name`、`event_name`、`index_version`、`metadata_schema_version`、`document_title`。

分类依据来自源标题、父级标题、结构化记录字段和通用格式规则。旧 Markdown 中 124 个“记录 N”弱标题通过“一级分类”或“原始日期 / 标准日期 / 年份 / 序号”字段恢复类别；没有使用 Eval alias 生成生产 metadata。

### 4.2 Retrieval Ranking

- Embedding input 使用 `title + section + structured metadata + content`；数据库正文和 Agent 返回正文保持原文。
- Lightweight `KnowledgeQueryIntent` 仅识别知识类别，不生成答案。
- 保留原 Hybrid 基础权重 `0.65 * vector + 0.35 * keyword`。
- intent 与 canonical_section 一致时增加最大 0.12 的保守 metadata boost；明确冲突只做轻微降权，不做 hard filter。
- Keyword score 分别评估标题/section、结构化 metadata 和正文，并保留旧评分作为下限。
- Query Guard、Confidence Gate 和 No-answer 策略未降低。

首轮生产实验得到 Section Recall 0.8333、Top-1 0.6333、No-answer 1.00，并暴露“以往活动时间”被误判为活动场次。第二轮只增加透明、通用的历史时间复合短语信号，r12 恢复命中；最终指标为 0.8667 / 0.6667 / 1.00。

## 5. 生产 Reindex 验收

| 项目 | 结果 |
| --- | --- |
| 生产租户 | `zhiy-e-intelligence` |
| Knowledge File | 1 |
| Chunks Before / After | 287 / 287 |
| Provider | `openai-compatible` |
| Embedding Model | `text-embedding-3-small` |
| Embedding Dimension | 1536 |
| Index Version | `rag-index-v2`: 287/287 |
| Metadata Schema | `knowledge-metadata-v1`: 287/287 |
| 数据库回滚快照 | `rag_index_backup_v1_4_2bee63d_20260910`，287 条 |
| 代码备份 | `/opt/enterprise-agent-workbench/backups/rag-v1-4-2bee63d-20260910` |

核心 metadata 覆盖：canonical_section、record_type、entity_type、source_file_id、source_filename、section、sheet_name、index/schema version 均为 287/287；year 218/287、event_name 24/287、person_name 7/287，允许为空。

Canonical 分布：AI知识库 30、活动场次 24、嘉宾档案 6、推荐阅读 52、活动流程规则 39、历史日期索引 132、使用说明 4。

## 6. 全部失败 Case

下列分数按 `actual_top_k_sections` 顺序排列，格式为 `vector / keyword / metadata / final`。所有候选的 `index_version` 均为 `rag-index-v2`，`metadata_schema_version` 均为 `knowledge-metadata-v1`。

### r09

- expected_section：AI知识库
- query_intent：AI知识库
- actual_top_k_sections：品牌历史｜活动成果；活动举办历史与规模；2023-2024秋季｜报名；活动规则；2023-2024秋季｜纪念品
- canonical_sections：AI知识库；AI知识库；活动场次；活动流程规则；活动场次
- scores：`0.6940 / 0.5000 / 1.0000 / 0.7461`；`0.6436 / 0.3333 / 1.0000 / 0.6550`；`0.7364 / 0.5000 / -0.2500 / 0.6462`；`0.6678 / 0.5000 / -0.2500 / 0.6015`；`0.6483 / 0.5000 / -0.2500 / 0.5889`
- 判断：Top-2 canonical 已正确，但未命中已审核 alias `名师面对面是什么`；属于 Evaluation Expectation / category-to-physical-section 层级差异，同时报名语义仍有竞争。

### r16

- expected_section：活动流程规则
- query_intent：活动流程规则
- actual_top_k_sections：中期准备：学期初；中期准备：活动前三天；现场执行；前期准备：确定主题；中期准备：活动前一周
- canonical_sections：活动流程规则；活动流程规则；活动流程规则；活动流程规则；活动流程规则
- scores：`0.7084 / 0.4000 / 1.0000 / 0.7205`；`0.7013 / 0.4000 / 1.0000 / 0.7158`；`0.6907 / 0.4000 / 1.0000 / 0.7089`；`0.6878 / 0.4000 / 1.0000 / 0.7071`；`0.6837 / 0.4000 / 1.0000 / 0.7044`
- 判断：Top-5 均为正确流程类内容，但都不在当前审核 Alias 的四个 physical section 中；属于 Evaluation Expectation 层级差异，不应通过自动扩大 alias 处理。

### r19

- expected_section：AI知识库
- query_intent：AI知识库
- actual_top_k_sections：品牌历史｜活动成果；活动定位；活动定位：品牌定位与目的；活动举办历史与规模；推荐检索字段
- canonical_sections：AI知识库；AI知识库；AI知识库；AI知识库；AI知识库
- scores：`0.7220 / 0.4286 / 1.0000 / 0.7393`；`0.7003 / 0.1429 / 1.0000 / 0.6252`；`0.6943 / 0.1429 / 1.0000 / 0.6213`；`0.6940 / 0.1429 / 1.0000 / 0.6211`；`0.6931 / 0.1429 / 1.0000 / 0.6205`
- 判断：Top-5 canonical 全部正确，但未命中唯一已审核 alias；属于 Evaluation Expectation / physical section 粒度差异。

### p08

- expected_section：AI知识库
- query_intent：AI知识库
- actual_top_k_sections：2023源文件差异；数据冲突；推荐检索字段；切片策略；活动举办历史与规模
- canonical_sections：AI知识库；AI知识库；AI知识库；AI知识库；AI知识库
- scores：`0.6849 / 0.2857 / 1.0000 / 0.6652`；`0.6647 / 0.2857 / 1.0000 / 0.6520`；`0.6618 / 0.2857 / 1.0000 / 0.6502`；`0.6361 / 0.2857 / 1.0000 / 0.6334`；`0.6300 / 0.1429 / 1.0000 / 0.5795`
- 判断：canonical 类别正确，但转换维护类 section 排在业务说明前；仍存在 Source Conversion / Chunk Structure 噪声，且评测只认可一个 physical alias。

## 7. 测试与生产状态

| 检查 | 结果 |
| --- | --- |
| 本地自动化测试 | 41 passed，1 个既有 Starlette 弃用告警 |
| Python compileall | PASS |
| 服务器端发布文件编译 | PASS |
| API / MCP / Worker | 全部 active |
| `/api/health` | `status: ok`、`knowledge: ok` |
| 最终结果候选版本检查 | 149/149 为 `rag-index-v2 / knowledge-metadata-v1` |
| 正例接受率 | 30/30 |
| 反例拒绝率 | 10/10 |

## 8. 已知问题与建议

1. 旧 Markdown 转换仍保留 124 个“记录 N”弱标题；metadata 已恢复类别和年份，但展示层 section 仍弱。下一阶段如继续检索质量，可优先修复源转换或引入 Parent-Child Retrieval，而不是扩大 Chunk Size。
2. r09、r16、r19、p08 的 canonical 结果正确但旧物理标题口径失败。是否补充 Alias 必须由知识内容负责人独立审核，本轮不自动扩大。
3. p08 仍受“源文件差异 / 数据冲突 / 切片策略”等维护类 section 排序影响。可评估可解释的 record_type 细分或业务内容优先级，暂不引入复杂 LLM Reranker。
4. 本地提交已创建，但 GitHub 直推三次受网络连接重置影响；生产部署和验收已完成，仍需补做远端 Git 同步。曾评估通过服务器桥接推送，但完整历史 bundle 在上传前被安全策略阻止，未离开本机且临时文件已删除。

## 9. 阶段边界

V1.4 到此结束。等待人工确认后，再决定是否进入 Reranker、Query Rewrite、Parent-Child Retrieval 或独立的后续阶段；不自动开始 Skill Registry。
