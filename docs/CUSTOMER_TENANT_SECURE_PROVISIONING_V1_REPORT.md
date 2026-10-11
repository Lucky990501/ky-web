# 客户 Tenant 安全开通候选

日期：2026-10-11。01 交接总控、02、06。

状态：`CUSTOMER_TENANT_PROVISIONING_CANDIDATE_READY`。FIRST_FAILURE_POINT：`NONE`。

工具在默认只读 dry-run 下验证精确身份，在单独获批执行时创建零积分、无 Agent 执行权限的客户 Tenant 和企业管理员。30 项真实 PostgreSQL 隔离测试通过。本状态仅表示代码候选可供评审，未签发 Production 开通授权，也未建立真实客户凭据交付通道。

## Source 和修改文件

直接 Parent / 冻结 Application Source：`562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3`。

Application Tree：`e5205965e57fce6ce2b65dd747a110d00f7f1b1a`。

分支：`codex/customer-tenant-secure-provisioning-v1`。工具后继的 Source/Tree 由本报告所在 Commit 的 `git rev-parse HEAD 'HEAD^{tree}'` 确定，最终交付同时提供 fresh remote MATCH；报告不写入会改变自身 Commit 的自引用 Hash。

新增四个文件：

- `enterprise_agent_poc/scripts/provision_customer_tenant_v1.py`。
- `enterprise_agent_poc/tests/test_customer_tenant_provisioning_postgres.py`。
- `enterprise_agent_poc/tests/run_customer_provisioning_pg16.py`。
- 本报告。

工具文件 SHA256：`899c915cc31508124ae418d11bd38455a373f780d6d27ef5ce675329da4ae891`。

app/store.py、product_store.py、auth.py、main.py、登录 API、Migration001–015、服务配置、Guard、其他工作树均未修改。没有修改 provision_test_tenant.py。

## 现有机制核查

已有 provision_test_tenant.py 是测试 Fixture 工具，固定测试身份、额度1000、Agent enabled，还创建测试 workspace，不能用于正式客户。

ProductStore.create_user() 会在积分账户不存在时补200。新工具先直接创建 balance=0 的账户并在同一事务写入用户，不调用这一辅助方法。客户后续通过现有正式成员 API 新增成员时，其 ON CONFLICT DO NOTHING 保持已有零积分。

ProductStore._seed_agent_catalog() 对三个当前 Catalog Legacy Agent 使用 INSERT enabled ON CONFLICT DO NOTHING。工具对数据库中**所有当前 Legacy 模板**显式创建 disabled 实例；Catalog 重复初始化不覆盖它们。Productized 模板不创建 Instance，不发布、不启用。

复用 app.auth.hash_password()、POCStore.connection()、现有 Migration checksum 和 rollback_preflight.check_history()、正式 release_identity()。不调用 initialize()/seed_demo_data()/migrate.up()/migrate.status()，不新增公开注册入口。

## 使用方法

工具要求 Linux 非Root操作账户。真实客户五个参数必须来自正式审批，不能替换为示例测试身份。数据库连接由现有 `ENTERPRISE_POC_DATABASE_URL` 提供；工具不输出该值。

默认模式及显式 --dry-run 均只读：

```text
PYTHONDONTWRITEBYTECODE=1 python -B scripts/provision_customer_tenant_v1.py \
  --tenant-id <批准的稳定独立ID> --tenant-name <客户名称> \
  --admin-email <客户管理员邮箱> --admin-display-name <管理员姓名> \
  --operation-id <新UUID> --approval <批准的操作文件> \
  --enterprise-config <客户已确认的普通配置JSON> --dry-run
```

省略 --enterprise-config 时只保存空对象；不存在默认继承。普通配置仅接受现有品牌与公开规则字段：brand_name、slogan、brand_style、primary_color、secondary_color、required_rules、forbidden_claims。Wechat/Secret/password/token配置不能通过此入口提交。

正式获批后，操作人员通过已批准接收程序启动工具，显式传递 --execute、--credential-fd 和 --credential-ack-fd。两个描述符必须由该程序通过安全的描述符继承传入，不能用文件、TTY、stdin/stdout/stderr或日志重定向替代。密码不作为输入参数，不进入环境变量。

## 精确操作授权和身份

Production 操作文件必须位于 `/etc/enterprise-agent-customer-provisioning-v1/<operation_id>.json`，目录 root-owned 0755，文件 root-owned 0444。文件只含非敏感审批身份。执行程序本身拒绝Root UID；审批文件的安装/签发属于未来单独授权，本轮未执行。

文件必须绑定以下精确字段，未知或缺失字段拒绝：

- version=`CUSTOMER_TENANT_SECURE_PROVISIONING_V1`，scope同版本，environment=production，production_authority=true。
- operator_uid、最长24小时 expires_at、五个业务参数以及确认配置的SHA256。
- application：冻结 Source/Tree、正式 base/release_id、archive_sha256/manifest_sha256。通过既有 release_identity 验证全部选定文件与归档，并验证 fresh release-current 指向。
- tooling：独立工具 checkout 的精确 Git Source/Tree，必须tracked且无tracked dirty。
- code_pins：工具脚本、app/auth.py、store.py、product_store.py 的精确SHA。后三个 helper 必须与正式 Application 文件一致。
- database：current_database、current_user、实际 host 地址、port、server_version_num、database_oid、PostgreSQL system_identifier。
- schema_sha256：工具只读结构快照的SHA256，覆盖table/RLS、policies、columns、constraints、indexes、triggers、functions、extensions、Migration ledger。
- legacy_agent_ids：精确当前 Legacy 模板清单。
- receipt_dir：操作账户拥有的0700目录，必须位于Tooling Source与Application Release之外。
- credential_handoff：PRIVATE_ANONYMOUS_PIPE_V1，以及接收进程PID、UID、可执行文件SHA、cmdline SHA。

没有在本候选中签发Production操作文件。它不是通用管理员授权API，不授予平台管理员能力。

隔离验证使用当前用户拥有的0400文件、scope=ISOLATED_CUSTOMER_PROVISIONING_TEST、production_authority=false，必须绑定真实合成Git身份和 `/private/tmp/ky-web-stage1-postgres.*` 自身PGDATA/标记，且TCP禁用。该分支不能加载为Production授权，也不能用于当前d8a Test数据库。

## 单事务写入和结果验证

默认 dry-run 首先采用 REPEATABLE READ READ ONLY 连接，验证数据库身份、完整Schema Pin及Migration001–015真实名称/checksum/时间；不创建migration ledger。验证poc_api_key可NULL、管理员状态列、主键/全局邮箱唯一约束、Tenant/Agent外键及当前模板清单。租户ID已存在或任意Tenant已占用该邮箱（大小写归一化）则拒绝。

执行使用短 SERIALIZABLE 事务，保留全局 advisory lock、模板共享锁及Tenant/User写入锁，lock_timeout=3秒、statement_timeout=10秒。重新核对目标后仅创建：

| 表 | 精确新记录 |
| --- | --- |
| tenants | 1条，poc_api_key=NULL |
| enterprise_configs | 1条，客户确认配置原样保存 |
| credit_accounts | 1条，balance=0 |
| users | 1条，enterprise_admin / enabled，独立随机密码哈希 |
| tenant_agent_instances | 每个当前Legacy模板1条，disabled，Revision/Instance override为空 |

管理员ID由operation_id和tenant_id确定性生成。写入后验证全部字段、准确行数、零积分、无Platform Admin及disabled集合，并在数据库端比较所有其他业务行的SHA摘要。其他Tenant内容/密码不返回工具；触发器在范围外创建记录会导致回滚。

审计不增加execution_events写入，不扩大上述五表范围。工具以O_EXCL创建独立0600 append-only收据，INTENT持久化并fsync后才开始写入，完成后追加COMMITTED。收据没有明文密码或密码哈希。

同一operation_id的收据不可重用；既有Tenant/邮箱也拒绝重复。失败和连接/commit结果不明确保留NOT_CONFIRMED，不自动重试或删除记录。必须只读确认事实后，再审批新的operation_id；审计写入在数据库commit后失败时记录COMMITTED_RECEIPT_PENDING，不能宣称已回滚。

## 凭据一次性交付

密码由 secrets.token_urlsafe(32) 生成，调用现有PBKDF2哈希。没有固定客户密码，不读取或复用其他用户密码。

前向凭据管道及ACK管道都必须由内核证明为独立匿名pipe，方向正确，且不是标准输入输出的别名。工具核对同一接收进程的UID、可执行文件、cmdline和两端inode；在提交前再次核对审批与接收端。

接收程序收到凭据后，必须完成正式批准的安全交付保管，再通过ACK管道返回 `operation_id` 和原始凭据包字节的SHA256。工具最多等待5秒；缺失、错误、超时、进程退出或审批撤销均返回CREDENTIAL_HANDOFF_BLOCKED或相应精确审批拒绝，并回滚整个事务。仅发送字节不视为交付成功。

批准的接收程序必须保证不输出密码到终端/日志，不把测试回传程序用于客户。隔离Receiver仅将合成密码经另一私有管道回传测试进程内存用于验证；不是已批准的客户交付服务。

ACK证明已批准程序按其协议确认接收，不是客户已登录或已完成改密。若数据库commit随后异常，不再次交付/创建，先结合收据与数据库只读取证。临时密码不会自动过期或强制首次改密；现有Schema没有该字段，客户应使用现有个人中心改密。已验证改密后旧密码和旧会话失效。

## Dry-run和隔离回归结果

隔离 dry-run：status=dry_run_pass、read_only=true、database_writes=0、原Tenant数量2、计划1/1/1/1/3条，credit_balance=0、agents=disabled、poc_api_key=NULL。无接收通道时明确 execution_ready=false、credential_handoff_status=CREDENTIAL_HANDOFF_BLOCKED；未生成密码或收据。此数量是合成数据库结果，不冒充Production数量。

最终：**30 passed / 0 failed / 0 errors / 0 skipped**，2项既有依赖弃用警告。使用实际PG16.6 / pgvector0.6.0、真实数据库事务、真实OS管道和原登录/成员/知识/会话API，没有Mock成功结果代替权限或事务。

验证覆盖用户20项要求：正常五表创建与配置归属、密码哈希正确、管理员role/status、积分0、全部Legacy disabled和重复初始化、重复操作/邮箱冲突、本人配置/成员/知识入口、跨Tenant文件/会话/成员/配置拒绝、平台权限拒绝、改密撤权、未授权Agent执行拒绝、全事务失败回滚及新审批重试。

额外验证：并发邮箱确认仅一方commit；Schema/Source/Code Pin/DB/环境/到期/文件模式错误拒绝；缺失或不安全handoff拒绝；真实PG四个阶段的触发器失败；范围外知识写入及强制enabled触发器回滚；真实CLI执行不泄露密码；事务中receiver退出、审批到期，以及错误operation/digest/缺失ACK不commit。

实际入口：独立Linux快照中 `python -B tests/run_customer_provisioning_pg16.py --tb=short`。Launcher限定非Root、fresh `/var/tmp` 源快照、PG16.6、私有Unix socket、TCP禁用。每个测试独立数据库，完成后删除自身数据库、停止PG及接收进程；日志保留。

最终JUnit：`/var/tmp/customer-provisioning.nSeOqw/customer-ack-final.xml`。PG日志：`/private/tmp/ky-web-stage1-postgres.customer-q6c33_om/postgres.log`。这是组件与真实PG权限证据，不是Production开通或Native安装验收。

## Production执行需要的正式授权

02/总控确认真实客户参数与配置，并批准本候选精确Tooling Source/Tree及当前Application绑定。若02已改变Production Source，本工具冻结的562f身份必须重新评审绑定，不能自动接受未来Source。

06在单独获批只读范围提取 fresh release-current、Manifest/Archive identity、完整数据库/Schema摘要、Legacy集合及原Tenant数量，核对数据库角色能完成所需只读检查与受控事务。权限不足时拒绝，不自动GRANT。

另行正式批准单次开通、受保护操作文件、收据目录和安全接收程序及其交付流程。不存在已批准且活跃的交付机制时禁止execute，保留CREDENTIAL_HANDOFF_BLOCKED。在既有Release/Guard的数据范围内审查新客户记录，不关闭或跳过Guard，不修改服务配置。

先dry-run并审核清单，再由正式操作人员显式execute。数据库commit后不提供自动删除Tenant的rollback命令；已发生成员/文件等后续业务写入时不能盲目删户。异常只读核对、凭据补救或清理须独立审批。

本轮没有Root操作、Production/Test数据库写入、服务重启、Source Switch、Provider/WeChat/Image调用或客户账户创建，也未联系或干扰02。旧Tenant数据和既有工作树保留。
