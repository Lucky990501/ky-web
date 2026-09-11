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

### 尚缺证据

当前截图没有覆盖同一浏览器会话内的“进入 Agent → 针对刚上传文件提问 → 页面显示 Final Response”，也没有把该页面结果与 `task_id`、`run_id`、`trace_id` 同屏或可审计关联。2026-09-11 再次尝试接管 Codex 内置浏览器时，控制桥在首次、重试及重置后均返回 `nodeRepl.fetch request failed`，无法安全操作或截图。

这不是后端 Upload、Processing、Retrieval 或 Agent Runtime 失败，但不满足文档规定的 Browser Gate 证据完整性。

Stage 7 结论：**BLOCKED（仅缺 Browser → Agent UI 最终证据）**。

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

Tenant Isolation 与三个 Agent Grounding 均通过；Browser Upload → Process → Retrieval 已通过，但 Browser → Agent UI 最终证据仍缺失。因此本组阶段总判定为 **BLOCKED**，不会据此提前宣布 Workbench Core V1 STABLE。
