# RAG 生产部署与配置一致性验收报告

验收日期：2026-09-10（Asia/Shanghai）<br>
范围：RAG 安全策略、Trace 脱敏观测、配置一致性诊断的生产部署

## 1. 发布版本

| 项目 | 结果 |
| --- | --- |
| 发布提交 | `1eb2473`、`501b63a` |
| Git 远端 | `origin/master` 已确认到达 `501b63a` |
| 发布方式 | 保留生产既有工作树，仅叠加经远端逐文件比对的 RAG 增量 |
| 回滚备份 | `/opt/enterprise-agent-workbench/backups/rag-501b63a-20260910` |

未覆盖生产中无关的 Agent、产品模块和历史未提交文件。

## 2. 部署内容

- Query 拒答策略：绝对化承诺、显式不存在实体直接拒答；价格类请求必须有价格证据。
- RAG 置信度策略与 Platform MCP 的异步检索调用。
- RAG Trace 的脱敏检索观测：仅保存 query SHA-256、长度、Chunk/File ID、分数和接受状态，不保存 query、Chunk 正文或隐藏推理。
- 运行配置指纹与 `scripts/verify_runtime_config.py`。
- 固定 40 条评测集、人工审核章节别名模板和本地回归测试。

## 3. 验收证据

| 检查 | 结果 |
| --- | --- |
| 干净 Git worktree 回归 | `27 passed, 1 warning` |
| Platform MCP 服务 | `active` |
| API 服务 | `active` |
| Worker 服务 | `active` |
| `/api/health` | `status: ok`、`knowledge: ok` |
| API 进程与 `.env.production` 的脱敏比对 | `matches: true`，无不一致变量 |

## 4. 结论

**部署与配置一致性验收：PASS WITH ISSUES。**

本次发布已成功运行，且此前发现的服务进程配置与 `.env.production` 配置漂移已消除。发布过程建立了持久回滚备份；临时上传和空评测文件已经清理。

## 5. 未完成项与阶段限制

1. 40 条生产 RAG 复评尚未得到有效结果。此前 SSH/PowerShell 包装器未能可靠启动评测；新增 `scripts/run_rag_v1_3_production.sh` 作为固定工作目录、固定环境文件和指定 venv 的直接入口，待部署后复验。
2. 小节别名模板尚未由知识内容负责人审核，因此不能用未审核映射宣称正例 Grounding 通过。
3. 临时 Tenant B 隔离、浏览器上传至 OSS/Redis/Worker/pgvector/Codex 的完整 E2E、Token/成本验收仍未完成。
4. 生产仓库仍含历史未提交改动；本次仅建立可回滚的增量发布，不将其表述为可复现的完整 release。

因此 Enterprise Knowledge Phase A 仍为 **BLOCKED**，不得开始 Skill Registry、Agent Expansion 或后续阶段。

## 6. 下一阶段验收输入

下一阶段开始前应由有权限的运维终端执行受控的只读评测，并保存脱敏结果；随后新增一份 `RAG_PRODUCTION_REEVALUATION_ACCEPTANCE_YYYY-MM-DD.md`，记录 40 条指标、章节映射审核结论、负例拒答率和最终阶段决策。
