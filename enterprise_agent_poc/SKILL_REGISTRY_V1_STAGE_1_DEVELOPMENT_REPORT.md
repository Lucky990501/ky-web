# Skill Registry V1｜Stage 1 本地开发验收

验收日期：2026-09-11

验收环境：本地开发环境，尚未构建或部署新生产 Release。

开发 commit：`f251e12584911c90288b12649b44320e0098ac2b`

## 已实现

### Registry 与 Migration

- 新增 PostgreSQL migration `005_skill_registry_v1.sql`；
- 建立 `skills`、`skill_versions`、`skill_packages`、`agent_skill_bindings`；
- 增加独立 `platform_admins` 权限表；
- 内置 5 个原生 Skill 包自动登记为 published，并按现有三个 Agent Manifest 建立初始绑定。

### Native Skill Import

- 保存原始 ZIP，不把 Skill 转换成平台私有格式；
- 保留 `SKILL.md`、`agents/`、`references/`、`scripts/`、`assets/`；
- 校验 slug 根目录、SemVer、UTF-8 `SKILL.md`、包大小和文件数量；
- 拒绝路径穿越、反斜杠路径、大小写路径碰撞、符号链接、特殊文件、加密条目及异常文件名。

### 版本状态机

- 新导入版本为 draft；
- 测试通过后可发布为 published；
- published / deprecated 版本不能通过导入覆盖；
- 仍绑定 Agent 的版本不能废弃；
- Agent 可切换至另一个 published 版本，实现 upgrade / rollback。

### Agent 与 Runtime 同步

- Agent Service 从 Registry 解析当前 Skill Manifest；
- Manifest 继续参与 Runtime Profile ID；
- Runtime 部署器只复制 Manifest 中明确绑定的 Skill；
- 部署器增加 Manifest 路径边界检查，拒绝目录逃逸；
- 图片、文案、活动策划三个 Agent 的内置 Skill 均完成 discovery 与隔离部署测试。

### Platform Admin

- 新增 `/platform/skills` 页面；
- 支持上传 ZIP、版本历史、包测试、发布、Agent 绑定/切换、回滚和废弃；
- 普通企业管理员访问 Registry API 返回 `403`，导航中不显示 Skill 管理；
- 新增 `grant_platform_admin.py`，默认 dry-run，只有显式 `--execute` 才授权。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| Python compileall | PASS |
| JavaScript `node --check` | PASS |
| 完整 pytest | `52 passed` |
| 明确 commit 干净归档 pytest | `52 passed` |
| Native ZIP 原样保留 | PASS |
| ZIP 路径攻击拒绝 | PASS |
| published 不可覆盖 | PASS |
| upgrade / rollback | PASS |
| 三 Agent 精确 Skill Discovery | PASS |
| 企业管理员权限拒绝 | PASS |
| 平台管理员导入 API | PASS |

唯一测试警告为 FastAPI TestClient 使用旧 httpx 接口的既有弃用提示，不影响本阶段结果。

## 未完成

1. 尚未在生产 PostgreSQL 执行 migration `005`。
2. 尚未指定并受控授权生产平台管理员。
3. 尚未通过真实浏览器上传三个 Skill ZIP。
4. 尚未完成生产 Publish → Bind → Runtime Sync → Codex Discovery → 真实 Turn。
5. 尚未生成最终 `SKILL_REGISTRY_V1_REPORT.md`。

## 结论

**PASS（本地开发门禁）**。可以进入 Stage 2 集成验收与 Release 候选构建；当前不得宣称 Skill Registry V1 已在生产完成。
