# 可迁移网站发布框架

适用于 Nginx 托管的静态网站。流程固定为：本地 Git bundle → 自有服务器中转 → GitHub → 不可变发布目录 → `current` 软链接 → Nginx 校验与 reload → HTTPS 200 健康检查。失败时自动恢复上一个 `current` 版本。

## 新项目迁移

1. 复制 `deployment/`、`publish-via-server.ps1`、`.deploy-secrets.example` 到新项目根目录，并将 `.deploy-secrets.local`、`.deploy-bridge/`、`*.bundle` 写进 `.gitignore`。
2. 复制 `.deploy-secrets.example` 为 `.deploy-secrets.local`，只在本机填入服务器、GitHub SSH 别名、私钥路径与域名。不要提交该文件。
3. 在本机私密配置中填入 `NGINX_*` 字段及真实证书路径。框架会在每次发布时重建并启用该独立站点配置；不要手工复用其他项目的站点文件。
4. 先运行 `./publish-via-server.ps1` 模拟。代码提交后，运行 `./publish-via-server.ps1 -Execute` 发布。

## 约定

- 每次发布用 commit SHA 创建独立目录，避免覆盖线上版本。
- Nginx 配置是受管资源：每次发布会校验、重建并启用 `/etc/nginx/sites-enabled/<NGINX_SITE_NAME>.conf`。若新配置无法通过语法校验，会恢复原配置。
- 项目若包含 `admin_backend/`，框架会自动安装并重启 `kunyuan-admin.service`，后台只通过 HTTPS `/admin/` 暴露，数据库存储在服务器 `/var/lib/kunyuan-admin/`。
- `HEALTHCHECK_URL` 是公开访问地址。若 HTTPS 由 CDN、负载均衡或反向代理终止，设置 `ORIGIN_HEALTHCHECK_URL=https://127.0.0.1/` 与 `ORIGIN_HEALTHCHECK_HOST`，发布时会校验服务器本机的 HTTPS Nginx，而不会错误依赖公网回环。
- `SERVER_SITE_ROOT`、`SERVER_SITE_HOST` 是旧项目兼容字段；新项目使用 `SERVER_RELEASE_ROOT`、`HEALTHCHECK_HOST`。
- 本框架管理静态 Nginx 发布。Node、Docker、后端服务可沿用 Git 中转，但应另行定义服务重启与健康检查策略。
