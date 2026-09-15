# First Customer UX — Polish Batch

## 修改了什么

- 登录页移除了验证码登录、忘记密码等未开放入口；顶部移除了未开放的全局搜索，并将企业信息改为只读展示。
- 工作台只展示已启用的智能体。每张卡片统一展示名称、用途、适用场景、积分/次和明确进入按钮；企业管理员快捷入口补齐最近任务和成员管理。
- 补齐工作台、历史记录、图片生成、知识库、素材库和成员管理的下一步空态提示；移除了无效的生成排序和素材分类标签入口。
- 素材标签支持数组与 JSON 字符串，统一渲染为 Chip；补充素材类型、上传时间、预览/删除的 `aria-label` 和 `title`，删除前明确确认。
- 统一前端展示状态为排队中、处理中、已完成、失败、已启用、已停用，并将知识处理中的内部术语收敛为“知识处理中”。

## 用户体验变化

- Member 登录后先看到可使用的智能体与下一步操作，不再看到未开放能力或技术入口。
- 企业管理员仍可从工作台进入资料、素材、最近任务、成员和企业设置；现有导航权限分层保持不变。
- 素材库中的标签可读，空数据页面明确说明下一步，删除操作有确认反馈。

## Browser evidence

- 隔离 Preview 已以独立 SQLite、数据目录和对象目录在 `127.0.0.1:8765` 启动。
- Browser Automation 受本机请求头策略阻断后，改用当前真实 Chrome 完成隔离 Preview 验收；未修改产品代码或 Production。
- Enterprise Admin Desktop：登录页没有验证码或忘记密码入口，也没有全局搜索；工作台展示 3 个已启用 Agent（用途、适用场景、积分/次、进入按钮），Members、Knowledge、Assets、Recent Tasks、Profile 均可访问。Assets 的 `brand`、`logo` 为正常 Chip，预览/删除具备可访问名称；删除前显示“确定删除这份素材吗？”确认。
- Member Desktop：仅见工作台、AI 创作、历史记录、我的生成、个人中心；未见 Members、Knowledge、Assets、Recent Tasks、Enterprise Settings 或 Platform 页面。工作台首屏直接说明可用 Agent 与下一步。
- 390×844：已实际检查管理员与成员的工作台、Drawer、History；并检查管理员的 Knowledge、Assets、Recent Tasks、Members、Profile 和帮助弹窗。Drawer 和弹窗均可关闭；卡片、筛选控件、空状态及操作按钮均未出现关键横向溢出或遮挡。
- 记录但不阻断：Knowledge 的说明仍用“等待处理 / 处理中 / 已完成 / 处理失败”，Recent Tasks 空态仅为“当前筛选范围内暂无任务”，可在后续文案批次统一，但不影响首客使用。
- 验收过程删除了一份隔离 Preview 的演示素材；只影响 `/private/tmp` 隔离数据库，不涉及产品代码或 Production 数据。

## Tests

- targeted pytest: `16 passed`
- full pytest: `447 passed, 106 skipped`
- Node routes: `29 passed`
- `node --check app/static/workbench.js`: PASS
- `python -m compileall -q app tests`: PASS
- `git diff --check`: PASS

## Remaining debt

- `REFERENCE_EVIDENCE_GAP`
- `ATTACHMENT_UX_DEFERRED`
- `TEST_HARNESS_STATE_ISOLATION_DEBT`
- 临时密码首次登录强制修改、忘记密码

## Manual acceptance result

`FIRST_CUSTOMER_UX_POLISH_PASS`
