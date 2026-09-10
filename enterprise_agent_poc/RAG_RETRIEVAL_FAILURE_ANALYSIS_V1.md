# RAG Retrieval Failure Analysis V1

分析日期：2026-09-10

依据：`rag-v1.3`、`rag-section-aliases-v1` 的 V3 生产复评。仅分析正例检索失败，不调整评测问题、expected_section 或 alias。

## 数量校正

V1.4 任务文档写为“13/30 正例未命中”，但 V3 聚合指标 `section_recall_at_k=0.60` 和生产失败列表共同确认：30 条正例中 18 条命中、**12 条未命中**。下表完整覆盖这 12 条；该校正不涉及修改评测数据。

## 分类定义

- A：Vector semantic ranking
- B：Keyword ranking
- C：Chunk structure
- D：Missing metadata
- E：Knowledge coverage
- F：Source conversion / parsing
- G：Evaluation expectation

## 逐条分类

| case_id | expected_section | 主要分类 | 证据与判断 |
| --- | --- | --- | --- |
| r05 | 活动流程规则 | A、B、D | Top-K 全为活动时间、名称、主线、报名和回顾；“活动”公共词贡献相同 keyword 分，缺少 workflow 类型与 canonical_section 帮助区分流程。 |
| r08 | 嘉宾档案 | B、C、D | Top-K 为“确定嘉宾”和执行流程，没有人物简介；“嘉宾”命中流程标题，但 Chunk 缺少 person/entity metadata，无法把人物档案提升。 |
| r09 | AI知识库 | B、D、G | “报名相关资料”检索到报名、活动成果和活动规则在业务上可解释，但与抽象 expected_section 不一致；需保留 G 风险，同时不能因此修改 expected_section。 |
| r12 | 历史日期索引 | A、B、D | 历史时间查询被当前活动时间和准备时间占据；缺少 year、event 类型和历史索引 canonical metadata。 |
| r13 | 推荐阅读 | A、B、D | Top-K 为活动成果、激励、纪念品、回顾和活动名称；阅读/书目结构信号未进入 metadata 或独立关键词评分。 |
| r15 | 嘉宾档案 | A、B、D | 嘉宾介绍查询被现场/前期流程占据；人物简介未进入 Top-K，person_name/entity_type 缺失。 |
| r18 | 使用说明 | A、B、D、G | “活动资料使用规则”同时具有使用说明和活动规则歧义；Top-K 偏向成果、纪念品、导读、证书和归档，缺少 usage canonical metadata。 |
| r19 | AI知识库 | A、D、G | 企业知识库活动问答检索到成果、回顾、用途和活动主线；“用途”具一定合理性但不在 Top-1，抽象 AI知识库分类与具体内容标题存在层级差。 |
| p01 | 活动场次 | A、D | 排期改写检索到具体活动主题/人物日期标题；这些记录按 V1.4 设计应具有 event/year metadata，并归入活动场次，而不是依赖标题 alias。 |
| p03 | 嘉宾档案 | A、B、D、E | 讲师人选改写只保留一个“活动主线”结果；既缺人物 metadata boost，也可能存在候选召回不足。 |
| p06 | 历史日期索引 | C、D、F | Top-K 均为“记录 87/114/14/118/99”；弱标题直接证明结构化源记录转换后字段名、年份和记录类别未进入标题或 metadata。 |
| p08 | AI知识库 | A、C、D、F | Top-K 包含“源文件差异”“旧版XLS说明”等转换/维护类标题；结构噪声进入检索，而知识用途和活动主线未被 canonical metadata 提升。 |

## 根因汇总

1. **Metadata 缺失是共性根因**：当前 Chunk metadata 仅有 `source=enterprise_file`，没有 canonical_section、record_type、entity_type、year、person_name、event_name、sheet_name 等字段。
2. **Keyword 过于粗糙**：当前按查询的 1–2 字中文片段做正文子串命中比例，“活动”“资料”“嘉宾”等公共词容易把流程或通用记录推高。
3. **Embedding 上下文不足**：当前向量输入只有 `chunk.content`，标题、section 和结构化 metadata 没有参与 Embedding。
4. **结构化转换存在可见损失**：原始 XLSX 经 Markdown 上传；“记录 N”类标题证明部分工作表/字段/父标题语义未被可靠保存。
5. **少数评测意图存在层级歧义**：r09、r18、r19 的实际结果具有一定业务相关性，但本轮禁止修改 expected_section，应通过稳定 taxonomy 与 metadata 解决或保留为评测风险。

## V1.4 实施方向

- 建立独立于 Eval alias 的 canonical knowledge taxonomy 和轻量 `KnowledgeQueryIntent`。
- 将结构化 metadata 作为 JSON 写入每个 Chunk；不把企业专有映射硬编码进数据库查询。
- 使用 `title + section + structured metadata + content` 生成 `rag-index-v2` Embedding，返回正文仍保持原文。
- 先以 metadata boost 改善排序，不做 canonical_section hard filter。
- Reindex 前建立可恢复备份，并保留旧索引版本证据。
