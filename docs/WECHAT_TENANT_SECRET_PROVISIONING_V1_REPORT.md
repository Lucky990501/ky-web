# WECHAT_TENANT_SECRET_PROVISIONING_V1_REPORT

日期：2026-10-08。窗口：01 / Agent Workbench Architecture。

状态：`WECHAT_SECRET_PROVISIONING_READY`（Source implementation + offline deterministic qualification）。不是 live installation、真实连接验收或 CREATE_DRAFT execution READY。

## 1. Source / PRIMARY attestation

本轮在 2026-10-08 02:28:12Z / 02:29:03Z 对 PRIMARY 做只读 Source/工作目录核对，未读取服务器凭据、Enterprise Config 或业务数据：

- PRIMARY：`115.190.56.61`，Unix user `lucky`。
- `current`：`/opt/enterprise-agent-workbench-test/releases/20261007-dca318d-wechat-skill-test-successor-v1`。
- API / MCP / Worker WorkingDirectory 均为该 release 的 `enterprise_agent_poc`；观察 PID 分别为 729183 / 730827 / 729207。
- 正式 checkout：`/opt/enterprise-agent-workbench-test/shared/source/skill-canonical-dca318d-v1`，tracked clean。
- 两处 `SOURCE = dca318de578a9b9601ad036e1b176bc5ab702029`。
- 两处 `TREE = fc28a7666b5a1224fa39991aee574527f75834aa`。

从该精确 Source 创建独立后继分支 `codex/wechat-tenant-secret-provisioning-v1`，不是从较早的 `0bfded6ee6837e8c0908721d8ffab1035506e133` 覆盖。复用本窗口已有 clean managed worktree，原 Windows checkout 的 7 个 tracked 修改及历史 untracked 数据未混入。

本报告与实现处于同一提交。提交、push 后的 `SECRET_SOURCE` / `SECRET_TREE` 在最终交付回执中提供，可通过该分支的 `git rev-parse HEAD` / `git rev-parse HEAD^{tree}` 复核；不是上述仍运行的 PRIMARY baseline identity。

## 2. Existing backend audit / minimal extension

原 `TenantSecretReferences` 是对 scoped `runtime_environment` 引用的只读适配；凭据存在进程环境或测试注入 mapping，无安全 writer/persistent store，因此不能完成 API 写入后 MCP 跨进程读取。

本轮扩展同一个引用边界，增加 `ProtectedTenantSecretBackend` 本地持久化适配器。不增加第二套 Registry、Vault、服务、DB 表、Schema 或 Migration。

- 普通 `enterprise_configs.payload.wechat_account` 仅保存 AppID、展示名、版本化 `wechat_app_secret_ref`。
- 密文记录位于服务器受保护的 `settings.data_dir/tenant-secrets/<environment>`，每 Tenant 一个 SHA256 文件名，不是 Task workspace、Skill Registry 或 ZIP。
- AppSecret、AppID、scope、version、active/revoked 和 verification 状态都在 authenticated encrypted envelope 内；没有明文旁路 metadata 文件。
- 使用标准 `cryptography.fernet.Fernet` authenticated encryption，新增直接依赖 `cryptography>=46,<49`；本地验证实际版本 48.0.1。没有自创算法。[官方 Fernet 文档](https://cryptography.io/en/latest/fernet/)
- 密钥只从独立、受保护的文件读取；配置仅提供 `ENTERPRISE_POC_TENANT_SECRET_KEY_FILE` 路径。没有 key value 环境变量、默认 key 或自动 key 生成。
- Native protection 是 Linux/POSIX：key 位于源码和密文目录外；文件 0600、父目录无 group/other 权限；祖先不可被其他用户/group 写入，无 symlink；owner 为 root 或当前服务 UID。cipher directory 私有 0700，record 0600。缺 key、错误权限、损坏密文均 fail closed。
- API/MCP 需要各自加载同一个环境的受批准 persistent data path 和独立 key path。各环境应独立 key + storage；不得把 Production key 注入 Test。
- Windows 本轮仅使用显式 synthetic codec loader；不声称完成 Windows native ACL 或 PRIMARY Linux filesystem acceptance。

新 reference 精确字段（无 secret value）：

```json
{
  "provider": "wechat",
  "tenant_id": "<server authenticated tenant>",
  "environment": "<server settings environment>",
  "name": "wechat-account",
  "version": 1
}
```

`provider=wechat` 是已有 reference 的 backend 类型，不是第二个 Secret Registry。旧 `runtime_environment` 引用保留 read-only 兼容和 redaction 测试，但不能证明 connected，也不能单独授权 CREATE_DRAFT；不得回退到 ambient/global 微信环境变量。

## 3. PROVISION / ROTATE / REVOKE / RESOLVE

- PROVISION：认证 + 当前 DB permission 后写加密记录，再写普通配置 reference；initial verification 为 `unverified`。
- ROTATE：新 AppSecret 生成单调递增 version，重置为 `unverified`；旧 reference 无法解析。
- SAVE 空/空白/省略 AppSecret：已有有效 protected reference 下保持原 secret；同 AppID 保持 version。没有可保持的旧 secret 时拒绝，不静默产生可用凭据。
- AppID 改变且保留旧 secret：生成新 version 并清除 connected 状态；仍需后续服务端重新验证。
- REVOKE：写入新 version 的 authenticated revoked tombstone（secret=null），然后移除账号普通配置。旧 reference 和未消费 lease 均不可再使用。
- RESOLVE：Tenant/Environment reference 校验先于 backend lookup，每次重新读取当前加密记录；无 API 内存、localStorage、共享明文 env 或 secret cache。
- Lease：仅服务器内部对象，repr redacted，最长 60 秒。消费前重新检查 version/revoke；CREATE_DRAFT lease 同时重查签名 token、Task running、现行 action permission、账号 reference、connected 和 network gate。注入只保留 OS 必需项及当前账号 credential，异常固定码且清空注入 dict。
- 完成注入后的既有子进程无法通过 Python dict 清空追溯擦除其内存；撤销保证新解析/未消费 lease 失败。本轮没有启动任何 credential-bearing 微信子进程。

## 4. API for 03 personal center

复用 `main.current_user` 的 signed session、当前用户/tenant DB identity、account enabled、credential version 校验。管理权限复用当前 `enterprise_admin`；普通 member 不升级，platform_admin grant 本身不替代 tenant-config role。写入事务内再次查询并锁定当前用户，不能使用客户端提交的 tenant/role/environment。

| Method / path | 功能 |
| --- | --- |
| GET `/api/v1/profile/wechat-account` | AppID、name、`app_secret_configured`、verification status、secret version；不返回 secret/ref/ciphertext |
| PUT `/api/v1/profile/wechat-account` | SAVE / PROVISION；新 AppSecret 自动 rotate；空值保持已有有效 secret |
| POST `/api/v1/profile/wechat-account/secret/rotate` | 必须提交新的非空 AppSecret |
| DELETE `/api/v1/profile/wechat-account` | REVOKE / unlink |
| GET `/api/v1/profile/wechat-account/verification-status` | 只读状态 |

SAVE 只接受 `wechat_app_id` / `app_secret` / `account_display_name`；ROTATE 只接受 `app_secret`。拒绝额外字段、重复 JSON key、非法 body、跨 Origin 写入。手工 closed JSON 解析不使用会回显 input 的默认模型错误；底层异常映射固定代码，不返回 credential-bearing exception。既有 main 全局 HTTP error envelope 保持不变。

没有 UI、没有公开 RESOLVE Secret API/MCP tool、没有测试连接 API 实现、没有 connected 写入端点。Secret 审计仅记录 operation、tenant、environment、provider、actor、version；不写入 key、secret、ciphertext、body 或 Task result。

## 5. CREATE_DRAFT connected gate

所有条件必须同时满足，否则 `BLOCKED_NOT_EXECUTED`：

1. 当前 Tenant 账号配置合法。
2. AppID 存在且符合原账号格式。
3. 当前环境/version AppSecret reference 可解析。
4. 受保护加密记录的 `verification_status == connected`。
5. 现有 Runtime token / DB context / Agent Revision / 精确 published Skill binding / capability scope 有效。
6. 真实 Task input 是用户明确创建草稿指令（沿用既有 imperative intent contract）。
7. 服务器 `ENTERPRISE_POC_WECHAT_NETWORK_ALLOWED=true`；默认 false，不是前端字段。

普通 config 的 connected/verification 字段不能认证连接；profile SAVE 拒绝这些字段。任意更换 AppID/Secret 清除 connected。V1 不存在客户端或普通配置 mint connected 的路径；未来只能由正式服务端 WeChat test-connection 成功后的受信任转移写入。本轮测试的 connected 由 synthetic backend fixture 模拟，不是实际微信成功。

实际 `CREATE_DRAFT ActionRegistration.enabled=False`、固定 `--check` 保持不变。即使 synthetic connected + environment network allow + permission PASS，dispatcher 仍拒绝执行。permission receipt 不是网络执行证明，没有 draft_media_id。

PREPARE 完全不依赖账号/secret/connected/network；原 Skill Revision Runtime、Controlled Action、SKILL_ONLY_TEST_QUALIFIED、canonicalization、Agent/MCP dispatch 保留。

## 6. Deterministic evidence

仅本地 targeted unittest，marked 临时 SQLite/密文/key、fake key loader、mock Runtime/process、mock WeChat workflow。Settings 在 import 前被 fixture stub，未加载 `.env` 或真实 Provider credential。没有 Full pytest、Stage 2、Candidate build。

| Suite | PASS | failed/errors | skipped |
| --- | ---: | ---: | ---: |
| `verify_wechat_secret_provisioning.py` | 60（29 new + 31 inherited dispatch） | 0/0 | 0 |
| `verify_skill_only_test_qualification.py` | 91（qualification/controlled/dispatch） | 0/0 | 0 |
| `verify_wechat_skill.py` | 66 | 0/0 | 0 |
| `verify_wechat_skill.py --runtime-tests` | 29 | 0/0 | 0 |
| `verify_skill_revision_canonicalization.py` | 30 | 0/0 | 0 |

合计 276 suite executions；其中 31 dispatch 与第一套重叠，去重 245 tests。raw skips=0、unresolved skips=0（仅上述 scoped suites）。不是 Full pytest 或当前 Source 的 PRIMARY live acceptance。

新证据覆盖：encrypted provision、API GET 不返回 Secret、实际 profile API 写入后的独立 OS process read、实际 `PlatformMCPService.wechat_action_permission` 跨进程执行、临时 credential hook、rotate/revoke 后跨进程失效、旧 ref/lease 失效、TTL、Tenant/Environment隔离、权限拒绝、connected伪造拒绝、重复/非法 JSON 无回显、cross-origin拒绝、cipher tamper/transplant、AppID/rotation清除验证、无key拒绝、DB写失败时旧ref仍拒绝、消费前Task取消拒绝、PREPARE无credential、enabled=False保留。

独立进程证据使用磁盘 DB + 密文 + 独立 fake key 文件；进程不继承微信环境变量，也不接收 AppSecret。只输出 synthetic secret SHA256 / sanitized permission receipt；不是通过 API 返回 Secret。

调整旧 WeChat tests 是将「环境变量 alone 即可授权」的预期改为拒绝，保留其 redaction/lease 隔离验证；positive connected permission 在新增 tests 验证。旧 frozen-source test 保留 Runtime/control/qualification/Skill 不变约束，只移除本轮明确授权修改的 Secret/connected 文件比较。

## 7. Changed files

- `enterprise_agent_poc/app/tenant_secret_backend.py`：persistent protected adapter。
- `enterprise_agent_poc/app/tenant_secret_reference.py`：versioned reference、lease/revoke/TTL、connected resolve。
- `enterprise_agent_poc/app/wechat_account.py`：permission、ordinary config/ref、provision/rotate/revoke/status/audit。
- `enterprise_agent_poc/app/wechat_account_api.py`：closed profile API。
- `enterprise_agent_poc/app/main.py`：注册既有 auth 下的 router。
- `enterprise_agent_poc/app/settings.py`：key path / network allow default + runtime config name identity。
- `enterprise_agent_poc/app/platform_mcp/service.py`、`app/skill_dispatch_config.py`：API/MCP相同 backend配置与 gate。
- `enterprise_agent_poc/app/wechat_action_contract.py`：connected / environment gate、消费前复核；不执行上传。
- `enterprise_agent_poc/pyproject.toml`：标准 encryption dependency。
- `enterprise_agent_poc/tests/test_wechat_secret_provisioning.py`、`scripts/verify_wechat_secret_provisioning.py`：本轮回归。
- `enterprise_agent_poc/tests/test_wechat_action_contract.py`、`tests/test_skill_only_test_qualification.py`：适配新安全预期，保留既有隔离/资格约束。
- 本报告。

未修改：Skill ZIP/Skill/Prompt/Agent execution/Runtime/Schema/Migration/Production/前端。Native ZIP SHA仍为 `4a140c878ae7057583089a4410cd1a23c5c95664988e6dd9700f75ed51d3b18c`；Python311 lock SHA仍为 `3a5f482475c7cafe660df2d171f369a32832ac91e12e9b0ddffde5c5d6ac8abe`。旧 sealed identities 和历史 approval 不改写。

## 8. Handoff / limitations

- 03 可按上述 API 做个人中心 UI；有配置不代表 connected，可显示 configured/unverified。管理 role 必须由现有正式管理机制授予，不能由 UI 自行升级。
- 后续真实 test-connection 必须另立授权/预算并建立 trusted connected transition；当前无微信调用及 token cache。CREATE_DRAFT uploader/registration enable 仍是后续独立任务。
- 06 若安装本 Source，需新 Source-bound Test contract、API/MCP dependency acceptance、Linux private storage/key 安全安装与持久化验收。不得复用旧 Source seal 来声称本 Source 已 live qualified，也不把 API credential 注入模型/通用 Skill child 环境。
- File + DB 非 distributed transaction。写密文先于写 DB：若 DB 更新失败，旧 ref 立即失效，返回 sanitized error，**不 rollback 到旧 secret**；确定性 test 已证明。恢复采用 authorized REVOKE 后重新 PROVISION，不能手改 version/回滚 ciphertext。此模型优先 fail closed，可能短暂不可用。
- Key 文件丢失/变化、权限不满足、密文损坏：fail closed；本轮未实施 master-key rotation、备份/restore、OS ACL install 或硬件 Vault。不能从损坏 record 静默重置 version。
- 同一 Linux服务 UID对密钥/目录的访问是信任边界，authenticated encryption 不替代 OS/service隔离；实际安装与 threat review 需独立验收。
- 本轮现有本地 FastAPI TestClient 发出 httpx deprecation warning；没有跳过测试，也未顺便更改 transport dependency。

`PRIMARY_MUTATIONS=0` · `PRODUCTION_UNCHANGED` · `WECHAT_CALLS=0` · `PROVIDER_CALLS=0` · `IMAGE_CALLS=0`。

完成后 STOP；无 deploy / switch / restart / live DB mutation / admin或Agent创建。
