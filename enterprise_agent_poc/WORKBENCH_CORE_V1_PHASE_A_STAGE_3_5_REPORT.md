# Workbench Core V1 Phase A｜阶段 3-5 验收报告

验收日期：2026-09-10  
范围：Phase 3 干净 Release 构建、Phase 4 配置注入校验、Phase 5 受控切换与回滚。  
结论：**PASS WITH ISSUES**（本地构建验收通过，生产切换尚未执行）。

## 已交付

- `scripts/build_release.py`
  - 强制解析为 40 位明确 Git commit。
  - 只归档 `app`、`migrations`、`scripts`、`skill_packages`、`deploy`、依赖清单及容器定义等白名单路径。
  - 在构建前拒绝 `.env`、`.pem`、`.key` 和 `.runtime-data` 路径，输出 commit、SHA-256、文件数，不输出任何内容值。
- `deploy/release_switch.sh`
  - 只接受字母、数字、`.`、`_`、`-` 组成的 release ID，并固定发布根目录为 `/opt/enterprise-agent-workbench/releases/<release-id>/enterprise_agent_poc`。
  - 配置只从 `/opt/enterprise-agent-workbench/shared/enterprise-agent.env` 注入；该文件必须存在且不可被组或其他用户读取。
  - 在 release 自身虚拟环境安装依赖、运行迁移、执行配置指纹校验，然后用 systemd drop-in 切换 API、MCP、Worker 的工作目录与启动命令。
  - 重启后检查三个服务与 loopback health；任一步失败都会恢复切换前的 `current` 链接和 systemd drop-in。

## 基线事实

生产主机只读核查确认，现有三个 systemd 单元均直接使用旧的脏工作目录：

- `WorkingDirectory=/opt/enterprise-agent-workbench/repo/enterprise_agent_poc`
- `EnvironmentFile=/opt/enterprise-agent-workbench/repo/enterprise_agent_poc/.env.production`

因此不能将旧目录当作本次发布根目录。新切换机制使用独立 `releases`、`shared` 与 `current` 路径，保留旧单元文件与旧目录作为回滚基线。

## 验证证据

| 检查 | 结果 |
| --- | --- |
| `build_release.py --dry-run` | 通过，commit 被解析为 40 位 SHA |
| 实际 Release 归档 | 通过，57 个白名单文件，输出 SHA-256 |
| Python `compileall` | 通过 |
| `git diff --check` | 通过 |
| Bash 静态检查 | 本机 Windows Bash 服务无启动权限，未能执行；生产执行前需先运行 `bash -n` |

## 尚未完成 / 阻塞

- GitHub 直连此前连续失败；根据任务约束，在完成 Git 对账前不得声明发布完成。
- 发布脚本尚未上传到生产，也未创建 release 目录、共享环境文件或 systemd drop-in；没有修改生产服务。
- 本阶段尚未运行生产迁移、配置指纹校验或服务切换，故不能替代后续租户隔离、浏览器 E2E、三 Agent、无知识与恢复门禁。

## 下一步

1. 对齐本地、origin 与生产 Git 状态；若 GitHub 仍不可用，记录 `GIT_SYNC_BLOCKED` 并停止发布声明。
2. Git 对账通过后，用单一 commit 归档上传到独立 release 目录，先执行 `bash -n`、迁移 `status`、配置指纹预检和受控租户 dry-run。
3. 仅在以上检查成功后执行受控切换，保留旧 release，并开始生产隔离与产品 E2E 验收。
