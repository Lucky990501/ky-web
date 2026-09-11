# Skill Registry V1｜Stage 3 生产受控发布验收

验收时间：2026-09-11 11:32:32 +08:00

## 1. Release Identity

| 项目 | 值 |
| --- | --- |
| Release ID | `20260911-4faed8e` |
| Source commit | `4faed8e14955b0303aca4fb46089081d3eeeea5e` |
| Archive | `skill-registry-v1-4faed8e.tar.gz` |
| Archive SHA-256 | `6D7E96BBF49710085F3EEC8CA5C36BECEF81896209202714176FFF76DAC14B4C` |
| Previous Release | `20260911-59d67ac` |
| 作废且禁止部署的 commit | `f251e12584911c90288b12649b44320e0098ac2b` |

本地与生产服务器分别计算归档 SHA-256，结果完全一致。生产端归档扫描确认 `.env`、`.pem`、`.key`、运行数据及 Token/Secret 命名条目为 0。

## 2. Migration 预检

候选 Release 的 `python scripts/migrate.py status` 返回：

- 001–004：`applied`；
- 005 `skill_registry_v1.sql`：`pending`；
- unknown history versions：空。

预检符合执行条件。

## 3. Platform Admin Dry-run

默认 dry-run 前后快照一致：

- `skills`、`skill_versions`、`skill_packages`、`agent_skill_bindings`、`platform_admins`：均未创建；
- migration 005 历史记录：均为 0；
- shared Registry 数据目录：均不存在；
- 用户原租户角色：`enterprise_admin`；
- 权限修改：0；
- 核验结果：`zero_write_verified=true`。

## 4. Migration 005

执行 `python scripts/migrate.py up` 后：

- applied now：`005`；
- 001–005：全部 `applied`；
- pending：0；
- unknown history versions：空；
- 005 applied at：`2026-09-11 11:28:14.891101+08:00`。

## 5. Platform Admin 授权

已对用户确认的生产账号执行显式 `--execute`：

- 用户：`126***@qq.com`；
- user ID：`ba2afd04-0cfd-45fe-9771-ef8e741796ba`；
- Tenant：`zhiy-e-intelligence`；
- 原租户角色：`enterprise_admin`，保持不变；
- 独立平台权限：`platform_admin=true`。

## 6. 受控 Release 切换

- RC 解包到独立目录 `/opt/enterprise-agent-workbench/releases/20260911-4faed8e`；
- 未覆盖旧生产工作树；
- shared environment 与 shared runtime data 继续使用稳定路径；
- config fingerprint match：`true`；
- `release-current` 已原子切换到新 Release；
- API、Platform MCP、Worker 的 systemd WorkingDirectory 均指向新 Release；
- 旧 Release `20260911-59d67ac` 目录仍完整存在，`rollback_available=true`。

切换期间 API 在启动窗口内出现 4 次本机连接拒绝，随后在脚本 30 秒健康门限内恢复，受控切换成功，未触发回滚。

## 7. 切换后基础检查

| 检查项 | 结果 |
| --- | --- |
| API service | active |
| Platform MCP service | active |
| Worker service | active |
| 本机 `/api/health` | `status=ok`, `knowledge=ok` |
| 公网 `/api/health` | `status=ok`, `knowledge=ok` |
| Environment | production |
| Runtime | `openai-codex==0.147.0` |
| Migration pending | 0 |
| Config fingerprint | match |

## 8. 冻结能力保护

对生产旧 commit `59d67ac` 与本次 source commit `4faed8e` 的变更清单进行复核：未修改 RAG 检索实现、V1.4 参数、Enterprise Knowledge 核心策略或 Tenant Isolation 实现。本次变化限定在 Skill Registry、Agent Skill manifest 解析、管理入口、migration 005、授权工具和测试。

- Enterprise Knowledge：`STABLE`；
- RAG Retrieval V1.4：`FROZEN`。

## 9. 未完成 Gate

生产浏览器控制接口在本机出现 CUA 连接失败及超时，尚未完成：

- 平台管理员登录后的 Skill 管理入口可见性；
- `/platform/skills` 已登录访问；
- 普通 `enterprise_admin` 生产 API 403；
- Native Skill ZIP 到真实 Agent Turn 的完整生产功能验收。

该问题不影响已完成的 Release 切换与服务健康，但阻止宣告 Skill Registry V1 最终 PASS。

## 10. 阶段结论

**生产受控发布：PASS。**

**Skill Registry V1 最终结论：尚未判定。** 下一阶段必须完成生产浏览器权限 Gate，以及图片、文案、活动策划三类 Skill 的 Upload → Validate → Draft → Test → Publish → Bind → Runtime Sync → Codex Discovery → Real Turn → Upgrade / Rollback 全链路验收。
