# Workbench V1 正式前端开发报告

## 结论

当前 FastAPI 静态前端已升级为可接 Gate 3.1 后端的企业工作台界面；当前仓库未包含 Next.js 项目，因此未创建或伪称复用 Next.js。正式迁移到 Next.js 需另行建立前端工程和部署边界。

## 页面与路由

| 页面 | 路由 | 接口 |
| --- | --- | --- |
| 登录 | `/login` | `/api/v1/auth/login` |
| 工作台 | `/workspace` | `/api/v1/workspace` |
| 图片 Agent | `/agents/image` | Agent task、Task polling、conversations、generations |
| 我的生成 | `/generations` | generations、storage、save-to-assets、delete |
| 企业知识库 | `/knowledge` | knowledge files/text/delete |
| 企业素材库 | `/assets` | assets create/list/delete |
| 企业配置 | `/enterprise-config` | enterprise config get/put |
| 会话历史 | `/conversations` | conversations list/rename/delete |

## 已实现交互

- 统一桌面工作台布局、响应式侧栏、可见焦点、缩减动画支持和 44px 最小主要点击区域；
- 图片 Agent 的会话入口、历史列表、文本输入、Polling 状态映射及错误提示；
- Task 完成后从 generation 查询平台对象存储图片，展示图片、下载及保存到企业素材库；
- 管理员配置、知识文本、素材 URL、生成记录及会话的现有后端接口已接入；
- 未在前端直接访问 Codex、DeepSeek、MCP 或 Image Provider。

## 浏览器验证

本地 `http://127.0.0.1:8090/login` 已实际验证管理员登录、工作台、图片 Agent、异步 `generating` 状态以及任务完成回复。Gate 3.1 最新成功任务有 generation 记录，前端现在按 `task_id` 查询并使用 `/api/v1/storage/{storage_key}` 展示，不依赖 Provider 临时 URL。

## Mock 与后端依赖

没有前端 Mock API；开发账号、Tenant A/B 和余额来自现有 SQLite 开发数据。图片、知识文件二进制上传、成员管理、Redis Worker、PostgreSQL 和正式 Tenant onboarding 仍依赖 Gate 3.1 后端阻塞解除。

## 已知问题

1. 当前知识与素材页仍对应后端现有的文本/URL API，尚未支持二进制上传及拖拽进度；
2. 文案与活动 Agent 仅为规划项，尚未展示“即将上线”卡片；
3. 历史会话检索、附件上传和生成时间/Agent 筛选仍依赖后端补齐；
4. 当前静态模块没有 Next.js 组件边界；如需正式 Next.js 工程，需要明确前端包管理与部署方案。
