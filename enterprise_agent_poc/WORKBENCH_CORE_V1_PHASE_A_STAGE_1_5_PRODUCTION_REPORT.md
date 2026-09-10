# Workbench Core V1｜Phase A Stage 1-5 生产执行验收报告

验收日期：2026-09-10  
结论：**PASS WITH ISSUES**

## Git 状态与 Release Identity

| 项目 | 验收结果 |
| --- | --- |
| local / origin commit | `f63e8b4cf8c72588dc2fee447fbe8dc47fe7300d` |
| 生产旧工作树 commit | `a2a66096ba8cdf7168dc3c8e72fc8cff745891b7`（保留、未覆盖） |
| 正式 Release ID | `20260910-f63e8b4` |
| Release Git commit | `f63e8b4cf8c72588dc2fee447fbe8dc47fe7300d` |
| Archive SHA-256 | `8cad9676c5f1b8c4e32c9dfd7ee7e470e124c00a4f8faa8e672f397651973cca` |
| 文件数 | 59 |
| Release path | `/opt/enterprise-agent-workbench/releases/20260910-f63e8b4/enterprise_agent_poc` |

`release-current` 已解析至该 Release path；目录中的 `release-manifest.json` 记录 release ID、commit 与 archive SHA-256。GitHub 曾断开，恢复 Clash Verge `7897` 代理后，最终 commit 已推送至 `origin/master`。旧生产脏工作树未用作发布来源。

## Migration Status

生产 PostgreSQL 已建立 `schema_migrations` baseline。`001_workbench_v1.sql`、`002_user_profiles.sql`、`003_agent_templates.sql`、`004_knowledge_v1.sql` 均为 `applied`，`unknown_history_versions=[]`；最终 `migration up` 返回 `applied=[]`，即 pending=0。

## Tenant 工具生产 Dry Run

| 工具 | 结果 |
| --- | --- |
| Provision | 通过；目标固定 `rag-isolation-test`，仅输出安全资源标识 |
| Cleanup | 通过；数据库资源=0、对象键=0、runtime 目录=0 |
| 秘密输出 | 未输出 Password、Token、API Key 或 Session Credential |

本阶段没有创建 Tenant B。

## 配置与发布隔离

| 项目 | 结果 |
| --- | --- |
| Shared config | `/opt/enterprise-agent-workbench/shared/enterprise-agent.env`，`600 root:root` |
| Release 归档 | 不含 `.env.production`、密钥或运行数据 |
| Config fingerprint | `5958f0ed7b94d9432d90f22b2432dd88229054c6cc84b9c5197fcc3c7f9648b1` |
| Fingerprint match | `true` |
| systemd EnvironmentFiles | 三项服务均仅引用 shared environment file |
| systemd WorkingDirectory | 三项服务均为新 release 路径 |

## Service Acceptance

| 项目 | 结果 |
| --- | --- |
| API | active |
| Platform MCP | active |
| Worker | active |
| `/api/health` | `status=ok`、`knowledge=ok`、`environment=production` |
| Runtime | `openai-codex==0.147.0` |

## Rollback 状态

- 当前 release：`20260910-f63e8b4`；上一 release `20260910-901ee3c` 仍存在。
- 已实际验证三类切换前失败自动恢复旧服务：依赖镜像缺少 `openai-codex`、release import 指向旧包、health probe 启动竞态。每次恢复后三服务均为 active。
- 未在已有 previous release 的状态下刻意注入故障，避免影响真实生产数据；rollback 属于有限深度验证。

## 遗留问题与下一阶段

1. 受控 venv 被复用，因为镜像源没有 `openai-codex==0.147.0`；脚本会验证该依赖和 `pip check`。后续应建立包含该版本的内部 wheelhouse。
2. 应在低风险窗口补做“有 previous release 时”的故障注入 rollback 演练。
3. Stage 6 Tenant A/B、浏览器 E2E、三 Agent Grounding、No-answer、Thread Resume 尚未执行。

Stage 1-5 无核心 BLOCKED，可进入 Stage 6；仍禁止启动 Skill Registry、Agent Expansion、RAG 优化、新 Agent、GraphRAG 或 Multi-Agent。
