# 客户 Tenant 开通工具的 Release 指针修复

日期：2026-10-11。01 交接总控和06。

状态：`CUSTOMER_TENANT_POINTER_FIX_CANDIDATE_READY`。代码及隔离回归已完成；这不是正式客户开通授权。唯一业务代码改动是把 Production `release-current` 的比较目标从 Release 根目录改为已完整验证的 Application 目录。Manifest、Archive、Source/Tree、Code Pin、数据库及审批校验均保留。

## 基线和后继

- 直接 Parent：`da164f9903cc07ef0aa1782d355478e45c0e4416`。
- Parent Tree：`fb87295a605ded5ddc48df9eed1a96501299df38`。
- Production Application 绑定保持：Source=`562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3`；Tree=`e5205965e57fce6ce2b65dd747a110d00f7f1b1a`。
- 后继分支：`codex/customer-tenant-provisioning-release-pointer-fix-v1`。新 Source/Tree 是本报告所在 Commit 的 `HEAD` / `HEAD^{tree}`；最终交付提供其精确值及 Fresh Remote MATCH，避免在报告内写入改变自身 Hash 的自引用。

旧 da164 Commit/Tree 不改写。新候选不是 Application Release，不改变正式 Application 身份。

## 返回合同和最小修复

`scripts/rollback_preflight.py:release_identity()` 先构造：

```python
directory = base / 'releases' / rid
source = directory / 'enterprise_agent_poc'
```

该函数验证受控路径、Manifest合同、批准摘要、Archive SHA、Archive Source Commit、成员路径和完整文件集、安装文件内容/权限及无未批准文件，最后返回 `(source, manifest)`。

因此 `app_root` 本来就是 `releases/<release_id>/enterprise_agent_poc`。旧 `app_root.parent` 错误地要求运行指针指向外层Release目录。修复后：

```python
need((Path(app['base'])/'release-current').resolve() == app_root,
     'PRODUCTION_RELEASE_CURRENT_MISMATCH')
```

没有删除校验或改成前缀/任意子目录匹配。指向Release根目录和其他Release仍严格拒绝。

修改三份文件：

1. `enterprise_agent_poc/scripts/provision_customer_tenant_v1.py`：一处比较目标修正和返回合同注释。
2. `enterprise_agent_poc/tests/test_customer_tenant_provisioning_postgres.py`：追加11项定向回归；原30项保持。
3. 本报告。

app/auth.py、store.py、product_store.py、main.py、Migration、Release Gate、Manifest合同、原Launcher、登录、Agent启用、积分账户逻辑均没有改动。主工作区及其他候选Dirty内容未修改或提交。

## 真实目录和安全门禁回归

全部使用临时Linux真实目录、软链接、真实tar.gz、Manifest文件和正式 `release_identity()` / `load_context()`，没有Mock路径解析或Loader返回值。Synthetic归档只包含来自冻结基线的三个Application helper；不是Production归档或审批原件。

| 新增用例 | 实际结果 |
| --- | --- |
| current→releases/id/enterprise_agent_poc | PASS，正式Loader返回的app_root与真实resolve结果一致 |
| current→releases/id | REJECT：PRODUCTION_RELEASE_CURRENT_MISMATCH |
| current→其他Release的enterprise_agent_poc | REJECT：PRODUCTION_RELEASE_CURRENT_MISMATCH |
| 批准Source不一致 | REJECT：EXACT_PRODUCTION_APPLICATION_REQUIRED |
| 批准Tree不一致 | REJECT：EXACT_PRODUCTION_APPLICATION_REQUIRED |
| Manifest内容变化 | REJECT：approved_manifest_identity |
| Archive实际字节变化 | REJECT：archive_checksum |
| 已安装helper实际字节变化 | REJECT：release_file_identity |
| 未授权审批位置及实际非Root所有权 | REJECT：PRODUCTION_APPROVAL_LOCATION / PROTECTED_PATH_OWNER |
| 工具Code Pin错误 | REJECT：CODE_PIN_MISMATCH |
| 正确目录布局下的真实PG dry-run | PASS：READ ONLY，business writes=0 |

为遵守本轮无Root操作边界，正向Production分支测试仅在精确临时审批目录/文件上模拟Root所有权。文件类型、实际权限、真实路径和所有其他门禁仍运行原代码；恢复真实所有权检查后，同一Synthetic材料不能作为Production授权。临时APP_ENV仅设置于测试进程并自动恢复，不启动或修改任何正式环境服务。

## 原有PG回归和只读结果

**41 passed / 0 failed / 0 errors / 0 skipped**，47.99秒。原30项真实PG回归全部继续PASS，新增11项PASS。两项既有Starlette/anyio依赖弃用Warning单独记录，不计为失败或跳过。

本轮使用独立PG16.6 / pgvector0.6.0、Python3.11.16、非Root UID=1000；属于本地定向组件/真实PG回归，不是PRIMARY Native或PG0.8.6全环境验收。没有运行Full pytest、Stage2或Candidate Build。

根目录：`/private/tmp/ky-web-stage1-postgres.customer-4w2fwm24`，owner-only Unix socket，TCP禁用；每项测试独立随机数据库。原测试覆盖真实事务回滚、租户/邮箱隔离、初始积分0、Legacy实例disabled、再次初始化保持、登录与成员/知识权限、改密撤权、匿名管道ACK及审计安全。

新增Production目录分支的dry-run使用其真实Synthetic PG身份，并执行 `REPEATABLE READ READ ONLY`；核对前后全业务行摘要一致、目标五表行数0、无收据或密码生成，Manifest/Archive及指针保持。未复用PRIMARY或Production数据库身份。

代码快照：`/var/tmp/customer-pointer-fix.UzVvXR/enterprise_agent_poc`。临时PG已停止，所有测试数据库及Receiver进程已结束，日志保留。

JUnit：`/var/tmp/customer-pointer-fix.UzVvXR/pointer-fix.xml`；Windows副本：`C:/Users/猪猪/Documents/ChatGPT/ky_web/.customer-pointer-fix-evidence-v1/pointer-fix.xml`。

JUnit SHA256：`e6e9674f153c58db11726cba866af9b5a99d2c49b01479cf2eb07bf5da61d5fa`。

## 06正式Receiver和审批条件

本轮不安装Receiver、不签发或修改Root Approval、不执行正式dry-run/execute。06后续必须在独立审批范围内：

1. 获取新的Tooling精确Source/Tree和工具Code Pin；旧da164审批不能继续作为后继工具授权。Application三个helper的Pin仍与实际562f文件逐一匹配，不更新旧Release Manifest或Archive。
2. Fresh只读确认Production仍为批准的562f Source/Tree、Release ID以及 `release-current.resolve()==releases/<id>/enterprise_agent_poc`。若Application发生变化，停止并重新审批，不自动接受其他Source。
3. 绑定真实客户五项参数、确认企业配置摘要、精确DB身份、Schema001–015完整Pin、Legacy模板清单、独立受保护Receipt目录及单次操作期限。没有真实客户参数或授权文件不能execute。
4. 正式Approval仍须位于 `/etc/enterprise-agent-customer-provisioning-v1/<operation_id>.json`，root-owned 0444，目录0755，精确operator UID及最长24小时有效范围。旧审批不可改写或复用为新工具批准。
5. 独立审查并批准安全Receiver：PRIVATE_ANONYMOUS_PIPE_V1，精确PID/UID/executable SHA/cmdline SHA、两条匿名pipe及方向/inode，接收后通过ACK确认operation_id和凭据包摘要。禁止测试Receiver直接用于客户，禁止stdout/日志/普通文件明文交付。
6. 先受控dry-run并审核清单，随后仅在另行正式开通授权下execute。Receiver缺失或不可信时继续 `CREDENTIAL_HANDOFF_BLOCKED`；不生成密码、不写业务记录。事务提交或ACK不明确时只读取证，不自动重试或删除Tenant。

本轮未更改Production release-current、Application、数据库、Manifest、Test环境、真实Agent实例/积分账户或任何Root Approval。真实客户账号创建=0；Provider/WeChat/Image calls=0。

`FIRST_FAILURE_POINT=NONE`（代码候选）。`PRODUCTION_EXECUTION_AUTHORIZED=NO`。

STOP。
