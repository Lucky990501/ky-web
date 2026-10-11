# e70 专用 Test Authority 签发适配器

此目录是独立 Tooling 候选，Application 仍冻结为 `e70b61aa3228001d0fdf522f8673dfe1d1042e62` / Tree `4cfa565813a96713402883540bfaf37c75e23c47`，其精确前驱是 `20261007-562f201-reference-dependency-retry-v1`。本工具不修改 e70 UI、历史 Release 体系或 v1/v2/v3/v4 Authority。

该候选复用现行 Root-owned Contract、独立 Approval、文件 Hash、严格 Manifest 与隔离安装检查机制。它不是 Production Release Operator，也不是 PKI 签名服务。Root 文件封存必须由另行获权的管理员执行；本仓库提交和单测不会签发真实权限。

## 文件和信任入口

- `authority.py`：标准库单文件入口。Root 拷贝到 `/opt/enterprise-agent-workbench-test/shared/test-release-authority-e70-v1/authority.py` 后才可能签发。
- `e70-package-pins.v1.json`：144 个 e70 发布文件与 141 个原始 562 前驱文件的 SHA256/执行位，以及精确 v2 declaration；内置校验 SHA 为 `53a4d4123bf602afe1620cd8001132637d10d68f1a1e8c01f3273dbe763691a7`。
- `test_authority.py`：本地隔离组件测试。真实历史离线包用于字节核对；所有 Root/内核元数据模拟均显式标注，不可冒充 Native。

执行签发必须使用系统 Root 管理的 `/usr/bin/python3 -I -B`，不能用 lucky 可写的 managed Python、PYTHONPATH 或动态外部 helper。Bootstrap 目录 root:lucky 0750，两个可信输入文件 root:lucky 0640，可信父目录必须 Root 所有且无 group/world write、无 symlink 逃逸。

## 真实 CLI

```text
authority.py plan
authority.py issue --request PATH --audit-id REAL_AUDIT_ID --operation-id UUID
  --expires-at UTC_ISO --tooling-source COMMIT --tooling-tree TREE
  --expected-code-sha256 ADMIN_APPROVED_SHA256
authority.py seal-package --operation-id UUID --archive PATH --manifest PATH
  --expected-archive-sha256 SHA256 --expected-raw-manifest-sha256 SHA256
  --expected-canonical-manifest-identity SHA256
authority.py verify-package --operation-id UUID --archive PATH --manifest PATH
  [--installed PATH] [--predecessor-installed PATH]
```

`plan` 只打印冻结范围。`issue` 要求真实 Linux x64 Test Host、UID0、系统 Root Python、可信代码、管理员独立批准的 code SHA、原请求 SHA、原始 562 包身份及现行 d8a 指针/三服务 CWD 不变。它只写自己的新 scope 文件，持有非阻塞 `.issue.lock`，重复 operation 拒绝覆盖。授权最长 24 小时，有明确审计编号、独立 Tooling Source/Tree、零调用预算和资源上限。

`issue` 在 `scopes/<UUID>/` 依次 O_EXCL 写 Contract、审计证据，最后才写 Approval 并 fsync。JSON 为 root:lucky 0640，scope 为 root:lucky 0750。中途失败保留半成品，不自动补签、删除或覆盖；没有完整 Approval/Evidence 链便不能消费。Root Approval 只允许 B 级构建/文件身份检查，不批准 DB 写入、隔离服务激活或 C 级恢复演练。

`seal-package` 是 Linux 构建完成后的第二次 Root 文件封存，不改原 Contract/Approval。它核对真实 `Linux-x86_64` Manifest、精确 Source/Tree、144 个文件的字节/执行位及管理员已批准的 Archive/Raw/Canonical SHA，再独立 O_EXCL 写 `candidate-receipt.e70.v1.json`。Windows Manifest 不能当作 Linux 收据，重新格式化历史前驱 Manifest 也会被拒绝。

`verify-package` 消费全部 Root 证据。必须在 `enterprise-agent-test.slice` 中命名为 `ea-test-e70-*.service` 的 lucky 一次性有界作业执行；检查 CPU/内存/Tasks/RuntimeMax 与根盘空闲上限。仅读允许的 `release-evidence/wechat-e70-validation-v1/artifacts` 输入，验证原始 562 包、新 Linux 收据及可选已安装目录。它不会解压、安装、启动服务或写 DB。安装目录存在时必须严格匹配文件集、SHA、执行位及 Root-owned/non-writable 要求。

所有信任、Source、Manifest、路径、期限、历史状态或收据不明确时默认拒绝，无 `--force`、`--skip`、Root绕过或任意 Source 选择参数。不得重跑或改写历史 Authority 的准备/执行脚本。v1/v2/v3/v4 常规目录只读留存；不存在的历史目录按 absent 记录，不新建或声称已验证其他未披露路径。

## 资源和依赖范围

Test Host 为 `115.190.56.61`，hostname 固定 `iv-yew0uhfksgbw80bm48mv`。最大 CPU 200%、MemoryHigh 3G、MemoryMax 4G、swap 1G、TasksMax 512、单验证作业 1800 秒、Workbench 总存储 10 GiB、根盘空闲至少 20%。无 Provider/WeChat/Image 请求；无网站端口 80/443 或 website 数据访问。

Application 依赖文件与 562 未变；合同固定参考 Python3.11.16、MCP1.30.0、Node24.21.0、69 包及 accepted dependency snapshot。文件包通过不代表运行环境已安装/依赖通过；实际 Linux 构建及 Native 前仍须 fresh installed attestation。签发器自身仅用系统 Python 标准库，兼容主机 Python3.10。

## 隔离测试

在普通独立开发工作树运行：

```text
python -B tools/wechat_e70_test_authority_v1/test_authority.py -v
```

必需 fixture 是已保留的 e70 Windows 离线 Archive/Manifest、原始 562 Archive/Manifest 和原审批申请。默认从该候选工作树所在 workspace 查找，或分别通过 `E70_TEST_ARCHIVE`、`E70_TEST_MANIFEST`、`E70_TEST_PRIOR_ARCHIVE`、`E70_TEST_PRIOR_MANIFEST`、`E70_TEST_REQUEST` 指定只读测试文件。缺 fixture 直接失败，不 skip、不造假。测试不访问服务器、数据库或 Provider；只在临时目录生成明确标记的模拟文件。

## 验收边界

| 级别 | 本工具能够证明什么 |
| --- | --- |
| A Windows 离线包 | 已有冻结 Source 及字节核对证据，不是 Linux/Native。 |
| B Linux 构建与安装身份 | 独立 Root scope、真实 Linux 收据和严格包/安装字节校验；仍须实际获权执行，当前候选本身不是 B PASS。 |
| C PG Schema015 systemd 及 562 恢复 | 本 scope 不授权。需要独立批准的 DB/Redis/units 与真实恢复证据，不能从 B 推导。 |
| D Production | 永不由此工具授权；正式 Production Release/恢复及数据保留均为独立门禁。 |

管理员精确 Tooling Source/Tree、最终 code SHA、完整 Bootstrap 和签发命令在候选封存后随 `WECHAT_E70_TEST_AUTHORITY_SIGNER_ADAPTER_V1_ADMIN_COMMANDS.md` 交付。不能把 README 参数说明当作已经签发的审批。
