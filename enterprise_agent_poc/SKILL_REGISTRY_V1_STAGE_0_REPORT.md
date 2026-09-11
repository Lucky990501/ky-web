# Skill Registry V1｜Stage 0 范围与基线验收

验收日期：2026-09-11

## Git 与生产基线

| 项目 | 值 |
| --- | --- |
| 当前生产 Release | `20260911-59d67ac` |
| 生产代码 commit | `59d67ac891a5aca57f1d2f41d9d0cf6cd0335bab` |
| Stage 0 开始时 origin/master | `da106d52015cfedd57557f95886768007e2cc2a1` |

`59d67ac..origin/master` 之间只有三个验收文档 commit，文件差异共 5 个 Markdown 文件：`docs/PROJECT_CONTEXT.md` 和四份 Phase A 报告。没有 Python、JavaScript、CSS、Migration、配置或依赖差异。

结论：无需为了同步文档 commit 重新部署生产，可以直接从最新 `master` 开始 Skill Registry V1 开发。

## 冻结边界

本阶段不修改：

- RAG Retrieval V1.4；
- Enterprise Knowledge 核心检索策略；
- Tenant Isolation 与服务端 Tenant 身份边界；
- Codex Runtime Provider / App Server Core。

## V1 范围

1. 原生 Codex Skill ZIP 原样导入，不转换 `SKILL.md` 或包目录。
2. 建立 `skills`、`skill_versions`、`skill_packages`、`agent_skill_bindings`。
3. 支持 draft、published、deprecated；published 版本不可覆盖。
4. 平台管理员可查看、测试、发布、绑定、切换、回滚和废弃版本。
5. Agent Template → Skill Binding → Runtime Profile → Skill Manifest → `CODEX_HOME/skills`。
6. Runtime 只同步当前 Agent 绑定的 Skill，禁止将所有 Skill 部署到所有 Runtime。
7. 使用图片、文案、活动策划三个现有 Skill 族完成真实验收。

## 设计决策

- Registry 是平台级控制面，Skill 内容不带 Tenant 企业知识。
- 平台管理员授权独立于企业管理员，普通企业管理员和成员均不能访问 Registry API 或导航入口。
- ZIP 必须以 Skill slug 为唯一根目录并包含根级 `SKILL.md`；路径穿越、符号链接、特殊文件、加密条目、路径碰撞和超限包均拒绝。
- Agent 绑定只接受 published 版本；切换版本会改变 Runtime Profile ID，从而建立新的隔离 Runtime，而不是污染旧 Profile。
- 内置 Skill 作为初始 published 版本进入同一 Registry，避免出现两套部署机制。

## 结论

**PASS**。生产与 Git 基线明确，冻结边界和 Skill Registry V1 范围已经固定，可以进入开发阶段。
