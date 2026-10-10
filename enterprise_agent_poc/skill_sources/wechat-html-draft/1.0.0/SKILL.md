---
name: wechat-html-draft
description: "将用户文字或现有 HTML 制作为微信公众号文章，按参考风格排版并准备标题、摘要和封面；在用户要求上传时，通过公众号接口上传素材、创建草稿并回读核对。用于公众号 HTML 排版和草稿上传，不用于自动发布或群发。"
---

# 公众号文章排版与草稿上传

把原始文字、图片或现有 HTML 交付为可预览的公众号图文；需要上传时，使用随包脚本完成素材上传、草稿创建和字段核对。

## 根据请求确定操作

- “排版”“生成 HTML”“给我看看”：生成文章文件和预览，不调用微信上传接口。
- “写一篇”：按用户的主题、事实与语气写作，再排版；缺少事实时不要编造数据、客户评价或活动信息。
- “上传到公众号”“创建草稿”：准备文件、离线预检，通过接口创建草稿。用户已明确要求上传且资料齐全时，直接继续，不重复确认。
- “打包 skill”或使用本 skill，不等于上传新的文章。脚本没有发布、群发、更新或删除功能。

沿用用户已确认的参考风格，除非本次提供了新参考。默认参考见 [references/style.md](references/style.md)；原始观察参数在 [references/reference-style.json](references/reference-style.json)。用户提供新参考 URL 时，读取页面和视觉证据后调整，无法访问时说明限制，不声称已经复刻。

## 文章制作

只要求排版时保留原文文字、事实和顺序；可调整分段、层级、重点句与图片位置，不套用参考文章的主题或专有句子。标题与摘要由用户明确给出时原样使用；需要生成时，摘要概括本文，不加入正文没有的信息。

从 [assets/article-template.html](assets/article-template.html) 的基础排版开始，按内容调整模块；它是结构参考，不是必须套用的固定文章。正文统一放在 `#article`，预览标题、工具按钮和操作说明放在该节点外。

生成 UTF-8 `article.html`，使用常规元素和内联 CSS。单栏流式布局，图片 `width:100%;height:auto`。将图片存入文章目录的 `assets/`，以相对路径引用，不依赖本机绝对路径。没有配图时省略图片节点，可用 HTML 注释记录建议位置，不输出空 `src` 或不存在的图片。

优先使用用户提供的图片。用户要求生成封面或配图时，使用当前可用图像生成工具；将结果保存到文章目录。未授权生成、没有图片工具或结果失败时，完成可做的文字排版并标明待补封面。封面路径可以与正文首图相同，也可以独立；手机和列表中的裁切需另外检查。

将 `assets/article.example.json` 复制并填写为文章目录中的 `article.json`。`account` 是显示名称，不能决定接口操作账号；真实身份由 AppID 和凭据决定。字段和路径规则见 [references/upload.md](references/upload.md)。

交付物保存到当前任务的交付目录，例如 `outputs/<文章名>/`，不要将文章、账号运行状态或凭据写入 skill 安装目录。只做排版而封面或账号未齐全时，交付 HTML、已知字段和待补说明，不能把预检成功当成已上传。

## 预览与上传

至少在移动端宽度检查文字换行、留白、图文顺序与溢出。优先用可用浏览器预览；无法渲染时明确尚未视觉核对。

上传时读取 [references/upload.md](references/upload.md)，使用 `scripts/wechat_draft.py`；Windows 交互输入凭据可用 `scripts/invoke_wechat.ps1`。先执行 `--check`，再在用户授权上传后执行 `--run`。预检失败要修正后再上传，不能默认添加 `--allow-style-risk` 跳过风险。

已有账号配置保存在 [references/account.example.json](references/account.example.json)。这是用户已提供的 OPC增长实验室 AppID，不包含密钥。用户指定该账号时可直接沿用；换账号时以本次确认的信息为准，不能自动把其他账号的稿件传到此账号。

AppSecret/access_token 只通过受保护的本地输入或用户已配置的进程环境变量使用。不要要求用户发到聊天，不写进 JSON、skill、日志、打包文件或命令明文。没有凭据时完成离线工作，再提供本机输入的执行命令。账号权限、IP 白名单或配额以微信实际返回和当前官方说明为准。

一篇文章、一个账号使用同一运行目录，保留 `state.json`。相同输入重跑会复用已有草稿；提交结果不明确时停止，先核对微信后台，不另建目录盲目重试。详细恢复方法见上传说明。

## 验收与报告

报告区分：本地预检、真实草稿创建、接口字段核对、后台/手机视觉预览。回读通过只能证明标题、摘要、正文文字、封面标识和正文图片素材一致，不能证明像素一致。

微信回读 JSON 必须按 UTF-8 解码；正文图片可能从 `src` 变成 `data-src`，展示路径从 `/0` 变成 `/640`，使用随包脚本按素材标识核对，不能简单比较 HTML 字符串。

普通排版尽量还原。复杂拼贴、手写文字等模块可在用户接受后做成独立图片，保留其他正文文字；不能宣称任意 HTML 都能同时像素完全一致、文字可编辑和交互可用。

完成时提供文章文件、预览入口和验证结果；真正创建草稿后给出草稿 ID 与后台草稿箱查看说明。只使用保存的接口结果核对时说明是历史返回复核，不声称进行了新的在线验证。

## Enterprise Agent Workbench V1 适配

Workbench 使用正式 Registry 的 wechat-html-draft@1.0.0，不将 agents/openai.yaml 当作 Registry manifest。文章和运行文件仅保存在服务端指定的独立 Task Workspace；WORKSPACE_ROOT 由执行器提供，所有正文、封面、CSS、image_map 和运行输出须留在此目录。

PREPARE 对应 --check，离线处理，不接收凭据。CREATE_DRAFT 对应 --run，但正式 Workbench 入口在 tenant-scoped Secret Reference resolver 与 Action egress executor 验收前禁止执行；不得改用环境变量、示例账号或 shell 命令绕过。原脚本保留本地独立使用能力，不代表 Workbench 已授权上传。

示例账号只作字段参考。正式账号须由 Enterprise/Agent account authority 绑定 AppID 和 secret reference；Agent 展示名与 article.json 的 account 字段均不能决定操作账号。不存在发布、群发、删除或旧草稿覆盖 Action。
