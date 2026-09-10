# Workbench Core V1 Phase A｜阶段 1-2 验收报告

验收日期：2026-09-10  
范围：Phase 1 受控测试租户运维工具、Phase 2 PostgreSQL 迁移执行器。  
结论：**PASS WITH ISSUES**（本地工程验收通过，尚未在生产 PostgreSQL 实施）。

## 已交付

- `scripts/provision_test_tenant.py`
  - 目标租户固定为 `rag-isolation-test`，不接受普通业务租户参数。
  - 默认及 `--dry-run` 均不写入；实际创建必须显式指定 `--execute`。
  - 创建隔离测试租户、测试管理员、最小非业务配置、积分账户、三类 Agent 实例和受控 Runtime 工作目录。
  - 测试密码仅从指定环境变量读取，标准输出不包含密码、Token、密钥或会话凭据。
- `scripts/cleanup_test_tenant.py`
  - 默认及 `--dry-run` 均不删除；实际删除必须显式指定 `--execute`。
  - 仅删除固定测试租户的用户、任务、会话、消息、Trace、知识文件/Chunks、资产、生成记录、积分、配置、Agent 实例与运行目录。
  - 清理前收集由数据库引用的对象存储键，并以 `knowledge/rag-isolation-test/` 前缀枚举复核；清理后验证数据库行、对象键与 Runtime 目录均为零。
  - Runtime 删除路径固定在 `DATA_DIR/runtime/rag-isolation-test`，并额外校验其解析路径仍位于受控 runtime 根目录。
- `scripts/migrate.py`
  - 提供 `status` / `up` 两个命令。
  - 在 `schema_migrations` 保存版本、文件名、SHA-256 checksum 与执行时间。
  - 已执行版本会跳过；历史 SQL checksum 变化或未知历史版本会失败，不会重跑或改写迁移历史。
  - 仅允许 PostgreSQL，拒绝 SQLite 的伪生产执行。
- `app/storage.py`
  - 存储抽象增加受限前缀枚举能力，支持本地目录与 OSS，供测试租户清理后的对象残留核验使用。

## 验证证据

| 检查 | 结果 |
| --- | --- |
| 新增/修改 Python 文件 `compileall` | 通过 |
| `provision_test_tenant.py --dry-run` | 通过；仅输出安全资源标识 |
| `cleanup_test_tenant.py --dry-run` | 通过；本地非 PostgreSQL 环境不进行资源盘点或删除 |
| 全量 `pytest` | 41 passed，1 个现有框架弃用警告 |
| `git diff --check` | 通过 |
| RAG v1.3/v1.4 回归测试 | 包含在全量测试中，通过 |

## 尚未完成 / 风险

- 本地工作区未配置可用于本任务的 PostgreSQL 与 OSS，因此尚未执行真实的 provision / cleanup / migration `up`。生产执行前必须先运行三项 `--dry-run` 并保存脱敏输出。
- 现有 Web 会话与 Runtime MCP Token 为短期无状态签名令牌。清理会删除其用户、租户、配置和 Runtime 目录，使测试租户资源访问失效；但系统尚未提供独立的 Token 撤销表。因此“Token 密码学上立即不可验证”不能在本阶段宣称完成，生产验收需以删除后 API/MCP 实测拒绝作为证据。
- 本阶段没有修改 embedding、切分、检索、重排、Query Guard、Alias 或任何 RAG 策略。

## 下一步

1. 在目标生产主机以发布环境执行 migration `status`，确认基线对账状态。
2. 运行受控测试租户 provision / cleanup 的真实闭环，并保存脱敏资源清单。
3. 继续实施干净 release 构建、配置指纹校验、受控切换与回滚机制，再进入租户隔离及浏览器 E2E 门禁。
