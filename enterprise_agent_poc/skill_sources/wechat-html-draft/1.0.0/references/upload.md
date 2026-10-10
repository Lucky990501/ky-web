# 草稿上传与恢复

## 运行环境

使用 Python 3.10+。脚本相对于本 skill 的路径是 `scripts/wechat_draft.py`，依赖列表为 `scripts/requirements.txt`。使用可用的 Python 或宿主提供的依赖运行时，不硬编码原聊天的 Python 路径。

需要依赖时，在当前任务工作目录建立环境；不要将虚拟环境写入 skill，也不要打包整套 Python：

```powershell
python -m venv work/wechat-venv
& 'work/wechat-venv/Scripts/python.exe' -m pip install -r '<skill目录>/scripts/requirements.txt'
```

以下命令中的 `<skill目录>`、`<文章目录>` 和 `<本次运行目录>` 必须替换成实际绝对路径。POSIX 使用对应虚拟环境的 `bin/python`。

## 配置与路径

`article.json` 使用 UTF-8。`account`、`title`、`digest`、`html`、`cover` 必填且不能为空。可从 `assets/article.example.json` 复制，不要上传未填写的示例。

| 字段 | 用途 |
| --- | --- |
| account | 账号显示名称，仅作记录，不替代身份验证 |
| title / digest | 标题和摘要，直接提交到微信，不自动用正文开头覆盖 |
| html / cover | 正文与封面路径，相对配置文件目录 |
| body_selector | 正文选择器，默认采用 `#article`，必须只匹配一个节点 |
| author | 可选作者 |
| image_map | 原图片地址到本地副本的映射；副本路径相对配置文件 |
| content_source_url | 可选完整 HTTP(S) 阅读原文链接 |

正文图片、外部 CSS 的相对路径相对于 HTML 文件目录。脚本支持本地图片、base64 和远程 HTTP(S) 图片；`--check` 不下载远程图片，上传运行会先验证全部图片，再调用素材上传。远程下载失败时提供本地副本并填写 `image_map`。

PNG/JPG 保留，其他可解码静态位图可转为 PNG；动图和 SVG 需另行处理，不会偷偷静态化。当前脚本的本地图片保护上限为 10 MiB，正文保护为 2 万字符及 1 MiB；这些不是微信所有账号接口的配额保证，以实际反馈为准。

## 离线预检

```powershell
& '<Python路径>' -B -X utf8 '<skill目录>/scripts/wechat_draft.py' '<文章目录>/article.json' --check --work-dir '<本次运行目录>'
```

检查 `prepared.html` 和 `preflight.json`，特别是远程图片待验证数、样式风险和正文选择范围。预览页标题应在 `#article` 外，防止正文重复出现标题。

脚本阻止交互内容、SVG、canvas、脚本、懒加载、背景图片和伪元素等输入。复杂布局、CSS 变量和响应式规则可能触发样式风险；先改为普通内联排版。用户明确接受显示差异后，才可对允许的样式风险使用 `--allow-style-risk`；它不会放行脚本等内容。

## 凭据与真实运行

账号需具有封面永久素材上传、正文图片上传、新建及获取草稿的接口权限。网页能登录不代表接口权限可用。

直接脚本读取进程环境变量：

- `WECHAT_APP_ID`：实际操作账号，必填。
- `WECHAT_ACCESS_TOKEN`：已有业务系统提供的有效 token，非空时优先使用。
- `WECHAT_APP_SECRET`：没有 token 时用于获取 token。

AppID 和 token 必须属于同一账号；脚本无法通过 token 本身证明对应关系。已有 token 管理系统时优先使用其有效 token。未配置凭据时提供交互命令，让用户在本机输入；不要通过聊天传输密钥。

Windows 安全输入封装（默认只是预检，添加 `-Run` 才上传）：

```powershell
pwsh -NoProfile -File '<skill目录>/scripts/invoke_wechat.ps1' -Config '<文章目录>/article.json' -Python '<Python路径>' -WorkDir '<本次运行目录>'

pwsh -NoProfile -File '<skill目录>/scripts/invoke_wechat.ps1' -Config '<文章目录>/article.json' -Python '<Python路径>' -WorkDir '<本次运行目录>' -AppId '目标AppID' -Run
```

使用 token 时在上传命令最后添加 `-UseAccessToken`。封装先预检，凭据输入不回显，退出时恢复原环境变量。若用户已在环境中配置好凭据，可直接执行：

```powershell
& '<Python路径>' -B -X utf8 '<skill目录>/scripts/wechat_draft.py' '<文章目录>/article.json' --run --work-dir '<本次运行目录>'
```

只有在用户要求上传或已有明确上传授权时运行真实接口。先完成用户已授权的准备工作，不重复增加确认流程。

40164 错误表示需要处理 IP 白名单。脚本从实际错误中提取有效的请求出口 IP；将它加入对应 AppID 的白名单，保留原有项目，再用相同网络和代理设置重跑。不要填 `127.0.0.1` 或本机内网 IP。出口 IP 改变时以新反馈为准。后台入口以当前页面为准。

## 状态、重跑与恢复

运行目录应放在当前任务工作目录或交付目录，长期保留。一个目录绑定文章输入与 AppID，不复制旧 `state.json` 给新文章或新账号。

同一输入、AppID 和运行目录重跑会复用已上传图片、封面与草稿，并再次回读。修改标题、摘要、正文、图片、封面或账号后，选择新的运行目录创建新草稿；脚本不会更新旧草稿。只有明确需要新文章版本时才换目录，不能用换目录规避未明确结果的提交。

脚本在变更请求前记录 `pending`。超时或连接中断可能已成功，后续运行会停止，防止重复创建；明确的微信错误响应则允许修正后重试。保留错误码与状态，不打印完整敏感响应。

`draft/add` 结果不明确时，先在微信后台或用户现有管理系统核实是否存在。确认现有草稿 ID 后，可接续：

```powershell
& '<Python路径>' '<skill目录>/scripts/wechat_draft.py' '<文章目录>/article.json' --run --work-dir '<原运行目录>' --adopt-draft-id '实际media_id'
```

ID 必须是接口草稿 `media_id`，不是浏览器链接。接续会先回读核对，匹配成功才清除 pending。不要在无法判断是否已创建时清除 `draft/add` pending。确认请求没有产生结果后，可备份状态并仅清除对应 pending；保留输入指纹、AppID、上传记录等其余字段。残留 `.lock` 只在确认无运行进程后移除。

封面上传结果不明确时核对永久素材库；正文图片不一定能在该列表找到。不能确定时先报告；用户决定重试正文图片可能重复上传，但不要因此盲目重建草稿。

## 验证记录

运行目录包含：`preflight.json`、`prepared.html`、`state.json`、`draft-payload.json`、`uploaded.html`、`wechat-returned.json`、`verification.json`。这些文件不保存密钥，但包含文章和账号操作记录，不应附带进 skill 分享包。

脚本按 UTF-8 解码响应，核对标题、摘要、封面 media_id、规范化正文文字、图片数量与顺序及素材标识。兼容 `data-src`、HTTP→HTTPS 和 `/0`→`/640` 等展示尺寸变化，同时拒绝素材被替换。真实接口字段通过后仍要在公众号后台和手机预览检查字体、行距、配图与封面裁切。

脚本的 17 项离线行为测试无需凭据，也不调用真实接口：

```powershell
& '<Python路径>' -m unittest discover -s '<skill目录>/scripts' -p test_workflow.py -v
```

本工作流已用于 OPC增长实验室真实草稿创建；历史真实返回经可逆编码恢复后字段核对通过。此记录不替代下一篇文章的接口回读，也不证明所有排版视觉完全一致。

需要核对最新权限、限制或新增接口行为时查微信官方文档：

- 新建草稿：https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html
- 永久素材：https://developers.weixin.qq.com/doc/offiaccount/Asset_Management/Adding_Permanent_Assets.html

官方页面无法访问时，不把历史观察写成最新承诺；继续依据现有代码和实际错误处理可做的工作。
