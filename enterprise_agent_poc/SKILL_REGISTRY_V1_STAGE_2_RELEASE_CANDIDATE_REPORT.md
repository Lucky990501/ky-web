# Skill Registry V1｜Stage 2 Release Candidate 验收

验收日期：2026-09-11

## Release Candidate Identity

| 项目 | 值 |
| --- | --- |
| source commit | `f251e12584911c90288b12649b44320e0098ac2b` |
| archive | `dist/skill-registry-v1-f251e12.tar.gz` |
| archive SHA-256 | `858A5508248D9216AE2C6A146DCFA67D08AB53FBEC7E9BC202AAC016FCB9C3AD` |
| archive size | 3,063,661 bytes |
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

从 commit `f251e12` 建立独立临时归档后执行：

- JavaScript `node --check`：PASS；
- 完整 pytest：`52 passed`；
- 既有 FastAPI TestClient 弃用警告：1 条，非阻断。

首次干净归档测试使用 pytest 默认临时目录时，Windows 中文用户名路径被错误解码并产生 `PermissionError`；改为归档内纯英文 `--basetemp` 后全部通过。该错误发生在测试夹具创建前，不是产品代码失败。

## 生产状态

- 当前生产 Release 仍为 `20260911-59d67ac`；
- 未上传、未执行 migration、未切换服务；
- 不因后续验收文档 commit 重新构建或部署；
- 下一步必须从上述 source commit 和 archive checksum 继续。

## 进入生产前输入

需要用户明确指定一个现有登录邮箱作为平台管理员。授权脚本会先 dry-run，核对 user ID、Tenant 与邮箱后，才允许 `--execute`。

## 结论

**PASS**。Release Candidate 来源、内容、校验和与干净提交测试均可复核；等待平台管理员账号确认后进入受控生产发布与验收。
