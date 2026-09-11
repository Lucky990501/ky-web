# Workbench Core V1｜Phase A Stage 11 Tenant B Cleanup 验收报告

验收日期：2026-09-11

生产 Release：`20260911-59d67ac`

Git commit：`59d67ac891a5aca57f1d2f41d9d0cf6cd0335bab`

## 清理对象

测试 Tenant：`rag-isolation-test`。执行前先运行 `cleanup_test_tenant.py --dry-run`，核对目标后再运行 `cleanup_test_tenant.py --execute`。未手工删除数据库记录，未清理普通生产 Tenant。

## Dry Run

| 资源 | 待清理数量 |
| --- | ---: |
| tenants | 1 |
| users | 1 |
| agent_instances | 3 |
| enterprise_config | 1 |
| credit_accounts | 1 |
| knowledge_bases | 1 |
| knowledge_files | 1 |
| knowledge_chunks / vectors | 1 |
| storage objects | 1 |
| runtime directories | 2 |
| documents / assets / generations / tasks / conversations / traces | 0 |

Dry Run 同时确认 `active_temporary_credentials=0`。首次执行审计发现知识库外键删除顺序错误，已修正为先删除 asset metadata 及知识子资源，再删除 knowledge base；完整测试通过后发布 `59d67ac`，再从头执行 Dry Run 与 Execute。

## Execute 与 Cleanup Verify

受控清理执行成功，最终校验结果：

| 检查 | 结果 |
| --- | --- |
| database resources | 全部为 0 |
| OSS / storage objects | 0 |
| runtime directories | 0 |
| runtime root | `/opt/enterprise-agent-workbench/shared/runtime-data/runtime/rag-isolation-test` 不存在 |
| cleanup status | `clean` |

数据库清理覆盖 users、sessions、conversations、messages、tasks、runs、traces、knowledge files、chunks/vectors、assets、generations、credits、agent instances与 Tenant 配置/主体。对象存储前缀和 Tenant Runtime 目录同步清理。

## 清理后凭据访问

在清理前保留一份仅用于本次验收、未写入报告的 Tenant B Session 和 MCP Credential；清理后实际重放：

| 凭据 | 清理后结果 |
| --- | --- |
| 原 Tenant B Session | HTTP `401` |
| 原 Tenant B MCP Credential | rejected |

系统现在会在校验签名与 scope 后继续核对服务端 subject / Tenant 是否仍存在。这里仅证明资源删除后实际访问被拒绝；由于凭据是短期无状态签名 Token，本报告不宣称其在密码学意义上被即时撤销。

## 验证

- 本地完整测试：`46 passed`，仅 1 条 FastAPI TestClient 弃用警告；
- 生产 API、Platform MCP、Worker 均 active；
- `/api/health` 返回正常；
- PostgreSQL migration pending = 0；
- RAG Retrieval V1.4 未修改。

## 结论

**PASS**。Tenant B 的数据库、对象存储与 Runtime 资源均已清零，旧 Session 与 MCP Credential 的实际请求均被拒绝。
