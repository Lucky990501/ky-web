# Preview 活动与公众号配图

仅安装在独立 Preview，基于 a636c3b。原图片模型、业务调用链和 20 次账本不变。活动内容合同不变；正文生成完成后，用户一次确认配图计划，再串行生成并绑定所有有效需求。没有 Key、未启用、未授权预算、张数超限均不发请求，纯文字功能照常。

## 配置

- 后端从数据目录上一级的 `.env.illustration` 读取独立配置，或使用 `PREVIEW_ILLUSTRATION_ENV` 指定受限文件。服务器文件必须为 0600，归属 Preview 用户。不可提交实际文件、输出 Key 或复用原生图 Key。
- 新模型固定 `qwen-image-3.0`，`https://api.n1n.ai`，1024x1024，保留 watermark、prompt_extend、direct、success_rate。示例默认禁用且空 Key。
- 独立 `illustration-budget-authorization.json` 参照 example。必须由 Owner 批准单价、次数、累计金额、每任务上限和用途；验收授权写入准确 `allowed_tenant_ids`，不能自动开放全部客户。
- 独立 `illustrations.sqlite` 存在 Preview 数据目录，仅增自身配图计划/请求/资产映射，不改共享数据库 Schema，不执行 Migration。
- 一次确认对整个计划原子预留。预留不等于账单；HTTP/未知结果不会退款或自动重试。重启后中断的计划标记失败，不重放。

## 正常页面

活动或公众号完整回复后可查看自动配图计划、所需张数和最高预留费用。确认后后台生成、转存，全部完成才出现图文 Word 或图文 HTML/ZIP。各张图片可独立下载。

历史查询和下载永不调用模型。部分失败保留成功资产及费用记录，不宣称整套方案完成；仅文档整理失败时可以用既有图片重做导出，绝不重做图片。当前版本不自动接受“重新生成配图”并计费；此操作需新的明确计划与授权。

公众号图文结果复用既有 sanitizer、opaque sandbox、CSP、双 MIME 剪贴板。HTML 不采信模型图片 URL，只插入本次私有存储映射。ZIP 包含 article.html 和 images 实际文件，解压可离线查看。单独 HTML 的私有图片地址需要对应 Preview 会话，不能保证粘贴到公众号后加载；微信后台仍需人工上传图片，不是 draft/add 或发布。

## 网关与下载合同

Adapter 对 data[0].b64_json 和 data[0].url 分支做隔离测试；不代表两种格式已获真实网关验证。每次只接受一张，实际格式仅 PNG/JPEG，最多 2MB（沿用 Word 现有上限），实际像素与 MIME 验证后保存并回读 Hash。

URL 仅接受独立 ENV `N1N_ILLUSTRATION_ASSET_HOSTS` 中已经核实的精确域名。域名为空或响应出现新域名则 Fail Closed，不按前端输入抓取外部 URL。HTTPS 公网 IP 固定连接、TLS/SNI 校验、拒绝跳转、凭据不转发至图片域名。需先取得真实资源合同后再配置；不得为追求 PASS 放开任意 URL。返回原始 Body、签名 URL、Key 不入日志和报告，仅保留 HTTP、受限请求 ID 与必要域名诊断。

## 测试与发布边界

定向 pytest 覆盖计划映射、两种响应分支、预算预检、归属拒绝、去重、未知/失败不重发、真实嵌入 DOCX、ZIP 和安全 HTML。`customer_preview_illustration_fixture.py` 与 `customer_preview_illustrations.cjs` 是明确 FIXTURE_ONLY 的本地 HTTP/SQLite/Chrome 验证，不是 Provider E2E。

Word 须检查有 Logo / 无 Logo 两条路径。Legacy 现在支持内联等比插图；不带插图时保持旧行为。DOCX 结构、图片关系和下载可自动校验，视觉排版仍需 documents 的 render/PNG 或 Owner Word 验收。本机缺少 soffice.exe 时不能声明自动视觉验收通过。

部署只更新精确 Preview 文件，先核对已装 Source 与全部文件 Hash，保存代码恢复证据，检查 Task/知识处理/配图计划空闲后，仅重启 Preview API/MCP。保留原数据库、文件、会话及所有预算；生产 Release、进程与健康必须不变。新模型真实测试必须另获 Key 和预算授权，并明确与 Fixture 结果区分。
