# WECHAT_SKILL_REVISION_RUNTIME_V1_REPORT

日期：2026-10-07。窗口：01 / Agent Workbench Architecture。
任务附件：`086222b7-1c62-45ab-af64-cd50bfb04826`。

交付目标：**WECHAT_SKILL_REVISION_RUNTIME_READY**。本报告、相关 Source 和 artifact 通过下述独立提交封存；最终 commit/tree/push 结果以外部 Source seal 和最终交接为准。此状态不是 `WECHAT_SKILL_PRIMARY_TEST_READY`，也不授权 PRIMARY 安装或 Production 部署。

## 1. Adapter Source / precise Git extraction

ADAPTER_BRANCH：`codex/wechat-revision-python-runtime-v1`。
父 Source：`fc073b84008e6e8faee638ac4360635ca489fc83`。

ADAPTER_SOURCE 为本报告首次进入上述分支的提交；ADAPTER_TREE 为该提交的 root Git tree。采用提交后的外部 seal，避免将 commit hash 写入其自身形成不可解的自引用：

```bash
ADAPTER_SOURCE=$(git log --diff-filter=A --format=%H -1 -- docs/WECHAT_SKILL_REVISION_RUNTIME_V1_REPORT.md)
git rev-parse "$ADAPTER_SOURCE^{tree}"
python -B enterprise_agent_poc/scripts/wechat_revision_python_runtime.py seal --output /approved/evidence/adapter-source-seal.json
```

本机外部交接文件：`.codex-wechat-revision-runtime-v1/adapter-source-seal.json`；包含精确 source_commit/source_tree、descriptor SHA、adapter digest、artifact SHA、lock SHA、Test environment 和 managed root。最终答复也直接提供精确 commit/tree。

只精确 stage 本 Skill 的 Source、permission integration、runtime/lock、测试、报告及 69,071-byte Native ZIP。不使用 `git add .`。既有 `.gitignore`、`deploy/RELEASE_TEST_ENVIRONMENT.md` 用户改动、其他历史 untracked 文件/报告均不纳入提交或清理。全工作树因此允许仍 dirty，但封存提交不包含这些改动。

Source seal 检查相关文件确已 tracked/committed 且无替换，并检查 descriptor 中 41 项 adapter/support/Native 文件 SHA。执行时另核对 Git HEAD/tree，拒绝任何 tracked app dirty。seal 是本轮代码/制品身份交接，不代替既有 Test authority 对 later Source 的版本化部署/验证批准。

## 2. Skill artifact / unchanged 13-file manifest

现有 Registry 字段：`skill_versions.id` / `skill_versions.version`。Skill slug `wechat-html-draft`，Native version `1.0.0`。未新建第二 Registry、Schema、Migration 或 live Agent。

Git 中正式 artifact：`enterprise_agent_poc/integrations/artifacts/wechat-html-draft-1.0.0.zip`。
SHA256：`4a140c878ae7057583089a4410cd1a23c5c95664988e6dd9700f75ed51d3b18c`。
13-file checksum manifest：`integrations/wechat-html-draft.revision.v1.json`，SHA `0329daa05bfaff79c158807364d88003d3c1fdc88ebdf245d8bfa11a6bbfd490`。

原 ZIP SHA 保持 `a2f9aae56b1ad59cf9d4532fe9310bfb2827f43465d68e0a4db3746e8b2e14c3`。Skill instructions、requirements、脚本、assets/references 和 Native ZIP 内容均未改。新 runtime 元数据在 ZIP 之外，不通过修改旧 Skill checksum 强行关闭 dependency gate。

## 3. Composite Revision identity / immutable binding

本轮建立初次 runtime contract V1，而不是覆盖已有 live runtime（尚不存在）。复合身份：

```text
existing published Skill Revision UUID + slug/version
  + exact Native artifact / 13-file identity
  + Adapter Source commit/tree + verified adapter file digests
  + Runtime descriptor SHA + Linux dependency lock SHA
```

`venv_identity` 是 canonical binding 的 SHA256，其中包含真实 Registry Skill/Revision IDs 和上述 approval tuple。runtime root 的 `receipt/binding.json` 首次登记后不得覆盖。相同 Revision 再安装只验证并复用同一 identity；不同 Source/Lock、V2、不同 UUID 或 checksum 不能复用 V1 环境。已有 failed/partial directory 不自动 repair、删除或在线补包。

任何已批准 tuple 的 artifact、Adapter Source 或 runtime lock 变化均需要显式新 Revision/descriptor；不得在现有 V1 下换包。V1 resolver 故意不自动接受新 version；未来新增支持需要单独 review。后续 release 若更换被绑定 Source，即使无关功能先变化，也必须显式处理 Source compatibility，不能悄悄更新 seal。

## 4. Minimal runtime model / directory

实现：`app/skill_python_runtime.py`。复用 Registry published row 作为 identity 输入，外置 descriptor 作为其制品合同，不新增 DB/runtime registration system。

```text
Skill Registry Revision
  → integrations/wechat-python-runtime.v1.json
  → managed-root/slug/skill_versions.id/venv/bin/python
  → lock + verified wheelhouse
  → verified Skill entrypoint
```

本轮 Test root 绑定到：
`/opt/enterprise-agent-workbench-test/shared/runtime/skills`。

```text
wechat-html-draft/<actual-published-revision-uuid>/
  venv/
  lock/runtime-lock.json
  lock/requirements.txt
  receipt/binding.json
  receipt/runtime.json
```

禁止 root 在 Source checkout 内；Task workspace 不得包含 runtime root，或位于 runtime root 内。验证 root/路径无 symlink escape，Revision ID 必须 canonical UUID。approval 必须是 Test 且精确匹配 managed root；当前合同不安装到 Production root。入口不接收模型/文章指定的 Python 路径。

## 5. Python / platform binding

Target：**Linux x86_64 / CPython 3.11.x / cp311 / glibc >= 2.28**。
PRIMARY 3.11.16 是用户/06 上轮 attestation 事实，本轮未重新查询服务器。

css-inline 使用 `cp39-abi3-manylinux_2_17_x86_64`，CPython stable ABI 可用于 3.11；Pillow 是 cp311 wheel，平台标签含 manylinux_2_27/2_28。合同保守要求 glibc >=2.28。纯 Python wheels 使用 py3-none-any。

wheel filename/tag 校验采用标准 packaging tags；不是把 Windows wheel 改名当作 Linux wheel。[PyPA platform tags](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/)。

## 6. Exact dependency closure / lock identity

输入保持原 `scripts/requirements.txt` 的五个直接 ranges。

| Package | Exact version | Role |
| --- | --- | --- |
| requests | 2.34.2 | direct |
| beautifulsoup4 | 4.15.0 | direct |
| css-inline | 0.20.2 | direct |
| pillow | 12.3.0 | direct |
| tinycss2 | 1.5.1 | direct |
| certifi | 2026.7.22 | transitive |
| charset-normalizer | 3.5.2 | transitive |
| idna | 3.20 | transitive |
| soupsieve | 2.10 | transitive |
| typing-extensions | 4.16.0 | transitive |
| urllib3 | 2.8.0 | transitive |
| webencodings | 0.6.1 | transitive |

新 lock：`integrations/wechat-python311-linux.v1.lock.json`。
SHA：`3a5f482475c7cafe660df2d171f369a32832ac91e12e9b0ddffde5c5d6ac8abe`。
hashed requirements SHA：`3a7929f34e0dc587b36b7954ecdc89f8e1cffeedd9f24c16d4c4d46fb1299767`。
runtime descriptor SHA：`1b7d02d983867a815c898a94944bf3660597fd513d1bf0310608100f9b70e769`。

每个 package 记录 exact version、filename、SHA256、官方下载 URL、wheel tags、Requires-Python、Requires-Dist 和 Linux3.11 active dependencies。生成器从 wheel METADATA 递归检查完整 reachable closure、specifier 和 target markers，拒绝缺项/多余项/版本冲突。12 个版本与旧 Windows 观察 pins 相同，但本轮 hashes 确为独立下载的 Linux/portable wheel identity，不是旧 Windows hash。

历史 dependency descriptor SHA `4320d2df0cc5c28780a8b6b4774b9961fba8f611e732f20d015d805d5e82ca1b` 保持；其中 NOT_ESTABLISHED 是旧证据的状态，新 platform lock 显式取代该条阻断，不回写旧 Native manifest identity。

## 7. Wheel supply / installation semantics

本机已下载 12 wheels，目录 `.codex-wechat-revision-runtime-v1/wheelhouse`。全部实际 bytes SHA 与官方 PyPI exact filename metadata MATCH，无 yanked artifact、无 sdist/source build。二进制 wheelhouse 不混入代码 commit，06 可从 committed manifest 的固定 URLs 重获相同 bytes，或接收已验证 cache。

`scripts/lock_wechat_python_runtime.py` 仅用于架构阶段生成不可变 lock；`scripts/wechat_revision_python_runtime.py fetch-wheels` 只按已有锁取固定 wheel 并验证 SHA，不解析版本。后续 PRIMARY 安装必须先备齐 wheelhouse，安装过程 offline。

Installer 的实际命令语义：

```text
<attested-base-python> -I -B -m venv --copies --without-pip <revision-dir>/venv
<attested-base-python> -I -B -m pip --isolated --disable-pip-version-check
  --python <revision-dir>/venv/bin/python install
  --no-index --no-cache-dir --no-deps --require-hashes --only-binary=:all:
  --find-links <verified-wheelhouse> -r <revision-dir>/lock/requirements.txt
<attested-base-python> -I -B -m pip --isolated --disable-pip-version-check
  --python <revision-dir>/venv/bin/python check
```

base pip 仅充当 installer driver，`--python` 明确指向专用 venv；不向 base venv 安装，不升级 base requests/Pillow，不使用 sudo/system Python，也不把 pip/setuptools 注入 Skill dependency closure。06 必须先验证其 attested pip 支持 `--python`。[pip download](https://pip.pypa.io/en/stable/cli/pip_download/)、[hash-checking installs](https://pip.pypa.io/en/stable/topics/secure-installs/)。

本轮真实执行了严格 `pip download --no-index --no-deps --require-hashes` 二次校验，12 wheels PASS；没有实际 pip install 或创建 Python venv。Windows sandbox 的临时 pip 目录清理有非致命 warning，exit0；未按提示清理历史/外部目录。

## 8. Runtime receipt / failure behavior

成功安装后生成：skill key/version、真实 Skill/Revision IDs、source approval tuple、Python version/ABI、target、lock SHA、package versions、wheel hashes、venv identity、creation_status READY、pip_check PASS、imports PASS、venv file digest 和 UTC timestamp。没有任何 Secret。

本轮没有伪造 live READY receipt。新测试中的 receipt 均明确为合成 fixture。失败留下 partial directory/binding，但无可用 READY receipt；重复调用失败关闭，禁止自动覆盖/repair。运行时核验 saved lock/requirements、binding、receipt、venv file bytes digest，以及实际 Python probe、imports、包版本和完整 installed set。不接受额外包/system-site contamination。

每次 resolve 只读，无在线 pip/download。venv 文件 digest 防止已装包悄悄改变；它不是跨目录 byte-identical venv 的承诺（shebang/绝对路径可不同），可复现性目标是相同 Revision 的 dependency/artifact identity。共享系统库、并发恶意文件交换及 OS 权限仍须 PRIMARY native 验收，不以 Python checksum 声称完成 OS sandbox。

## 9. Execution resolution / same runtime for both actions

`wechat_skill.runtime_for_action()` 对 PREPARE/CREATE_DRAFT 调同一 exact Revision resolver。`execute(PREPARE)` 不再使用 Workbench `sys.executable` 或其 metadata 作为执行依赖证明；无 resolver/revision 即 `SKILL_RUNTIME_NOT_READY`，无 fallback。

启动用 resolved venv Python、`-I -B` 和受信任 bootstrap，仅加入已校验的 Skill script directory；不继承 PYTHONPATH/Wechat/Provider credential。配置不能指定 Python 路径。Skill Source 与 Task 输出分离，旧 Path/SSRF guard 不放宽。

CREATE_DRAFT 的 Runtime selection 与 PREPARE 完全相同，但本轮 **仍不启用网络上传 runner**。旧 unscoped `execute(CREATE_DRAFT)` 继续 fail closed，权限/临时 Secret lease 合同保持。Runtime ready 不等于外部写授权；两种 action 只在 permission/Secret/network 上有差异，不另建 Python 环境。

Native ZIP 不改写 shell instructions/discovery。本轮接通的是正式 adapter 的专用 Python 选择，不把“复制 Native Skill 到 profile、直接运行系统 python”认定为通过。06 激活前必须验收真实 Agent action dispatch 经过这个 resolver；若 native dispatch 仍绕开它，停止并返回集成阻断，不能凭文档提示冒充 enforcement。

## 10. Deterministic tests / attribution

本机 Windows Python3.12.14；无 Full pytest、Stage2、Candidate Build、WSL/PRIMARY runtime 执行。

| Suite / check | Actual result |
| --- | --- |
| 原 ZIP 未改的原始测试 | 17 PASS / 0 failures / 0 errors / 0 skipped |
| 既有 adapted suite | 66 PASS / 0 failures / 0 errors / 0 skipped |
| 新 revision runtime suite | 29 PASS / 0 failures / 0 errors / 0 skipped |
| 12 Linux/portable wheel 官方 SHA + closure | PASS |
| 严格 hash 模式二次 offline download | PASS / exit0 |
| Native artifact / 13-file identity | PASS / unchanged |
| PRIMARY venv build / pip check / imports | NOT_EXECUTED |
| PRIMARY Agent PREPARE | NOT_EXECUTED |

17 也包含在 66 retained cases 中，不把它们统计成 112 个 unique tests；本轮共有 **95 个 unique adapted/runtime tests**。

新 29 项覆盖用户要求的全部 12 场景，并增加 Source commit mismatch、不同 UUID、venv file drift、imports/pip-check 失败、Source 内 root、draft revision、无解释器 fallback、额外包、descriptor drift、无自动安装、坏 UUID、glibc/ABI/arch、坏 wheel hash 写入前拒绝、同 Revision 不得改绑 Source、Production/不同 root 拒绝和 PREPARE 实际 command 选择/scrubbed env。

新 tests 使用真实 descriptor/artifact/文件 hashes，Git identity、Linux probe、安装命令 I/O 为受控合成夹具。不是 Linux 3.11 安装证明。66 中 PREPARE behavior 在测试专用 resolver 下使用现有 Windows interpreter/dependency directory，验证实际离线 HTML 生成；测试 bootstrap 的 dependency injection 仅在 test 中，不是 production fallback。原 FakeAPI 的“草稿创建成功”输出不是网络调用。

运行：`verify_wechat_skill.py --original-zip <original>`、默认 66、`--runtime-tests` 29；新 command-plan case 使用 `WECHAT_RUNTIME_TEST_WHEELHOUSE=<verified-cache>`，缺缓存直接失败，不跳过关键测试。

## 11. 06 PRIMARY install plan — not executed this turn

以下是下一轮受控计划，不是本轮授权执行：

1. Fresh live Source/tree/immutable Test contract/base Python/dependency snapshot/资源与 root ownership attestation。新 Source 需按既有版本化规则批准，不改旧 sealed v1/v2；不得替换 current 或混进 Production venv。
2. fetch exact ADAPTER_SOURCE、detached checkout，验证 ADAPTER_TREE；读取本报告/descriptor，用 final seal tuple 核对全部 hashes。不复制 dirty Windows 目录。
3. 验证 committed Native ZIP SHA + 13 checksums；验证 runtime descriptor / lock / hashed requirements。按固定 URLs 获取或转交 wheelhouse，再核对全部 12 filenames/hash/target tags；不能传 Windows wheels。
4. 获取现有正式 published Registry row 的真实 UUID。如尚无 row，先通过已授权 Registry import/test/publish 登记元数据/包，**不 deploy 到 Agent/profile、不 enable**；再导出 row。这是 `skill_versions.id` runtime 路径的必要前置，禁止虚构 UUID 或手工 SQL。Profile Skill installation 仍在测试之后。
5. operator-only CLI `install --approval <verified-seal> --revision <trusted-registry-row.json> --managed-root /opt/enterprise-agent-workbench-test/shared/runtime/skills --base-python /opt/enterprise-agent-workbench-test/shared/runtime/python311/bin/python --wheelhouse <verified-cache>`。在 Test resource slice 内运行；不执行本轮。
6. 独立核对 receipt、pip check、5 imports、版本/完整 installed set、base Workbench snapshot 未变、目录/权限/原生 symlink行为。installer driver 不提供这一步的 fake PASS。
7. 专用 venv 下运行 17 原始和 66 adapted，再运行 29 runtime cases；传入 verified Test wheelhouse。保存 Linux3.11 fresh evidence，不能重用 Windows PASS 作为 PRIMARY PASS。
8. 通过正式 Registry/Profile deployment 安装精确 Skill Revision；核对 read-only assets/scripts、runtime reference/discovery。不能让 Native bash helper跳回主 Python。
9. 定位已有唯一公众号 Agent、明确 draft Revision，用原 Skill + optional action binding 方法；经授权测试/发布/实例流程激活 Test Binding，保留其他绑定/历史。不创建重复 Agent，不动 Production。
10. 真实 restricted Agent Task PREPARE：记录 Task/context/Skill UUID/runtime identity，核对该 venv Python、HTML/inline CSS/local assets、Task 输出、no-secret/no-traversal，以及普通聊天不触发。没有强制 runtime dispatch 证据则 STOP/BLOCK，不直接用 script smoke替代。
11. CREATE_DRAFT 不启用，不 provision Secret、不访问 WeChat。完成后 STOP，报告 PRIMARY 实际安装/回归结果及 Production unchanged。

所有 live 子步骤均需要下一轮对应授权，尤其登记/publish Registry row、Source 部署、Agent test binding；本轮未执行这些操作。

## 12. PRIMARY unchanged / Production unchanged

PRIMARY operations=0；未 SSH、install/load、服务 restart/switch、读 Secret、DB/Redis mutation 或 Agent/Skill live binding。未触碰 managed dependency snapshot、sealed contracts、Production venv/Registry/Bindings/Secrets。Provider/Image/WeChat=0/0/0。

当前正式 runtime/source 制品准备完成不关闭 PRIMARY native install/permission/discovery/3.11 acceptance gate；上轮 missing dependencies 观察仍真实有效，服务器尚未安装完整 Skill closure。本轮只为其提供可执行的受控安装机制与 exact artifacts。

## 13. Changed files / evidence

新增 `app/skill_python_runtime.py`、runtime descriptor、Linux lock/hashed requirements、Native artifact copy、两个 operator scripts、29 tests、本报告。封存此前 `wechat_skill.py`、Tenant Secret/action contracts、4 个正式 permission/config 最小适配、Native Source13文件、旧 descriptors、66 tests/runner/两份历史架构报告。

更新此前两份报告采用 append-only 后续记录，不改其历史 PRIMARY facts。`app/settings.py` 等未改正式 support files 仅被 checksum 绑定，不人为修改其业务逻辑。无 Schema/Migration/Prompt/Model/Skill 内容改动，无全局依赖变更。

本机 evidence：`.codex-wechat-revision-runtime-v1/local-validation.json`、`adapter-source-seal.json`、`wheelhouse/`、`hash-validation/`。wheelhouse 与 venv binaries 不作为 Source 提交；Native ZIP 是正式小型制品，随 Source 提交。已批准 Revision/Source/Lock 禁止任意覆盖。

**PRODUCTION_UNCHANGED**。完成后 STOP。
