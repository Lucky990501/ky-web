# Skill Registry V1｜Stage 4 Agent 绑定 P0 修复验收

验收时间：2026-09-11 12:29:21 +08:00

## 1. 生产发现

平台管理员在生产页面导入并发布 `poster-design 1.1.0` 后进行 Agent 绑定。截图与生产数据库只读核验确认：

- `poster-design 1.1.0` 已发布；
- `image-agent → poster-design 1.1.0` 已实际保存成功；
- 页面刷新后下拉框重新显示首个 Agent“活动策划智能体”，造成保存失败的视觉假象；
- 首次操作同时产生了非预期的 `campaign-agent → poster-design 1.1.0` 新绑定；
- 当前版本没有解除绑定 API/UI，无法通过产品操作恢复原始 Campaign manifest。

进一步代码审计发现：Registry 启动种子化会对 bundled Skill 执行 upsert 绑定，服务重启可能把已升级的 1.1.0 覆盖回 catalog 的 1.0.0。

## 2. 根因

1. Agent 下拉框没有空白占位，页面刷新后默认选中列表首项；
2. 新增绑定与版本切换共用相同请求，没有区分高风险的“新增 Agent-Skill 关系”；
3. 产品未提供解除绑定能力；
4. bundled seed 使用覆盖式绑定，没有只在缺失时初始化。

## 3. 修复内容

- 下拉框默认显示“请选择智能体…”，不再伪装为已选择首项；
- 未明确选择 Agent 时，前端拒绝绑定；
- 新增 Agent-Skill 关系前显示明确确认；
- API 默认拒绝未携带 `allow_new_binding=true` 的新增关系；
- 已有关系的版本升级/回滚保持原行为；
- 新增 `DELETE /api/v1/platform/agents/{agent_id}/skills/{skill_slug}`；
- 每条现有绑定增加独立解除按钮和影响提示；
- 解除绑定后同步更新 Agent 的 Registry manifest；
- bundled seed 改为只补齐缺失绑定，绝不覆盖现有升级版本；
- `manifest_for_agent` 以 Registry 真实绑定为准，不在空绑定时静默回退 catalog。

## 4. Release Candidate

| 项目 | 值 |
| --- | --- |
| Source commit | `4edc5edae830c9fd17574dff5abec70c99dc054f` |
| Archive | `dist/skill-registry-v1-p0-4edc5ed.tar.gz` |
| SHA-256 | `4E3DB2CF468D9304DD92E1B3D5CCE0DDEC65C7752BCBB69D2760914682180BD9` |
| Archive size | 3,064,736 bytes |
| Selected files | 63 |
| Forbidden archive entries | 0 |

制品直接从明确 commit 构建，不读取当前工作树的历史未提交文件。

## 5. 测试结果

- P0 专项测试：`10 passed`；
- 完整工作树回归：`56 passed`，连续两次通过；
- 精确 commit 干净归档回归：`56 passed`；
- Python compileall：PASS；
- JavaScript `node --check`：PASS；
- FastAPI TestClient 既有弃用警告：1 条，非阻断；
- Windows 中文用户名路径导致 pytest 缓存/临时目录异常，改用纯 ASCII 运行数据目录后全部通过，非产品代码失败。

新增回归覆盖：

- Registry 重启后保持已升级版本；
- 未显式确认的新绑定被拒绝；
- 显式新增绑定成功；
- 解除绑定成功并恢复原 Agent manifest；
- Platform Admin API 的新增确认与 DELETE 路径；
- 重复测试运行使用唯一 slug，避免全局测试库污染。

## 6. 生产状态

- 当前生产 Release：`20260911-4faed8e`；
- 当前生产误绑定仍保留，未通过手工 SQL 修改；
- migration 005 已 applied；
- P0 RC 尚未部署；
- 下一步使用独立 Release 目录和受控 systemd switch 部署 P0，然后通过 UI 解除 `campaign-agent → poster-design`，并执行重启持久性复核。

## 7. 阶段结论

**PASS。** P0 根因、修复、专项测试、完整回归和可追溯 RC 均已完成。该结论仅适用于候选版本开发验收，不代表生产修复或 Skill Registry V1 最终验收完成。
