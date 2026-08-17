# 可迁移网站发布框架

适用于 Nginx 托管的静态网站。流程固定为：本地 Git bundle → 自有服务器中转 → GitHub → 不可变发布目录 → `current` 软链接 → Nginx 校验与 reload → HTTPS 200 健康检查。失败时自动恢复上一个 `current` 版本。

## 新项目迁移

1. 复制 `deployment/`、`publish-via-server.ps1`、`.deploy-secrets.example` 到新项目根目录，并将 `.deploy-secrets.local`、`.deploy-bridge/`、`*.bundle` 写进 `.gitignore`。
2. 复制 `.deploy-secrets.example` 为 `.deploy-secrets.local`，只在本机填入服务器、GitHub SSH 别名、私钥路径与域名。不要提交该文件。
3. 在服务器 Nginx 站点配置中，将根目录固定为 `/var/www/<PROJECT_NAME>/current`；首次创建目录并授予部署用户权限。
4. 先运行 `./publish-via-server.ps1` 模拟。代码提交后，运行 `./publish-via-server.ps1 -Execute` 发布。

## 约定

- 每次发布用 commit SHA 创建独立目录，避免覆盖线上版本。
- `HEALTHCHECK_HOST` 会让服务器本机以 HTTPS 校验指定虚拟主机；不设置时直接访问 `HEALTHCHECK_URL`。
- `SERVER_SITE_ROOT`、`SERVER_SITE_HOST` 是旧项目兼容字段；新项目使用 `SERVER_RELEASE_ROOT`、`HEALTHCHECK_HOST`。
- 本框架管理静态 Nginx 发布。Node、Docker、后端服务可沿用 Git 中转，但应另行定义服务重启与健康检查策略。
