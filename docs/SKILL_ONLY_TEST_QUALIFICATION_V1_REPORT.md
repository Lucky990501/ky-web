# Skill-only Test Qualification Source Patch V1

任务：`41671734-35dd-4d74-ad9c-f73109887a43`；日期：2026-10-07。
Base：`0bfded6ee6837e8c0908721d8ffab1035506e133`。
Branch：`codex/skill-only-test-qualification-v1`，独立clean worktree；原目录dirty文件保留，未混入提交。

状态：**SKILL_ONLY_TEST_QUALIFICATION_READY** — Source-level implementation/required targeted regression PASS，非PRIMARY/live/Production READY。最终Source/tree以本轮commit和外部post-commit seal为准，不将自身commit SHA写入提交形成循环。

## Qualification is not Runtime Test PASS

新增 `app/skill_only_test_qualification.py` 和独立 `CONTROLLED_SKILL_TEST_ELIGIBILITY_V1` manifest。
唯一资格模式 `SKILL_ONLY_TEST_QUALIFIED`，只能由既有root-owned/source-bound Controlled批准文件的可选qualification对象授予。
资格绑定Test环境、Controlled entry、Tenant/caller、Agent UUID、Agent Revision UUID、配置fingerprint、Skill UUID/version/artifact/lock、action PREPARE、Source/tree及authority。
请求body布尔值不是资格；_request保持原精确field集合。内部Authority对象也必须重新匹配native批准文件/identity，不能仅靠创建一个对象绕过。

受控Task创建显式传递可信Authority，进入独立resolve_context；**普通resolve路径原规则保持**。
资格只接受configured实例及exact draft/published Agent Revision、一个exact published wechat-html-draft@1.0.0，以及三个optional工具。
Context记录`runtime_test=False`、`model_execution_disabled=True`、eligibility_mode和safe qualification receipt。
不创建任何`agent_template_tests` runtime行，不publish/enableAgent，不写RuntimeTestPassed结果。
普通Chat不读取资格；普通profile/AgentService model调用遇到qualified context立即`ZERO_PROVIDER_CONTRACT_VIOLATION`，受控ticket继续走原NoModel Task/Run/MCP/persistence分支。

原DB schema要求context保存声明的Codex/model metadata，因此未改schema、未写假NULL schema值；这只是Agent声明快照。
受控Run仍明确model/provider/reasoning=NULL、codex_thread_id=NULL、execution_kind=controlled_skill_action；绝不是模型执行/质量证据。
`_runtime_passed` AST未变，AgentRuntimeTest实现、Provider代码、Skill业务、RevisionRuntime、锁、Secret和Action Permission文件均与base byte-identical。

## Authority and MCP enforcement

原无qualification的Controlled流程保持原published/enabled/真实RuntimeTest门禁及b0拓扑，不变更已有语义。
Qualified流程的API/Worker/**MCP必须同一个新sealed Source/tree**；旧b0 MCP没有新configured-instance permission consumer，明确拒绝mixed topology。
原e7 RevisionRuntime project/seal/41file descriptor/Native13/lock仍独立保留；不把新app复制进旧runtime Source。
下一轮须在新Source生成existing dispatch seal与qualification seal，安装对应versioned native Test批准/guard，不改旧sealed Release Contract或Production tooling。

MCP每次重新读取native资格并核对当前configured实例、caller enabled/member、配置/Binding、exact published包hash、限定scope和running controlled Task。
只允许skills:execute和当前Action业务scope；knowledge/assets/image/config等任意MCP权限不在资格内。
Production无论qualification对象存在与否，创建和permission消费均硬拒绝。
Provisioning临时platform_admin可在Provision完成后revoke；qualified执行只需exact native批准的enabled member，不需要保留admingrant。
其他管理员、错误caller、已disabled身份、credential-version失效及source/authority变更仍拒绝。未改变普通authoring API的platform_admin规则。

## PREPARE and negative CREATE_DRAFT

资格仅授权PREPARE。CREATE_DRAFT只是原Controlled contract已允许的**负向credential probe**：使用原Action Permission/Secret gate、原execution-disabled registration，无论credential是否存在均不能启用--run。
无credential测试实际到达`action_permission`阶段，返回原正式等价`SKILL_ACTION_NOT_ALLOWED`，child未调用。
没有修改Secret/Action业务语义，也没有新增微信上传实现。
Audit记录eligibility_mode、environment、Agent/Skill/UUID/action、Source/authority/approval/qualification identity、Task/Run及0-call标记；不含session token、Secret、article、env或stdout全文。

## Deterministic tests / provenance

Windows Python3.13.0，既有MCP1.30.0：**91 PASS / 0 failed / 0 errors / 0 skipped**。
包括27新qualification cases + 33原Controlled + 31原Dispatch，真实合成SQLite/Registry/Binding/Task/AgentService/FastMCP/持久化；native approval、HTTP transport、Revision interpreter/child I/O是明确fixtures。
新fixture不创建runtime测试行、不给platform_admingrant、使用explosive NoProvider，平台model设disabled。
覆盖用户14项：允许/未资格/Prod/普通Chat/Codex+Model/wrongAgent+Skill+UUID/credentialprobe/requestboolean/source mismatch/zero calls/正式TaskRun。
额外覆盖foreign MCP scope、fingerprint/entry/lock drift、old MCP topology、disabled member、内部伪造proof、runtime_test=false、native consumer环境拒绝、authority撤回、资格不能借用普通context，以及RuntimeTest/Provider/Secret/Skill文件不变。

本机WSL开发 Python3.11.16，规范Git暂存tree归档的三套要求回归：**93 PASS / 0 failed / 0 errors / 0 skipped，2 warnings，144.06s**。
`test_agent_execution.py`、`test_agent_productization.py`、`test_agent_runtime_test_lifecycle.py`，均为marked disposable SQLite/Runtime doubles；无Provider，也未改变旧WSL venv。
本轮要求的定向总结果：**184 PASS**（27qualification + 33Controlled + 31Dispatch + 93ordinaryeligibility/RuntimeTest），0failed/errors/skips。
额外尝试`test_release_scoped_runtime_test.py`未验证：缺少STAGE1_POSTGRES_ROOT，setup error；没有安装/启动PG，没有把该结果计为PASS或跳过闭合。不是本轮要求的本地SQLite回归通过证明，也不提供PG/PRIMARY接受证明。
不是PRIMARY acceptance、Full pytest、Stage2、Candidate或真实Provider证据。
Windows初次Temp ACL失败后同一离线测试unsandboxed重跑；增加两个static invariant tests后修正UTF-8读取。修复后的90项全部通过。
Windows legacy pytest ownership guard无getuid；直接WSL读NTFS Source又因文件mode触发manifest校验。未改/放宽manifest，改从已审查Git暂存tree生成规范mode归档。
Linuxordinaryregression测试tree：`e7fefc832562c074bacdd8ee3749ecf503ed94d5`；tar SHA256 `19fc5f22bacba38aba0d87f4a8ff1eec8a542c7c98544f9177e5738781017861`。随后只补充qualified/normal context错配拒绝条件及对应第27项qualification case；最终91Windowscases涵盖该条件，ordinaryeligibility/RuntimeTest代码未变化。
静态compile八个Python文件在Windows3.13及Linux3.11.16均PASS，git diff --check PASS；未生成pyc。两条warning为现有Starlette/httpx/anyio弃用提示。

## Scope / handoff

改动仅资格resolver/receipt、controlled authority/gates、MCP permission consumer、model-profile拒绝门禁、内部Task创建参数、tests/contract/seal/report。
没有AgentRuntimeTest实现、Provider代码、Schema/Migration、Skill/Prompt/Secret/Action Permission/Release tooling改动。
PRIMARY未连接：未创建admin/Agent/Revision、venv、Binding/approval、Task/Run，未执行真实PREPARE、未restart或Source switch。
Production未连接/操作；Provider/Image/WeChat=0/0/0。技术债未提升状态。
下一轮06需按新exact Source的versioned authority执行Provision/native qualification/Runtime安装/真实HTTPMCP与PREPARE验收；本轮不得把Source级READY当作liveREADY。

**普通Chat：UNCHANGED；Production：UNCHANGED；Runtime Test：NOT FORGED。**

交接工具：`scripts/seal_skill_only_test_qualification.py --output <external evidence path>`。
该seal只校验已commit/tracked文件并记录post-commitSource/tree/contractSHA及8filehashes，不install或激活authority。
下一轮qualifiedAPIMCPWorker必须安装同一newSource并生成新existingdispatchseal；e7runtime Source保持独立不变，旧b0-onlyMCP不满足qualified资格。
Qualifiednativeapproval在旧schema中增加严格qualification对象（字段见validate/manifest），Source/Agent/UUID/fingerprint都必须实际provision后填写，不可使用占位值制造livegrant。
**完成commit/push/外部seal后STOP，不执行PRIMARY Provision或PREPARE。**

<!-- SKILL_REVISION_CANONICALIZATION_V1_BEGIN -->
## Cross-platform Revision Identity successor

`SKILL_REVISION_CANONICALIZATION_READY` — Source/local parity only.
Based exactly on 4ca; branch `codex/skill-revision-canonicalization-v1`.
The setup defect was `sorted(Path)` Windows case-insensitive vs Linux
case-sensitive order followed by position-sensitive `files` list equality.
New verifier uses exact normalized relative path + hash + mode, UTF-8 bytewise
order and separately parsed fixed-field metadata. Persisted Native v1 identity,
Artifact/declaration/Runtime Lock and historical receipts remain unchanged.

Final Windows 30/91/66/29 and Linux 30/91/29/93 all PASS:309 unique cases,
0 failed/errors/skips; two existing Linux lifecycle warnings. All three Linux
Qualification/Controlled/Dispatch classes now execute 91 tests without setup
errors. Qualification semantics/Controlled Action/eligibility/Runtime are not
modified; corresponding static test protects the whole Skill AST except the two
explicitly allowed identity functions. Runtime-only verifier fixture no longer
imports unrelated Skill business dependencies, without any dependency install.

No PRIMARY attestation/switch/provision/Revision/venv/PREPARE or Production
operation. 06 must approve and verify the exact new Source/tree and fresh native
evidence; do not reuse old 4ca identity or rewrite its seal history.
See `SKILL_REVISION_CANONICALIZATION_V1_REPORT.md` for rules/evidence. STOP.
<!-- SKILL_REVISION_CANONICALIZATION_V1_END -->
