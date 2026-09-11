# Skill Registry V1｜Stage 2 Release Candidate 验收

验收日期：2026-09-11

## Release Candidate Identity

| 项目 | 值 |
| --- | --- |
| source commit | `4faed8e14955b0303aca4fb46089081d3eeeea5e` |
| archive | `dist/skill-registry-v1-4faed8e.tar.gz` |
| archive SHA-256 | `6D7E96BBF49710085F3EEC8CA5C36BECEF81896209202714176FFF76DAC14B4C` |
| archive size | 3,063,959 bytes |
| selected files | 63 |

Release Candidate 由 `scripts/build_release.py` 直接从明确 Git commit 构建，没有读取当前工作树的未提交改动。

## 包内容核对

已确认包含：

- `app/skill_registry.py`；
- `app/main.py`、`app/service.py`、`app/skills.py`；
- `/platform/skills` 前端资源；
- `migrations/postgres/005_skill_registry_v1.sql`；
- `scripts/grant_platform_admin.py`；
- 现有原生 `skill_packages`。

构建脚本只允许 app、migrations、scripts、skill packages、deploy 和固定运行文件进入归档。未发现 `.env`、`.pem`、`.key` 或 `.runtime-data`。

## 干净提交验证

从 commit `4faed8e` 建立独立完整源码归档后执行：

- JavaScript `node --check`：PASS；
- Python `compileall`：PASS；
- 完整 pytest：`54 passed`；
- 既有 FastAPI TestClient 弃用警告：1 条，非阻断。

首次干净归档测试使用 pytest 默认临时目录时，Windows 中文用户名路径被错误解码并产生 `PermissionError`；改为归档内纯英文 `--basetemp` 后全部通过。该错误发生在测试夹具创建前，不是产品代码失败。

## Dry-run 零写入修复

初版候选 commit `f251e12584911c90288b12649b44320e0098ac2b` 的平台管理员授权脚本会在 dry-run 路径调用 Registry 初始化，可能创建表和数据目录，不满足“dry-run 必须零写入”的发布要求。因此：

- 初版归档 `dist/skill-registry-v1-f251e12.tar.gz` 已作废，禁止部署；
- commit `4faed8e` 将账号只读查询与显式授权拆开；
- 默认 dry-run 不初始化 ProductStore、不初始化 Skill Registry、不创建表、不创建 Registry 目录；
- `--execute` 不会隐式补建 Schema，migration 005 未就绪时必须失败；
- 已增加 dry-run 零建表、零目录写入与显式授权回归测试。

## 生产状态

- 当前生产 Release 仍为 `20260911-59d67ac`；
- 未上传、未执行 migration、未切换服务；
- 不因后续验收文档 commit 重新构建或部署；
- 下一步必须从上述 source commit 和 archive checksum 继续。

## 平台管理员匹配

已通过生产数据库只读查询确认用户提供的账号：

- 邮箱：`126***@qq.com`；
- user ID：`ba2afd04-0cfd-45fe-9771-ef8e741796ba`；
- Tenant：`zhiy-e-intelligence`（知野智能）；
- 当前租户角色：`enterprise_admin`。

查询期间未执行授权、migration、上传或服务切换。进入生产仍需对明确账号执行受控 `--execute`，并按新 Release 流程完成 migration、配置校验、切换和验收。

## 结论

**PASS**。新 Release Candidate 来源、内容、校验和、dry-run 零写入行为与干净提交测试均可复核；等待生产执行确认后进入受控发布与验收。
