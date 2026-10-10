# 微信公众号个人中心配置 UI + API V1

日期：2026-10-08，Asia/Shanghai。

状态：**WECHAT_PERSONAL_CENTER_CONFIG_READY**（功能源码、mock 确定性测试与本地浏览器验收）。不是 PRIMARY 安装或真实微信连接验收。

## Source / inheritance

- 独立 clean branch：`codex/wechat-personal-center-config-v1`，worktree `.wechat-personal-center-config-v1/source`。
- 精确基线 Source：`cb17abb6b2e2fea888c1b5603f169a99a235191a`，Tree：`9e0006619a29aafbbac90dffbb0f50003444153c`。开始前 fresh 远端 `origin/codex/wechat-tenant-secret-provisioning-v1` 与该 commit MATCH。
- 基线继承链：Skill Dispatch `b0e96df` → Controlled Action `0bfded6` → Qualification `4ca2133` → Canonicalization `dca318d` → Secret `cb17abb`；祖先检查通过。没有从旧 Source 覆盖新功能。
- 本报告与功能代码一起提交。`PERSONAL_CONFIG_SOURCE / PERSONAL_CONFIG_TREE` 由 commit/push 后正式 Git 回执给出，可 fresh 用本分支 HEAD / HEAD^{tree} 复核；不把基线 SHA 冒充最终功能 SHA，也不制造文件内自引用 SHA。
- 原 checkout 的 7 个 tracked dirty 文件和历史未跟踪文件保持原样，未暂存、未清理、未混入本分支。

## UI

个人中心 `profileV2` 增加“应用配置 → 微信公众号”，复用现有 profile-card 风格，显示四种状态：未配置、已配置未验证、连接正常、验证失败。企业管理员可保存、修改、测试连接和二次确认解除；普通成员只读，API 仍重新校验当前数据库管理权限。

字段：选填公众号名称、必填 AppID、首次必填 AppSecret。AppSecret 为 password input，保存请求发出时立即清空输入；保存后显示固定 `******** 已配置`，修改时输入框为空，留空保留旧凭据。没有查看／复制 Secret、Access Token 输入、localStorage/sessionStorage Credential 或 User Profile Credential 字段。AppID 展示摘要做 mask，编辑时使用普通账号标识全值。

表单使用可见 label、关联帮助文本、键盘 focus、44px 操作控件及明确 loading / success / failure。移动端单列、按钮换行；375px 无新增横向溢出。退出页面后的旧异步响应通过 DOM 所有权检查不能覆盖新页面。

公众号 Agent 使用稳定 slug `wechat-official-account-writing` 识别，不依赖展示名。未配置时显示“请先在个人中心配置微信公众号，然后再创建草稿。”与 `/profile` 的“去配置”入口。该提示不替代后端授权，不修改聊天提交／Runtime／Skill Action 的内部错误码或门禁。

## Config / Secret Contract

继续使用 `enterprise_configs.payload.wechat_account` 的 AppID、展示名与版本化 Secret Reference。没有新表、Schema016、Migration 或第二套 Secret 服务。管理作用域由当前签名 session 的 Tenant 和服务端 environment 决定，客户端不能提交 Tenant／Environment／role 或认证状态。

cb17 的正式 PROVISION / RESOLVE / ROTATE / REVOKE 被保留：新 Secret 或 AppID 改变生成新版本并变为 unverified；相同 AppID 下的空 Secret 保留已有值及版本。仅修改名称不改变凭据验证。解除使用 REVOKE，不删除 Agent、Skill、历史文章或 Task。

AppID/name/Secret 做 trim，空选填名称使用“微信公众号”。普通数据库只保存非 Secret Config + Reference；Secret、验证状态和证明时间仍位于上游 protected encrypted record。GET 保留原 API 字段兼容性，并增加安全 `verified_at / verification_error_code`，不返回 AppSecret、Reference、ciphertext 或 Access Token。`secret_version` 是上游原有版本号字段，UI 不展示内部技术版本。

对原 encrypted backend 的唯一生命周期扩展是 trusted server `record_verification`：以当前 Tenant／Environment／AppID／Reference version 做 CAS，记录 connected 或 failed、UTC 时间与固定错误码。兼容旧无验证时间记录；rotation 后旧验证 metadata 不延续。没有新的 provision/store 实现，也没有公开 connected=true setter。

该扩展涉及加密 record 的可选 metadata／failed 状态，不是 PostgreSQL Schema/Migration。新读器兼容 cb17 旧记录；cb17 的旧严格读器不能读取含这些新字段／状态的记录，会 fail closed。因此后续 API/MCP 必须安装匹配的新读器 Source，不能在写入验证结果后只切回旧 Secret reader。本轮没有证明跨版本加密记录降级兼容或授予 rollback 安全结论，后续 Source-bound 安装／兼容性 review 需明确此边界。

## API / Test Connection

保留 GET / PUT / DELETE `/api/v1/profile/wechat-account` 与 POST `/secret/rotate`。新增 POST `/api/v1/profile/wechat-account/test-connection`，只接受空 JSON `{}`。沿用 signed session、current-user 检查、closed JSON parser、重复 key／超限／跨 Origin 拒绝、当前 DB enterprise_admin 管理权限。

流程：读取当前 Config → 用同一 `TenantSecretReferences` RESOLVE → SecretLease 临时作用域 → credential-only tester → 再校验当前用户管理权限和 Config identity → CAS 写入同一加密记录 → 脱敏 audit／状态 response。网络操作不持有 DB 写锁；验证期间发生 rotation、AppID 更换、revoke 或 role 撤销时，旧结果不能认证新配置。

Credential adapter 沿用 Native Skill 的 `GET https://api.weixin.qq.com/cgi-bin/token`、`grant_type=client_credential`、AppID+AppSecret 合同，使用后端固定 HTTPS 客户端。确定性测试从实际 Native Skill AST 对照该 grant 合同，未更改 Skill Source／ZIP。适配器不启动 Native upload runner，不调用素材或 draft 端点，不跟随 redirect、不读取代理环境、不记录 credential-bearing URL、原始响应或异常文字。只检查返回的 Token 存在并立即丢弃，不返回／持久化 Token，不要求用户填写 Token。超时为每次 I/O 10 秒，无自动重试。

错误固定分类：AppID/AppSecret 验证失败、IP 白名单、微信服务不可用、连接超时。原 main 安全 error envelope 增加这些配置服务错误的中文映射；未改变其他业务错误分类。审计仅含 Tenant、environment、actor、version、状态和固定错误码。

沿用服务端 `wechat_network_allowed` 门禁，默认关闭。未批准网络许可时验证在请求前 BLOCK；本轮所有正／负验证使用注入 mock tester 或 fake HTTPS connection，不设置 live 网络许可。

页面只显示 IP 白名单操作提示，没有可信出口 IP 配置时不猜测、不硬编码、不把 WeChat 原始 errmsg 展示出来。

## CREATE_DRAFT gate / preserved boundaries

账号已配置不等于 connected。上游 require_connected 仍从当前 authenticated encrypted record 读取验证状态，并保留 Secret Reference、签名 Task、Action Permission、现行 Agent/Skill Revision、明确用户上传意图及 network allowance 门禁。

**CREATE_DRAFT ActionRegistration.enabled=False 保持不变。** connected 仅满足账号验证条件，不能全局启用创建草稿。PREPARE 不依赖凭据。Skill Dispatch、Qualification、Canonicalization、Skill Source/ZIP、Runtime、integrations、Schema/Migration 与基线 diff NONE。

## Deterministic tests

| Suite | Result |
| --- | --- |
| `verify_wechat_personal_config.py` | 23 passed / 0 failed / 0 errors / 0 skipped |
| inherited `verify_wechat_secret_provisioning.py` | 60 passed / 0 failed / 0 errors / 0 skipped（含 31 个 Dispatch/gate tests） |
| `node --test tests/workbench_routes.test.cjs` | 90 passed / 0 failed / 0 skipped |
| local Playwright Chrome fixture | Frontend 1–9 PASS，1440px / 375px PASS |
| static checks | 7 Python files compile + Python 3.11 AST syntax PASS；workbench.js / browser test syntax PASS；diff-check PASS |

Backend 1–12 均有新／继承测试：首次 Provision、GET 不回显、更新 AppID、空 Secret 保留、rotation、revoke、Tenant 隔离、未登录拒绝、DB 越权拒绝、connected 伪造拒绝、凭据变化失效、CREATE_DRAFT 无配置阻断。另覆盖错误类别、网络关闭、意外异常脱敏、验证中 rotation/revoke/权限撤销、状态 CAS、日志 audit 脱敏和 Token endpoint 合同。

浏览器验收覆盖：首次页面、真实 DOM 表单提交到 mock API、保存 mask、刷新无回显、空 Secret／rotation payload、测试 loading/success/failure、解除取消及确认、Agent 去配置、普通成员无管理按钮、移动端溢出与页面 JS errors=0。响应 fixture 不含 Secret，浏览器 storage 未写 Secret。没有把模拟 connected 当作真实微信连接成功。

测试环境：本轮独立 Windows Python 3.12.14 venv，合成 SQLite/加密记录/fake key loader。cryptography 48.0.1、MCP 1.30.0、FastAPI 0.142.4、httpx 0.28.1、项目锁定 Codex SDK 0.147.0。依赖仅安装到本轮 workspace 隔离目录，没有改服务器或全局 Python。Windows native Secret ACL 不是本轮证明，真实部署仍需上游 Linux private key/path 验收。

保留中间失败归因：首次 MCP import 缺 pywintypes 路径、sandbox Temp 无法打开 SQLite，之后上游回归曾有 2 项 dependency errors（SDK 缺失／独立子进程未加载依赖）。通过独立 fixture root + 正常 venv/.pth + 安装锁定 SDK 解决，未修改旧测试断言。最终成绩以上表为准。存在继承的 Starlette/httpx TestClient deprecation warning，未升级 transport 或隐藏失败。未运行 Full pytest、Stage 2、Candidate 或 PRIMARY live qualification；没有 release-critical live skip 被宣称关闭。

## UI evidence

截图位于 Codex visualizations 的 `wechat-personal-config-v1` 目录，已人工查看：

- `wechat-unconfigured-1440.png`：SHA256 `bdb1f4759d31df55aaef8df2404d1a00ab758d73deb1627ce77c96425284d345`。
- `wechat-connected-1440.png`：SHA256 `0a35199d729b87c3053e62ed708ad5eae634a8425bdf26ced9c76fd79caf5450`。
- `wechat-unconfigured-375.png`：SHA256 `dc71036a2300fd9e80ae29cf78612dca4bcbdb9a5cc4a47fb05b240349a2ca75`。

截图为合成账号／mock 状态，未包含真实凭据。旧 `profile` 与当前入口 `profileV2` 双实现是既有结构，本轮只接入当前入口，没有扩大页面清理。新卡片样式隔离，不改 Sidebar／登录页／Word／积分等布局或逻辑。

## Changed files

1. `enterprise_agent_poc/app/main.py`
2. `enterprise_agent_poc/app/wechat_account.py`
3. `enterprise_agent_poc/app/wechat_account_api.py`
4. `enterprise_agent_poc/app/tenant_secret_backend.py`（仅 trusted verification transition）
5. `enterprise_agent_poc/app/wechat_connection.py`
6. `enterprise_agent_poc/app/static/workbench.js`
7. `enterprise_agent_poc/app/static/workbench.css`
8. `enterprise_agent_poc/tests/test_wechat_personal_config.py`
9. `enterprise_agent_poc/tests/workbench_routes.test.cjs`
10. `enterprise_agent_poc/tests/wechat_personal_config_ui.playwright.cjs`
11. `enterprise_agent_poc/scripts/verify_wechat_personal_config.py`
12. `WECHAT_PERSONAL_CENTER_CONFIG_V1_REPORT.md`

## Subsequent PRIMARY plan / current calls

后续单独核对新 Source-bound Test authorization、API/MCP 依赖、protected Test key/path 与 network permission。不能复用旧 Source seal 宣称新源码已安装／qualified。

获准后：登录配置管理员测试用户 → 个人中心填写 Test AppID/AppSecret → 保存并检查 redaction/reference → 单独授权真实测试连接 → 检查当前版本 connected → 待 CREATE_DRAFT executor 的独立授权与实现完成后执行真实 draft Smoke → 核对对应微信草稿箱和正式 Task/Run/receipt。当前 CREATE_DRAFT 的 enabled=False 仍保留，不能直接跳到 draft Smoke。

**WeChat Calls=0；Provider Calls=0；Image/Text Calls=0；Retry=0。PRIMARY unchanged；Production unchanged。**

未连接服务器、读取真实 key/Secret、部署、重启、写 live DB、执行 migration 或调用微信。本轮只做源码、mock 验证、commit/push；原历史证据和 dirty 内容保留。
