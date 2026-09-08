# Gate 3.3｜云服务器部署与最终验收报告

验收时间：2026-09-08（Asia/Shanghai）  
结论：**PASS WITH ISSUES**（生产试运行可用；尚不满足严格的容器化正式发布口径）

## 1. 已部署拓扑

| 层级 | 线上实现 | 验证结果 |
| --- | --- | --- |
| HTTPS 入口 | Nginx + Let's Encrypt，`https://workbench.luckio.cn` | 根路径返回 HTTP 200 |
| API | FastAPI/Uvicorn，仅监听 `127.0.0.1:18090` | `/api/health` 返回 `ok` |
| Platform MCP | FastMCP，仅监听 `127.0.0.1:18091` | 内部真实工具调用成功 |
| Worker / 队列 | systemd Worker + Redis | 真实任务完成 |
| 数据库 | PostgreSQL | 已完成迁移并用于线上任务、会话与生成记录 |
| 图片存储 | 阿里云 OSS | 真实生成图片已保存并可通过受鉴权接口读取 |

本轮按低成本方案采用 `systemd + apt`，没有启用 Docker Compose；PostgreSQL、Redis 和 MCP 均未暴露到公网。

## 2. 运行时基线

| 项目 | 实际配置 |
| --- | --- |
| Codex Harness | `openai-codex==0.147.0` |
| Provider | `deepseek` |
| Wire API | `responses` |
| Model | `deepseek-v4-pro` |
| Reasoning effort | `high` |
| Skill | `poster-design@1.0.0` |
| 密钥处理 | DeepSeek、图片网关、OSS 均仅存在服务器 `.env.production`；未提交仓库 |

模型 Provider、模型 ID 和 reasoning effort 均从 Runtime Profile / 环境配置读取，`CodexRuntimeProvider` 未写死模型。

## 3. 真实在线验证

### 3.1 最小 Turn 和 MCP

| 测试 | 结果 |
| --- | --- |
| DeepSeek + Codex：`只回复 OK` | 成功，返回 `OK` |
| `enterprise_config_get` | 成功，返回当前企业名称 |

### 3.2 知野智能真实图片任务

企业 Tenant：`zhiy-e-intelligence`（知野智能）  
请求：`帮我做一张秋季招生海报`

| 项目 | 结果 |
| --- | --- |
| 任务 ID | `ed5fab1f-a857-4e0c-8437-c54296cb2897` |
| Run ID | `2bc6ea9f-93d6-43ea-9b23-fbb95d10a990` |
| Conversation ID | `3d962804-179e-422e-9904-f81be1c7b569` |
| Codex Thread ID | `01a08181-d147-7062-ba2a-8912448aa40b` |
| 状态 | `completed` |
| 总耗时 | 118,169 ms |
| Skill | `poster-design@1.0.0` |
| MCP 调用 | `enterprise_config_get`、`knowledge_search`、`asset_search`、`image_generation`，均完成 |
| 图片 | PNG，1024×1536，已写入 OSS |
| OSS 存储键 | `generated/zhiy-e-intelligence/1385ef2728c843e78b79ee7eb0419668.png` |
| Token / 成本 | Provider 未返回可用 token usage / estimated cost，记录为 `null`，未估算 |

最终结果遵守了企业品牌色和 Slogan。由于当前企业没有上传 Logo、课程、师资或价格资料，Agent 明确留空，未虚构企业事实。

## 4. 修复记录

首次真实图片任务发现图片网关返回 HTTP 401。根因是部署时另一份配置覆盖了工作台使用的 `GATEWAY_API_TOKEN`。已将服务器 `.env.production` 修正为工作台专用令牌，重启 MCP 与 Worker 后，图片网关请求和 OSS 读取均返回 HTTP 200。

另修复了域名根路径 404：现在 `/` 进入工作台登录页，并新增稳定健康检查 `/api/health`。

相关代码已推送：

- `d5c4ef2`：MCP 内部网络可达性修复
- `f88e3ef`：根路径与生产健康检查修复

## 5. 安全与隔离状态

- MCP、Redis、PostgreSQL 只监听本机回环地址。
- API 使用 HttpOnly / Secure 会话 Cookie；生产环境关闭演示数据自动创建。
- 生成文件通过租户和用户归属校验后才可读取。
- Run Trace 记录工具输入/输出摘要、耗时和结果；不记录模型隐藏推理内容。
- 密钥未写入仓库、报告或前端响应。

## 6. 遗留项与正式发布条件

1. 本轮根据成本决策使用 systemd 部署，故不满足 Gate 3.3 的 Docker Compose 正式标准；后续稳定后应迁移到 Compose / 镜像仓库。
2. 已验证单个真实企业 Tenant 的全链路；Tenant B 的完整线上回归、多轮续聊和重启后 Thread Resume 需要作为上线前回归继续执行。
3. 当前真实 Provider 未返回 token usage 和成本字段，控制台会如实显示为空；需要接入 Provider 可计量账单或单独的计费估算器。
4. 当前管理端知识库使用文本录入、素材库使用 URL 录入；PDF/DOCX 文件上传及解析仍需按产品需求补齐。
5. 服务器仓库存在未跟踪 `enterprise_agent_poc/build/` 目录，未被本次部署覆盖；应由运维确认来源后再清理或加入忽略规则。

## 7. 建议

可以将该环境作为受控的生产试运行环境：用户可登录、创建图片任务、看到生成状态与图片、下载图片并保存到素材库。正式大规模上线前，应完成上述遗留项，并按 Gate 3.3 容器化与多租户回归标准复验。
