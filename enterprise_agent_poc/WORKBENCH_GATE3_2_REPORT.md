# Workbench V1 Gate 3.2 产品基础设施收口报告

日期：2026-09-08  
结论：**BLOCKED**

## 实际基础设施检查

| 项目 | 实测结果 |
| --- | --- |
| Docker Desktop | 已尝试启动本机已安装程序 |
| Docker daemon | 不可用：当前账户无权读取 `C:\Users\猪猪\.docker\config.json`，且无权连接 `npipe://./pipe/docker_engine` |
| PostgreSQL | 未启动；无 `psql`，无法运行正式 migration |
| Redis | 未启动；未发现 Redis 服务或独立 Worker |
| API / Platform MCP | 本地 FastAPI 与 MCP 曾在 Gate 3.1 成功运行 |
| Runtime / 图片 / generation | Gate 3.1 有真实成功 Run 和 generation 记录 |

## Gate 3.2 项目状态

| 验收要求 | 状态 | 说明 |
| --- | --- | --- |
| PostgreSQL 正式运行、可重复 migration、health | BLOCKED | 只有 PostgreSQL SQL 基线，没有可用数据库实例 |
| Redis + 独立 Worker | BLOCKED | 当前产品仍使用进程内 `asyncio.create_task` |
| Tenant/成员产品入口 | BLOCKED | 未实现 platform_admin Tenant 创建、启停和成员管理流程 |
| 两 Tenant 正常浏览器登录 | BLOCKED | 没有正式 Tenant 创建入口，不能按要求建立 A/B |
| 知识文件上传、Worker 解析/索引 | BLOCKED | 只有文本知识 API，无 multipart 文件上传和异步处理 |
| 素材二进制上传 | BLOCKED | 只有 URL 素材记录，无 PNG/JPG/JPEG/WEBP multipart 上传 |
| 图片 generation 与对象存储 | PASS（Gate 3.1） | 最新成功 generation 使用平台 `storage_key`，而非 Provider 临时 URL |
| 浏览器图片卡片 | 部分完成 | 前端已接 generation 图片卡片代码；未在本轮以新任务再次完成 UI 截图验收 |
| 积分成功与失败 | 部分通过 | Gate 3.1 成功扣 20、失败不扣；余额不足和 retry 幂等未完成浏览器验收 |
| 多轮与 Runtime Resume | BLOCKED | 未在正式 Workbench 浏览器端完成三轮和重启恢复 |
| 客户首次使用 onboarding | BLOCKED | 前置 Tenant、上传、Worker 条件未满足 |

## 现有真实证据

- Runtime 最小 Turn：`fb4b542c-b492-4d75-ad79-a64c079064d2`，返回 `OK`；
- MCP 企业配置最小 Turn：`5b175c47-9e2a-41cf-baf9-adfd4721ded3`；
- 成功图片 Task：`062fc022-1a51-4e9e-b033-049a7c42c3eb`；
- 成功图片 Run：`57312bab-343b-4426-8e82-a069b088afc3`；
- Generation：`0e129a27-4497-4be3-8ba1-532dec554ba1`；
- 自动化回归：`8 passed`。

## 解除 BLOCKED 的最小行动

1. 为当前 Windows 用户授予 Docker Desktop/`docker_engine` 访问，或提供可访问的受管 PostgreSQL 与 Redis 连接；
2. 将业务 Store 切换至 PostgreSQL，并通过 migration runner 实际执行 migration；
3. 用 Redis 队列和独立 Worker 替换 API 内的 `asyncio.create_task`；
4. 补齐 Tenant/成员管理、知识/素材 multipart 上传与异步文件处理；
5. 以两个通过产品流程创建的 Tenant 完成浏览器隔离、多轮、Resume、积分不足/Retry 和 onboarding 验收。

没有这些真实运行证据，Workbench V1 不能交给真实客户试运行。
