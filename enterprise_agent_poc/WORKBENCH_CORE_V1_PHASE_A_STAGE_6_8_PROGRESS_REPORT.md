# Workbench Core V1｜Phase A Stage 6–8 生产验收报告

验收日期：2026-09-11

生产 Release：`20260911-59d67ac`

生产代码 commit：`59d67ac891a5aca57f1d2f41d9d0cf6cd0335bab`

## Stage 6｜Tenant A/B 真实生产隔离

Tenant A 为 `zhiy-e-intelligence`；Tenant B 为通过受控工具 `provision_test_tenant.py --execute` 创建的 `rag-isolation-test`。测试期间没有手工插入数据库、替换 Tenant ID 或借用普通生产 Tenant。

### Knowledge 与浏览器检索

| Tenant | 文件 | 查询 | 结果 |
| --- | --- | --- | --- |
| A | `a测试.txt`（file ID `39b783fa-c89a-4977-b770-dff2da97770f`） | `名师面对面成长营活动形式` | 仅命中 A 的教育活动内容，首条评分 `0.7516` |
| B | `B测试.txt`（file ID `b4557050-51cb-4cf9-87e2-39a4389bcb1e`） | `星云协作台核心能力` | 仅命中 B 的虚构企业产品内容，评分 `0.6141` |

浏览器截图记录了文件从“等待处理”到可检索的产品路径，并分别显示 A/B 专属正文。

### 数据层与攻击性隔离

| 检查 | 生产结果 |
| --- | --- |
| A Session 请求 B knowledge file | `404` |
| B Session 请求 A knowledge file | `404` |
| pgvector Tenant filter | A 共 288 chunks、B 共 1 chunk；两侧查询均只有一个 `tenant_id` |
| OSS / object storage prefix | A/B 对象分别位于 `knowledge/zhiy-e-intelligence/` 与 `knowledge/rag-isolation-test/` |
| Platform MCP knowledge_search | A token 不返回 B 标记；B token 不返回 A 标记 |
| MCP Scope | 错误 scope 被拒绝 |
| Runtime Bearer Token | 篡改签名被拒绝 |
| 模型自行提供 tenant_id | 服务端身份绑定优先，不能改变真实 Tenant |
| 跨 Tenant conversation_id | 查找结果为 0 / 拒绝 |
| 跨 Tenant codex_thread_id | 不允许按请求参数跨 Tenant 复用 |
| Agent 最终响应 | Grounding Trace 所用知识调用保持 Tenant 绑定 |

Stage 6 结论：**PASS**。

## Stage 7｜Browser Knowledge E2E

### 已通过链路

- 浏览器登录后进入企业知识库；
- UI 上传 `a测试.txt` / `B测试.txt`；
- 文件经历等待处理并最终可用；
- Worker 完成解析、切分、Embedding 与 pgvector 索引；
- 浏览器 Retrieval Test 返回对应 Tenant 的文件正文；
- 生产 Agent 的 `knowledge_search` 与 Final Response 已分别在 Stage 8 Trace 中验证。

### Browser → Agent 最终证据

2026-09-11 10:36，用户在 Tenant A 的“继续文案创作智能体”页面提交：`请仅依据企业知识库说明“名师面对面成长营”的活动形式；资料不足请明确说明。` 页面最终返回“数学学习规划讲座、现场答疑、学习方法诊断”，服务对象为“初中阶段学生及家长”，并主动区分“名师面对面”与“名师面对面成长营”，对知识库未提供的信息明确不补充。

| 证据字段 | 值 |
| --- | --- |
| screenshot | `codex-clipboard-8376c3ee-8b08-42d5-84c6-46437af2534c.png` |
| screenshot SHA-256 | `BF7E3A22337C64DBC462785DB4B63C8579E38782CBC28889D99D25C7146EABC5` |
| file_id | `39b783fa-c89a-4977-b770-dff2da97770f`（`a测试.txt`） |
| task_id | `2552cf82-1cfc-4625-a9a1-9398d121fb8a` |
| run_id / trace primary key | `d8db1dec-4545-487d-8fb3-7b4f4fddd01c` |
| conversation_id | `3be43303-ce22-4ba1-92f4-1e2be1461b38` |
| codex_thread_id | `01a08e52-4dda-7ff2-8ef7-215b34a360a7` |

生产 Trace 状态为 completed，`enterprise_config_get`、`asset_search`、`knowledge_search` 均 completed，`tool_calls_completed=true`、`final_response_received=true`；`knowledge_search` 的持久化输出指向上述 Tenant A 文件，评分 `0.7342`。当前 schema 没有独立 `trace_id` 列，`run_traces.run_id` 是 Trace 主键，报告按真实数据模型记录该映射。

Stage 7 结论：**PASS**。

## Stage 8｜三个 Agent Grounding

验收采用依赖约束，不要求 `knowledge_search` 与 `asset_search` 固定先后顺序。

| Agent | Task | Run | 必需工具与最终响应 | 结论 |
| --- | --- | --- | --- | --- |
| 文案 | `bee88b41-3663-455a-8a55-d4441d144a90` | `9f9c6328-b793-4717-8298-7edf8edc4d9a` | config、asset、knowledge 均成功；Final Response grounded | PASS |
| 活动策划 | `e439d165-0a18-4c50-a61b-64d8c2fbcb4f` | `1fe80094-8609-446d-ac5d-13f9d67fe3f5` | config、asset、knowledge 均成功；最终正文已返回 | PASS |
| 图片 | `f919283f-6f7b-45f3-b53b-3726ba594bde` | `65f9415b-4937-48c4-a602-71ba4753609b` | config、knowledge、asset、image_generation 均成功；Final Response 与 artifact 已生成 | PASS |

图片 Agent 的 `starting_runtime` 阻塞已经修复并由独立 P0 报告记录。所有必需企业规则、知识和素材均在相关输出使用前完成，三个 Agent 均满足 `required_tool_calls_completed=true` 与 `final_response_received=true`。

Stage 8 结论：**PASS**。

## Stage 6–8 总结

Tenant Isolation、Browser Upload → Process → Retrieval → Agent Final Response 与三个 Agent Grounding 均通过。本组阶段总判定为 **PASS**。
