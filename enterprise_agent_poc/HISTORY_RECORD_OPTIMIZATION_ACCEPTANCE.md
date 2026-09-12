# 历史记录与图片渲染优化验收报告

日期：2026-09-12
状态：本地开发验收 PASS；尚未推送或部署生产

## 1. 本轮目标

- 让历史记录清楚表达“做了哪个创作项目”。
- 按智能体和项目类型归类历史内容。
- 让已生成图片在刷新、重新登录和再次打开历史后仍能渲染。
- 保持现有 Embedding、RAG、pgvector 和 Agent Runtime Core 不变。

## 2. 已确认根因

1. 会话正文保存在 `messages`，图片产物保存在独立的 `generations`；原会话详情没有返回图片附件，刷新后前端无法恢复图片。
2. 会话标题长期使用“新会话”等默认值，历史列表没有智能体、任务状态、最近需求和产物数量，用户无法判断每条记录属于哪个项目。
3. 不同页面自行拼接图片地址，空 Key、中文、空格或 `#` 等字符可能产生错误 URL，且缺少统一鉴权响应契约。
4. 图片任务仅从最多 600 字符的工具摘要中用正则恢复 `storage_key`；工具结果较长或 SDK 表示变化时，图片虽已生成却可能无法关联到历史记录。
5. `asset_metadata` 的旧外键没有级联删除，删除企业素材会触发外键错误，导致已经共享的生成图片无法撤销团队访问。
6. 历史页与项目页快速前进/后退时，较早发出的异步请求可能覆盖当前页面，出现“URL 是历史页但内容仍是项目页”的状态竞争。
7. 历史列表若逐会话查询任务和图片，50 条记录最多产生 251 次 SQL，不适合历史数据持续增长。

## 3. 实现结果

### 3.1 项目归类与查找

- 采用兼容语义：当前一个 Conversation 对应一个创作项目，不新增业务实体。
- 按图片生成、文案创作、活动策划智能体分组。
- 默认标题使用首次任务需求自动生成；用户自定义标题不会被覆盖。
- 展示项目类型、智能体、最近需求、任务状态、执行次数、图片数量和最近活动时间。
- 历史按最近任务活动时间排序，继续创作的项目自动回到列表前部。
- 增加独立“历史记录”入口、项目搜索、智能体筛选及无结果状态。
- 每次加载 50 个项目，支持继续加载更早记录，不再静默截断。

### 3.2 历史图片与任务完成链路

- 会话详情返回项目内的 tasks、generations 和图片计数。
- 通过确定性 `task_id` 把图片挂接到对应助手消息。
- 旧数据没有 messages 时，可从任务结果恢复只读历史并关联原图片。
- 无法挂接到具体消息的旧图片仍在“本项目生成图片”区域展示。
- 会话列表、会话详情、助手消息、任务终态和“我的生成”统一返回同源鉴权 `content_url` / `image_url`。
- 新运行从 Codex SDK 的 `structured_content` 提取且只持久化允许字段 `storage_key`；旧 Trace 继续兼容摘要正则恢复。
- SSE 对 PostgreSQL datetime 做 JSON/ISO 编码，并在任务终态直接返回 generation，避免完成事件断流或页面二次查询竞态。
- 图片响应使用已保存 MIME；旧记录缺失 MIME 时兼容 `image/png`。
- 增加加载态、失败态、原位重试、大图预览、下载和键盘焦点管理。

### 3.3 权限与生命周期

- 私人生成图片继续按 tenant + user 鉴权。
- 图片保存为企业素材后，仅同租户成员可读取；跨租户仍拒绝。
- 删除企业素材时，同事务先删除旧 `asset_metadata`，团队共享授权随即撤销。
- 删除历史项目后，“我的生成”不再显示无效项目跳转。
- 删除个人 generation 后，已保存为企业素材的对象仍可读；未保存为素材的对象仍不可被其他成员读取。
- 图片响应设置 `private, no-store` 和 `nosniff`，避免账号切换后复用旧鉴权缓存。
- OSS/本地对象不存在返回 404；配置、权限或临时 I/O 异常返回脱敏 503。

### 3.4 导航、响应式与可访问性

- `/conversations` 可直接打开和刷新，并支持浏览器前进/后退恢复页面与项目状态。
- 图片、文案和活动项目拥有独立前端路径；项目选择与任务完成后同步 History API 状态。
- 增加异步页面令牌校验，旧请求不会覆盖用户已经切换到的页面。
- 移动端历史页提供“返回工作台”，历史入口和按钮点击区域不小于 44px。
- 历史入口可通过 Tab 聚焦；图片预览支持 Escape、焦点循环及关闭后焦点归还。
- 图片加载失败不再把紧凑缩略图撑坏，并保留屏幕阅读器状态说明。

### 3.5 性能与数据库

- 历史列表由最多 N+5 查询优化为固定 3 次批量查询。
- 新增 append-only PostgreSQL migration `007_history_storage_indexes.sql`，只为历史/图片鉴权访问增加普通 B-tree 索引，不修改已执行 migration。
- SQLite 初始化同步创建相同索引，保证本地与生产查询模式一致。
- 空白 `storage_key` 不产生伪图片链接。
- 已下线智能体使用安全的历史名称，不会使整个历史页 500。
- 运行中的 SSE 轮询不查询 generation，仅在终态查询，减少数据库压力。
- 静态资源版本升级为 `history-v1`，避免旧缓存遮蔽新功能。

### 3.6 受控发布安全

- systemd drop-in 仅在依赖、migration 和配置指纹预检通过后备份并进入切换阶段，前置失败不再误删现网配置或重启稳定服务。
- 自动回滚会先关闭递归 ERR trap，并在单项恢复失败时继续完成其余恢复步骤。
- 配置指纹按顶层 `matches` 严格判断；单个字段的 `matches=true` 不能掩盖整体不一致。
- 健康门禁除 HTTP 成功外，还要求 `status=ok`、`knowledge=ok`、`environment=production`。
- migration 007 设置有限 lock/statement timeout，无法安全取得锁时中止发布，不无限阻塞生产写入。

## 4. 修改文件

- `app/product_store.py`
- `app/product_service.py`
- `app/runtime/codex_provider.py`
- `app/main.py`
- `app/storage.py`
- `app/static/workbench.js`
- `app/static/workbench.css`
- `app/static/index.html`
- `migrations/postgres/007_history_storage_indexes.sql`
- `deploy/release_switch.sh`
- `scripts/verify_runtime_config.py`
- `tests/test_history.py`
- `tests/test_run_trace.py`
- `tests/test_storage.py`
- `tests/test_release_switch.py`
- `tests/test_knowledge_ingestion.py`

## 5. 自动化验收

- Python compileall：PASS
- JavaScript `node --check`：PASS
- Git Bash `bash -n deploy/release_switch.sh`：PASS
- `git diff --check`：PASS
- 历史、存储、任务定向回归：29 passed
- 完整 pytest：86 passed，1 个既有 Starlette TestClient 弃用警告

新增回归覆盖：

- 图片、文案项目归类、分页和任务隔离。
- 五个数据入口的 `content_url == image_url == canonical URL`，并逐一真实 GET PNG 字节。
- 未登录、同租户其他用户、跨租户对会话、任务、生成列表和图片对象的访问边界。
- 企业素材授权、个人生成软删除、素材删除后的授权撤销及未共享负对照。
- 无 messages 的旧任务正文和图片恢复。
- 存储缺对象、Provider 配置失败和本地 I/O 异常的安全映射。
- PostgreSQL 风格 datetime 的 SSE 编码和终态 generation。
- SDK `structured_content` 图片 Key 提取、敏感字段丢弃及旧 Trace 回退。
- 跨租户或非法图片对象 Key 不得关联 generation、不得完成任务或触发扣费。
- `/conversations` 及三个 Agent 项目路径的直接访问均返回当前应用壳。
- 历史访问索引在 SQLite 和 append-only migration 中均存在。
- 发布配置整体不一致时 CLI 返回非零；发布脚本备份时序、严格健康 JSON 和回滚防递归均有回归门禁。

## 6. 浏览器验收

使用隔离的本地数据库、对象目录和合成 PNG 进行真实浏览器验证：

- `/conversations` 直接访问与刷新恢复历史页：PASS
- 按智能体展示“图片生成项目”及项目名、需求、状态、次数、图片数：PASS
- 历史卡片图片真实加载：HTTP 200，`complete=true`，`naturalWidth=1`，未进入失败态
- 进入项目后，助手消息正文和对应历史图片同时显示：PASS
- 项目页刷新后仍恢复相同项目和图片：PASS
- 快速进入项目后立即后退，URL 与历史页内容保持一致：PASS
- 390px 视口无横向溢出：`scrollWidth=390`
- 移动端“返回工作台”可见且高度 44px：PASS
- 临时服务、数据库和对象目录在验收后已停止并清理

## 7. 边界与待发布事项

- 未修改 Embedding Provider、Embedding Model、RAG、Query Guard、Confidence Gate、pgvector 或知识检索策略。
- 新增的 007 仅包含历史业务表和图片鉴权所需 B-tree 索引，尚未在任何生产数据库执行。
- 未修改或恢复任何已清理的 Embedding 实验。
- 未推送 Git、未构建 Release、未部署生产。
- 生产真实历史数据与 OSS 图片仍需在受控 Release 部署后做一次浏览器复验。
- 搜索和筛选作用于当前已加载的历史批次；超过 50 条时需先点击“加载更多”。
- 已退役智能体的旧项目可以安全展示，但不允许恢复到已不存在的智能体继续执行。

## 8. 验收结论

本地功能、权限、兼容性、性能、真实图片渲染和响应式浏览器验收均通过。

**结论：PASS（等待生产受控发布授权）**
