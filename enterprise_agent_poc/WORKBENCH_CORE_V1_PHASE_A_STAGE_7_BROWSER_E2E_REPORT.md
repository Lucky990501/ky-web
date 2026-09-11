# Workbench Core V1｜Phase A Stage 7 Browser Knowledge E2E 验收报告

验收日期：2026-09-11

生产 Release：`20260911-59d67ac`

## 浏览器产品链路

已通过真实生产站点完成：

`Browser Login → Knowledge → Upload → OSS → Worker → Parse → Chunk → Embedding → pgvector → ready → Retrieval Test → Agent → knowledge_search → Final Response`

前序浏览器证据记录 Tenant A 的 `a测试.txt` 与 Tenant B 的 `B测试.txt` 从上传、等待处理到可检索，并显示两个 Tenant 的专属检索正文。Tenant B 已在完成隔离测试后按 Stage 11 受控清理。

## Browser → Agent 最终证据

2026-09-11 10:36，Tenant A 用户在“继续文案创作智能体”页面提交：

`请仅依据企业知识库说明“名师面对面成长营”的活动形式；资料不足请明确说明。`

页面返回内容包括：

- 数学学习规划讲座；
- 现场答疑；
- 学习方法诊断；
- 服务对象为初中阶段学生及家长；
- 主动说明“名师面对面”与“名师面对面成长营”并非同一活动；
- 对课程、师资、价格、名额、时间地点和联系方式等知识库未提供信息明确不补充。

这与 `a测试.txt` 的 Tenant A 专属内容一致，没有混入 Tenant B 知识或虚构缺失信息。

## 证据标识

| 字段 | 值 |
| --- | --- |
| screenshot | `codex-clipboard-8376c3ee-8b08-42d5-84c6-46437af2534c.png` |
| screenshot size | 98,027 bytes |
| screenshot SHA-256 | `BF7E3A22337C64DBC462785DB4B63C8579E38782CBC28889D99D25C7146EABC5` |
| file_id | `39b783fa-c89a-4977-b770-dff2da97770f` |
| task_id | `2552cf82-1cfc-4625-a9a1-9398d121fb8a` |
| run_id | `d8db1dec-4545-487d-8fb3-7b4f4fddd01c` |
| trace_id | 当前 schema 以 `run_traces.run_id` 作为 Trace 主键，即 `d8db1dec-4545-487d-8fb3-7b4f4fddd01c` |
| conversation_id | `3be43303-ce22-4ba1-92f4-1e2be1461b38` |
| codex_thread_id | `01a08e52-4dda-7ff2-8ef7-215b34a360a7` |

## 生产 Trace

| 检查 | 结果 |
| --- | --- |
| task status | completed |
| trace status | completed |
| enterprise_config_get | completed |
| asset_search | completed |
| knowledge_search | completed |
| tool_calls_completed | true |
| final_response_received | true |
| final response length | 209 |
| knowledge file | `a测试.txt` / `39b783fa-c89a-4977-b770-dff2da97770f` |
| retrieval score | `0.7342` |

Trace 的结构化 `knowledge_retrievals` 当前无法解析 MCP content wrapper，表现为 `result_count=null`、results 为空；但同一条 Trace 的 `knowledge_search.output_summary` 明确记录上述 file ID、文件名、正文摘要、评分及检索返回，且浏览器最终回答与该内容一致。该问题记为非阻断的可观测性缺陷，不修改 RAG V1.4。

## 结论

**PASS**。真实浏览器上传的知识已通过正式处理链路进入检索，并被生产 Agent 的 `knowledge_search` 使用后形成 grounded Final Response。
