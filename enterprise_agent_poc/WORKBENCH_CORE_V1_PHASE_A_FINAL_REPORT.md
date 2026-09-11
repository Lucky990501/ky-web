# Workbench Core V1｜Phase A 最终生产验收报告

验收日期：2026-09-11

最终结论：**PASS WITH ISSUES**

## Release Identity

| 项目 | 值 |
| --- | --- |
| release_id | `20260911-59d67ac` |
| git_commit | `59d67ac891a5aca57f1d2f41d9d0cf6cd0335bab` |
| migration_version | `001_workbench_v1.sql` 至 `004_knowledge_v1.sql`，pending = 0 |
| runtime_version | `openai-codex==0.147.0` |
| archive_sha256 | `69434ba7bea320acfabfaaecb569082801856e02177a5e57d7c23a10ac73da9c` |

生产 `release-current`、API、Platform MCP 与 Worker 均指向并运行上述 Release；`/api/health` 正常，Shared Environment 与 Config Fingerprint 检查通过。

## Tenant Isolation

**PASS**。

- Tenant A `zhiy-e-intelligence` 与受控创建的 Tenant B `rag-isolation-test` 分别上传并检索不同测试知识；
- Knowledge File API、Retrieval 与 pgvector 查询仅返回当前 Tenant；
- OSS 对象使用 Tenant 前缀隔离；
- A/B Session 交叉读取 knowledge file 均为 `404`；
- Platform MCP token、Runtime Bearer token、scope、conversation 与 codex thread 的跨 Tenant 攻击均被拒绝或无结果；
- 请求或模型自行提供 `tenant_id` 不能覆盖服务端认证主体。

## Browser E2E

**PASS**。

浏览器截图覆盖 Login → Knowledge → Upload → Processing → ready → Retrieval Test；A/B 页面分别显示上传文件及专属检索正文。Tenant A 随后从浏览器进入 Copywriting Agent，对刚上传的知识提问并获得 grounded Final Response。

最终 Browser Agent 证据：file ID `39b783fa-c89a-4977-b770-dff2da97770f`，task ID `2552cf82-1cfc-4625-a9a1-9398d121fb8a`，run ID / Trace 主键 `d8db1dec-4545-487d-8fb3-7b4f4fddd01c`，conversation ID `3be43303-ce22-4ba1-92f4-1e2be1461b38`，Codex thread ID `01a08e52-4dda-7ff2-8ef7-215b34a360a7`。生产 Trace 为 completed，`enterprise_config_get`、`asset_search`、`knowledge_search` 全部完成，`tool_calls_completed=true`、`final_response_received=true`；检索输出指向 `a测试.txt`，评分 `0.7342`。截图 SHA-256 为 `BF7E3A22337C64DBC462785DB4B63C8579E38782CBC28889D99D25C7146EABC5`。

## Agent Grounding

| Agent | Task / Run | 结果 |
| --- | --- | --- |
| Copywriting | `bee88b41-3663-455a-8a55-d4441d144a90` / `9f9c6328-b793-4717-8298-7edf8edc4d9a` | PASS |
| Campaign | `e439d165-0a18-4c50-a61b-64d8c2fbcb4f` / `1fe80094-8609-446d-ac5d-13f9d67fe3f5` | PASS |
| Image | `f919283f-6f7b-45f3-b53b-3726ba594bde` / `65f9415b-4937-48c4-a602-71ba4753609b` | PASS |

三个 Agent 均满足依赖式 Grounding Gate：企业规则在使用前读取，企业事实在 Final Response 前检索，图片必需知识/素材在 `image_generation` 前完成；`required_tool_calls_completed=true`、`final_response_received=true`。图片 Agent 已生成真实 artifact。

## No-answer / Hallucination Regression

**PASS**。

Copywriting、Campaign、Image 分别对不存在价格、不存在课程、不存在人物、未公开企业信息、管理员凭据及无依据绝对承诺进行生产回归。三个 Agent 均明确拒绝或说明资料不足，没有虚构企业事实；Query Guard、Confidence Gate 与 RAG V1.4 策略未调整。

## Thread Resume

**PASS**。

- 三个 Agent 均完成 Turn 1、Turn 2、Worker Runtime Restart、Resume 与 Turn 3；
- tenant、user、agent、conversation、runtime profile、runtime version 与 skill manifest 绑定正确；
- Copywriting 与 Campaign 直接恢复原 thread；Image 在旧 metadata 冲突时使用只重放用户可见消息的受控重建；
- Image thread → Copywriting、Copywriting thread → Campaign 均返回 HTTP `409`，没有跨 Agent Resume 或任务创建。

## Cleanup

**PASS**。

`cleanup_test_tenant.py --dry-run` 核对后执行 `--execute`。Tenant B 最终 database rows = 0、OSS objects = 0、runtime directories = 0。清理前保存的旧 Session 在清理后请求返回 HTTP `401`，旧 MCP Credential 被拒绝；仅记录实际拒绝，不宣称无状态 Token 被密码学即时撤销。

## Known Issues

1. Run Trace 的结构化 `knowledge_retrievals` 解析不了当前 MCP content wrapper，因而该字段出现 `result_count=null` / 空 results；同一 Trace 的 `knowledge_search.output_summary`、file ID、评分和最终页面已提供完整验收证据。建议后续单独修复可观测性解析，不调整 RAG V1.4。
2. Codex 内置浏览器控制桥曾返回 `nodeRepl.fetch request failed`；本次最后一段由人工浏览器执行并保留脱敏截图，不影响产品链路结果。
3. 生产安装仍复用受控 venv，因为当前镜像源没有 `openai-codex==0.147.0`；发布脚本会校验依赖与 `pip check`，后续仍建议建立内部 wheelhouse。
4. “已有 previous release 时的故障注入 rollback”尚未在真实生产数据环境刻意演练；现有自动恢复与基础 rollback 已验证。

## Final Gate

| 核心门禁 | 结果 |
| --- | --- |
| Tenant A/B Isolation | PASS |
| Browser Knowledge E2E | PASS |
| 3 Agent Grounding | PASS |
| No-answer Regression | PASS |
| Thread Resume | PASS |
| Cleanup | PASS |

全部核心门禁已经通过。由于仍存在不阻断产品使用的可观测性、依赖供应与深度 rollback 演练事项，本报告最终结论为 **PASS WITH ISSUES**。

- Enterprise Knowledge: **STABLE**
- RAG Retrieval V1.4: **FROZEN**
- Workbench Core V1: **STABLE**
- Next Phase: **Skill Registry V1**
