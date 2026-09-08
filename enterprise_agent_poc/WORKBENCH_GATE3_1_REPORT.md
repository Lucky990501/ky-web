# Workbench V1 Gate 3.1 阻塞解除报告

日期：2026-09-08  
结论：**BLOCKED**

## 已解除：P0-1 DeepSeek Workbench Runtime

根因是浏览器验收仍请求到受限网络环境中的旧 Uvicorn 进程，而非 Codex Runtime 架构或 Key 配置错误。替换该进程后，以下真实测试成功：

| 测试 | Run ID | 结果 |
| --- | --- | --- |
| 管理员最小 Runtime：`只回复 OK` | `fb4b542c-b492-4d75-ad79-a64c079064d2` | completed，返回 `OK` |
| 管理员 MCP 最小测试：`enterprise_config_get` | `5b175c47-9e2a-41cf-baf9-adfd4721ded3` | completed，返回 Tenant A 企业名 |
| 浏览器图片闭环（首次恢复） | `3816b055-d067-4014-a35d-acbcf60fe24f` | completed；旧 MCP 格式未创建 generation |
| 浏览器图片闭环（对象存储复测） | `57312bab-343b-4426-8e82-a069b088afc3` | completed；对象存储与 generation 成功 |

新增管理员诊断 API：`GET /api/admin/runtime/diagnostics`，安全返回 Runtime/Provider/Model/Base URL/Wire API、密钥存在状态及 SHA-256 截断指纹、MCP 配置、最近错误；不返回完整 API Key。新增 `POST /api/admin/runtime/test`，支持 `ok` 与 `enterprise_config` 两个真实最小链路测试。

## 浏览器与图片资产证据

浏览器管理员登录 Tenant A，提交 `帮我做一张秋季招生海报`。实际观察到：

```text
Task queued → running → generating → completed
Workbench → Codex → DeepSeek → Platform MCP → Image Gateway → Object Storage → Generation
```

最终任务：

- task_id：`062fc022-1a51-4e9e-b033-049a7c42c3eb`
- run_id：`57312bab-343b-4426-8e82-a069b088afc3`
- conversation_id：`9723503c-1a4c-4c5f-8ec2-326d8b9a2afa`
- generation_id：`0e129a27-4497-4be3-8ba1-532dec554ba1`
- storage_key：`generated/tenant-a/615a212d81a5457f8390dcd35cc601aa.png`

MCP 旧进程被替换后，图片工具将 `storage_key` 优先返回，平台将 Provider 临时 URL 下载到 LocalStorage；generation 不再依赖第三方 URL。浏览器完成后可看到合规海报结果。企业积分从 200 成功变为 180、再变为 160；成功任务各扣 20，失败任务没有扣费流水。

## 当前仍阻塞 Gate 3.1 的项目

| 硬性条件 | 当前状态 |
| --- | --- |
| PostgreSQL 实际运行和可重复 migration | 未完成；只有 SQL 基线，Docker daemon 未运行且未检测到 `psql` |
| Redis + 独立 Worker | 未完成；当前仍是进程内 `asyncio.create_task` |
| 正式 Tenant 创建 / 编辑 / 启停 / platform_admin | 未完成；无产品入口，不能通过正常流程创建 `tenant_real_test` |
| 成员管理 | 未完成；无管理员邀请、禁用、列表 UI/API |
| 知识文件上传与 Worker | 未完成；当前仅文本知识，没有 PDF/DOCX/TXT/MD 上传、解析、索引、重试 |
| 素材二进制上传 | 未完成；当前是 URL 型素材记录，没有 PNG/JPG/JPEG/WEBP multipart 上传 |
| 两 Tenant 浏览器隔离 | 未完成；Gate 2 API/MCP 隔离存在，但未完成两个正式 Tenant 的 UI 验收 |
| 多轮 + Runtime restart Resume 浏览器验收 | 未重新完成 |

## 已知问题与技术债

1. 浏览器消息区当前显示模型最终文字，尚未把 platform generation 以 `<img>` 卡片形式呈现；
2. 本地对象存储是开发适配器，生产需要 OSS/S3 Provider 与访问签名策略；
3. Task Worker、迁移、鉴权与可观测性尚不满足生产要求；
4. 历史错误 Task 仍作为审计记录保留，未重复扣费。

## 验证状态

`python -m pytest -q`：**8 passed**（第三方 Starlette 弃用警告）。

只有在 PostgreSQL、Redis 独立 Worker、Tenant/成员入口、文件上传和两租户浏览器隔离均实际运行后，Gate 3.1 才能从 **BLOCKED** 提升为 PASS 或 PASS WITH ISSUES。
