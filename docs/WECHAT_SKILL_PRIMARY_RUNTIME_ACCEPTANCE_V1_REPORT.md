# WECHAT_SKILL_PRIMARY_RUNTIME_ACCEPTANCE_V1_REPORT

日期：2026-10-07（Asia/Shanghai）。
任务附件：fbe8556b-6667-4dbe-9f14-0b66c3914445。
最终状态：**WECHAT_SKILL_RUNTIME_DEPENDENCY_BLOCKED**。

本轮只完成 PRIMARY managed Python 的依赖/机制只读验收、批准 Native artifact 的完整性核对，以及有明确限制的健康采集。
未安装依赖/Skill、未激活Binding、未创建Task/Workspace，未运行PRIMARY 17/66tests或PREPARE。
未将本地上游66PASS替代PRIMARY执行证明；未连接Production。

## 1. Fresh PRIMARY identity / immutable scope

Host：115.190.56.61；Unix user lucky；root /opt/enterprise-agent-workbench-test。
依赖检查时间：2026-10-07T05:17:52.813145+00:00。
健康/机制检查时间：2026-10-07T05:21:51.576878+00:00。
Managed Python：**3.11.16**；
Interpreter：/opt/enterprise-agent-workbench-test/shared/runtime/python311/bin/python。
Baseline current：/opt/enterprise-agent-workbench-test/releases/20261005-b25fc43-reference-edit-observability-test-v1。
当前运行Source属已批准b25fc43 Test activation，不是此次WeChat适配的未提交工作树。
旧封存Test contract SHA：84d28a9b8d04994f752303750f3d3eb7e29a82951fd9d59ac45d6f13b37e89b5。
Managed dependency snapshot SHA：0ee0a997e95f650a5be699075ec214dd7d9c2d94a94d8c7a7becfd0397fd1008，与0ee0a997历史批准snapshot MATCH；未改该snapshot。
检测只读临时unit属于enterprise-agent-test.slice，CPUQuota100%、MemoryMax256M、TasksMax32，结束后自动回收；
没有修改已有服务unit，也没有常驻新环境。server_mutation=false指没有应用/配置/持久化变更，不隐瞒这次只读检查unit启动。

## 2. Dependency status / imports / pip check

| Package | User range | Observed PRIMARY version | DEPENDENCY_STATUS | Import |
| --- | --- | --- | --- | --- |
| requests | >=2.32,<3 | 2.34.2 | PRESENT_COMPATIBLE | requests PASS |
| beautifulsoup4 | >=4.12,<5 | missing | MISSING | bs4 NOT_AVAILABLE |
| css-inline | >=0.19,<0.21 | missing | MISSING | css_inline NOT_AVAILABLE |
| Pillow | >=11,<13 | 12.3.0 | PRESENT_COMPATIBLE | PIL PASS |
| tinycss2 | >=1.4,<2 | missing | MISSING | tinycss2 NOT_AVAILABLE |

现有python -B -m pip check：**PASS**，exit0，No broken requirements found。
这只证明已安装Workbench依赖内部一致，不证明缺失的Skill包可导入或全部Skill依赖PASS。
5个直接依赖中没有已观察的VERSION_CONFLICT；阻断是MISSING及没有已闭合的安全安装/绑定机制，
不是版本冲突、Python语法问题或PRIMARY公网异常。没有尝试pip install或强制升级已有兼容包。

## 3. Dependency installation mechanism / concrete blocker

Installation method：**NOT_EXECUTED**。
上游dependency descriptor记录本地Windows观察版本及12个exact package pins，明确：
wheel_hash_lock=NOT_ESTABLISHED_FOR_PRIMARY_LINUX_PYTHON311；
primary_compatibility=NOT_EXECUTED。
Descriptor SHA：4320d2df0cc5c28780a8b6b4774b9961fba8f611e732f20d015d805d5e82ca1b，未改。
app/wechat_skill.py.check_dependencies()按同一解释器逐项检查全部exact pins，不提供per-Skill环境选择/解析。
其PREPARE子进程固定使用sys.executable，不是已经实现的isolated dependency runtime。

当前PRIMARY immutable Source中的正式机制审计边界：
- app/skills.py仅做Registry→profile Skills文件部署，不创建/选择dependency venv。
- app/skill_registry.py正式import/test/publish/checksum/package流程不提供revision-bound Python依赖环境。
- app/runtime/codex_provider.py部署profile Skill manifest并使用现有native Runtime；无已找到的per-Skill interpreter resolver。
- deploy/prepare_runtime_dependencies.sh固定base=/opt/enterprise-agent-workbench、runtime_venv=$base/venv，
  仅允许candidate Pillow>=10,<13 preparation；不是PRIMARY Skill dependency installer，**未执行**。
- shared/skill-runtime、shared/runtime/skills、shared/skill-dependencies三个既有候选位置均不存在。
这些是针对正式组件和候选位置的检查，不宣称遍历全Host证明任何私有机制都不存在。

没有可直接使用、已验收且绑定Skill V1的Linux3.11 wheel supply/hash lock和isolated runtime resolver。
直接安装到当前managed runtime会改变已封存的0ee0a997 Workbench dependency baseline；
本轮不静默改基线、依赖合同或global interpreter来迁就Skill。
没有新建长期环境/第二Registry或动态执行Skill时pip install，没有Windows依赖目录传输。

下一步需要先由架构/总控明确受控、Revision-bound的PRIMARY依赖环境/installer及Linux wheel identity，
并将runtime adapter的正式Source身份/安装范围封存；不能在本轮凭missing包擅自建设新的依赖架构或复制dirty代码。
随后才能受控provision并验收5 imports/pip check/17/66tests，再继续安装和Binding。

## 4. Approved artifact / Revision / checksum — fresh local audit

Native Skill：wechat-html-draft@1.0.0，现有Registry Revision field=skill_versions.version。
Artifact：.codex-wechat-html-draft-v1/wechat-html-draft-1.0.0.zip，69071 bytes。
Original ZIP SHA：a2f9aae56b1ad59cf9d4532fe9310bfb2827f43465d68e0a4db3746e8b2e14c3（保留上游pin；本轮未重新导入或重建原ZIP）。
Native ZIP SHA：4a140c878ae7057583089a4410cd1a23c5c95664988e6dd9700f75ed51d3b18c。
Revision descriptor SHA：0329daa05bfaff79c158807364d88003d3c1fdc88ebdf245d8bfa11a6bbfd490。
Dependency descriptor SHA：4320d2df0cc5c28780a8b6b4774b9961fba8f611e732f20d015d805d5e82ca1b。

批准ZIP内13个文件集合及每个文件SHA与Revision declaration逐项 MATCH；
assets/references/scripts/SKILL.md均在批准archive中。只审计artifact，不以dirty source树替换ZIP内容。
Source checksum drift=NONE；没有改ZIP、SKILL.md、requirements或Revision identity。
本机python launcher首个审计尝试返回Failed to create process、未产生有效JSON；不是Skill测试失败。
改用PowerShell/.NET ZIP+SHA256只读审计完成13文件验证，无新安装或真实Skill execution。

本地新增app/wechat_skill.py、wechat_action_contract.py、tenant_secret_reference.py及66 tests/descriptor仍untracked，
部分既有MCP/catalog/ProductStore实现是本地dirty修改；
上游报告明确“未commit/push/deploy，尚待review，不是sealed Source approval”。
PRIMARY当前三模块与dependency descriptor均NOT_INSTALLED。
不能从Windows dirty worktree复制这些代码到current、伪装已安装Runtime Contract，或称已有66 tests可在未适配Source上正式执行。

## 5. Registry / Agent binding / discovery / read-only install

Registry install：**NOT_EXECUTED_DEPENDENCY_GATE**。
Skill published Revision ID：NOT_CREATED / NOT_READ。
Skill install directory只读、assets/references可读和script执行权限：NOT_EXECUTED，archive完整性不等于live权限证明。
Agent binding activation / Runtime discovery：NOT_EXECUTED。
没有读写live Agent/Skill rows、发布Agent draft、创建Agent/User/Admin/Instance，没有手工SQL绑定。
现有公众号Agent身份/显式draft未核对；不推断存在、不创建副本。
需依赖验收和正式Source/Action executor到位后，使用原Registry及draft-only binding/publish/instance流程验收。

## 6. Tests / Workspace / PREPARE / trigger cases

| Required stage | This round |
| --- | --- |
| Native scripts/test_workflow.py 17PASS onPRIMARY | NOT_EXECUTED_DEPENDENCY_GATE |
| Workbench adapted66PASS onPRIMARY | NOT_EXECUTED_DEPENDENCY_AND_SOURCE_GATE |
| One real PRIMARY Task Workspace | NOT_CREATED |
| PREPARE Skill load / article / HTML / inline CSS / local assets / output files | NOT_EXECUTED |
| Agent receives Skill result | NOT_EXECUTED |
| HTML title/digest/body/no-script/no-secret/no-absolute-path checks | NOT_EXECUTED |
| Runtime native symlink/read-only install containment | NOT_EXECUTED |
| Path traversal / SSRF localhost/private/link-local reject underPRIMARY | NOT_EXECUTED; no request to those targets |
| A 公众号文章 → PREPARE | EXPECTED YES, live runtime NOT_EXECUTED |
| B 内容排版公众号 → PREPARE | EXPECTED YES, live runtime NOT_EXECUTED |
| C 你好 → no trigger | EXPECTED NO, live runtime NOT_EXECUTED |
| D Python分析 → no trigger | EXPECTED NO, live runtime NOT_EXECUTED |

未创建文章文件、临时workspace或虚拟环境于Skill/Registry/Release目录；未制造outputs/state.json/verification.json来冒充runtime成功。
上游连续两轮66PASS（Windows Python3.12.14）和17 retained tests保持原Source/平台归属，本轮不重写为Linux3.11 PASS。
WeChat技能的Workbench边界已读取：PREPARE=--check、Task Workspace输出、CREATE_DRAFT必须受Action/Secret/egress合同约束。
本轮未执行直接原脚本--run或绕过正式权限入口，不为验证trigger自动创建LLM Task。
Provider授权预算默认0，本轮没有额外批准Text/Image调用，均未发生。

## 7. CREATE_DRAFT credential gate / security

PREPARE按合同不需要Wechat Credential，缺Wechat Secret不是本轮dependency blocker。
CREATE_DRAFT live gate：**NOT_EXECUTED**，不能伪造WECHAT_CREDENTIAL_REQUIRED实际runtime receipt。
保持NOT_ENABLED / no upload executor activation。
上游26项Contract tests的无Secret拒绝和PREPARE无Secret支持保留为offline evidence，不作为freshPRIMARY证明。
没有Provision任何Wechat Test/Production Secret、读取既有credential值、访问api.weixin.qq.com。
cgi-bin/token、material/add_material、draft/add、draft/get calls=0。
WeChat calls=0，Provider calls=0，Image calls=0；没有草稿media_id。

## 8. PRIMARY health / Test Web / no mutation

Fresh现有service状态：
API/MCP/Worker/Pg/Redis active；
PID447181/447172/447199/721/722，与原baseline一致，没有restart/switch。

| Health probe | Fresh actual result |
| --- | --- |
| API | HTTP200,status=ok,environment=test；**knowledge=degraded** |
| MCP | initialize/ping PASS，无tool调用；instance count=1 |
| Worker | active、原PID/CWD；authenticated queue-loop NOT_VERIFIED |
| PostgreSQL | pg_isready exit0 accepting；authenticated query未做 |
| Redis | protocol reachable，**NOAUTH**；authenticated PING NOT_VERIFIED |
| Test Web | HTTPS HEAD401 + TEST Basic Auth challenge + X-Environment: TEST + noindex,nofollow |
| Frontend OPEN/login/rendering | 本轮未认证/视觉复核，不将401当frontend页面PASS |

不能概括为fresh“五服务全面healthy PASS”；API knowledge degraded和Redis认证检查不足如实记录。
NOAUTH是只读probe没有认证，不是Redis故障证据。未读取Key来补认证、未现场修知识服务/Redis。
公共HTTPS Test边界正常可达，没有maintenance操作；未fresh核对OPEN状态文件，不制造OPEN证明。
current、baseline contract、dependency snapshot、Source、Registry/Bindings与credentials均未修改。
只读临时inspection unit自动回收；无persistent remote文件写入、runtime/data fixture或cleanup删除。
本轮只新增/更新本机脱敏报告和只读audit scripts/JSON。

## 9. Production / final decision

Production：**UNCHANGED_BY_THIS_TASK**。
没有连接Production、读Production Secret/Registry/Binding/DB、部署/重启/迁移或Wechat调用。
不把昨日的Production状态或图片Smoke结果重新fresh验证为本任务证据。

主阻断：WECHAT_SKILL_RUNTIME_DEPENDENCY_BLOCKED。
附加后续前置：runtime adapter未进入identity-bound正式Source；
不可复制dirty工作树，Registry/Binding/PREPARE/Security/CREATE_DRAFT receipt均未达到执行阶段。
本轮没有新增或解决技术债、没有改旧sealed contract或历史测试结果。

成功条件尚未满足，**不返回WECHAT_SKILL_PRIMARY_TEST_READY**。
已完成所有本轮可安全执行的只读验收，未绕过依赖/Source边界，报告后STOP。

## 10. Evidence

Local：
.wechat-primary-acceptance-v1/readonly-preflight.json；
.wechat-primary-acceptance-v1/mechanism-health-readonly.json；
.wechat-primary-acceptance-v1/artifact-audit.json。
上游：
.codex-wechat-runtime-contract-v1/local-validation.json；
docs/WECHAT_SKILL_RUNTIME_CONTRACT_V1_REPORT.md；
原docs/WECHAT_HTML_DRAFT_SKILL_INTEGRATION_V1_REPORT.md历史正文保留。

Formal installed code checksum：
- app/runtime/codex_provider.py: 87b0d6a79ad9127b658a9e810ffc952c549a25c8b3e3eae41abc3b59f6b0b36c
- app/skill_registry.py: 0cfe887aeaa1e41ab9b0e23fa2182199747891e75adb8f143077479336f956d7
- app/skills.py: 1e41efafe3fa21615cfa0b324c8056850343647a09fd132645008b2be74278e6
- deploy/prepare_runtime_dependencies.sh: 92ef37467e62d2de9e20887f0a358c371ac05da30f943290097758481fbf8480

## 11. 2026-10-07 architecture follow-up — no fresh PRIMARY execution

`Revision-bound Skill Python Runtime V1` 已在独立分支建立可封存 Source、Linux3.11 wheel supply/hash lock、专用 venv installer、receipt 和 fail-closed interpreter resolver。
新 descriptor SHA：`1b7d02d983867a815c898a94944bf3660597fd513d1bf0310608100f9b70e769`。
新 Linux lock SHA：`3a5f482475c7cafe660df2d171f369a32832ac91e12e9b0ddffde5c5d6ac8abe`。
Native SHA / 13 files / old dependency descriptor 均未改变。

见 `WECHAT_SKILL_REVISION_RUNTIME_V1_REPORT.md` 获取 ADAPTER_BRANCH / post-commit seal / 06逐步安装计划。Registry真实UUID登记是专用目录的前置，不允许虚构ID；Profile deployment与Agent Test Binding必须在Linux安装/测试验收后。

本轮 Windows offline：17 / 66 / 29 tests全部PASS，12官方Linux/portable wheels SHA及严格offline hash download PASS。未做Linux venv install/pip check/imports，更未操作PRIMARY。上文3.11.16、missing packages和health结果仍是原轮实测，不将本轮架构准备换成fresh服务器PASS。

正式 gate：Revision runtime/source artifacts可交接；**PRIMARY acceptance仍NOT_EXECUTED / NOT_READY**。旧sealed契约/managed venv snapshot不改；later Source仍需版本化批准。Provider/Image/WeChat=0/0/0，Production unchanged。
