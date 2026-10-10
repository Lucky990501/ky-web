# Workbench Test Knowledge RAG P0 修复与隔离验证

日期：2026-10-10。01 交接总控、02、06。

代码已消除 Schema015 知识写入对缺失 `embedding_dimension` 列的强依赖，隔离功能回归通过。不新增列、不执行启动 DDL、不占用 Schema016、不重建知识库。当前 Test 的精确 Schema Pin、pgvector0.8.6 和 Native Source successor 兼容核对仍待 06，不能把本候选直接安装到 d8a。

状态：`WORKBENCH_TEST_RAG_REPAIR_BLOCKED`。

唯一 FIRST_FAILURE_POINT：`PENDING_06_SCHEMA_PIN_COMPATIBILITY`。这是实际环境兼容证据尚未交付，不是伪称运行 PRIMARY Guard 后得到的错误码；缺列根因已在隔离环境复现并修复。代码及隔离测试候选保留，正式 Native 验收不宣称 PASS。

## 基线和修改范围

- 直接 Parent：`d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7`。
- Parent Tree：`6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5`。
- 独立分支：`codex/test-knowledge-rag-p0-v1`。
- Worktree：`C:/Users/猪猪/.codex/worktrees/test-knowledge-rag-p0/ky_web`。
- 新 Commit/Tree 及 fresh remote 验证结果由最终交付给出，避免把自引用 Commit 写入报告。

实际变更仅五个文件：

1. `enterprise_agent_poc/app/product_store.py`：只读 pgvector 类型/维度探测、兼容缺列与已有列的写入、维度与归属检查。
2. `enterprise_agent_poc/app/knowledge.py`：健康诊断、调用前维度拒绝、Embedding 批次完整性、pgvector 查询与错误传播。
3. `enterprise_agent_poc/tests/test_knowledge_schema015_postgres.py`：20 项真实 PostgreSQL 隔离测试。
4. `enterprise_agent_poc/tests/run_knowledge_pg16.py`：有界 Unix-socket 本机 PG16.6 定向测试入口。
5. 本报告。

未修改 main.py、Migration001–015、Schema016、Skill、Prompt、Query Guard、Native Guard、Trial Tooling、Secret Backend 或其他工作树。未签发运行/安装授权。

## 根因和实际调用链

`main.py:156–157` 仅在 `environment == production` 且 Embedding Provider 不是 local-hash 时调用 `ProductStore.ensure_pgvector_schema()`。当前 Test/local-hash 不满足条件，因此不运行该补列逻辑。不能通过把 APP_ENV 伪装成 production 修复。

Migration004 创建 `knowledge_chunks.embedding vector(128)`，没有 `embedding_dimension`。Migration005–015 也未为该表增加这一列。Migration006 的 `knowledge_chunk_embeddings vector(1536)` 是另一张现存表，当前 KnowledgeProcessingService/KnowledgeRetrievalService 并未使用它；不能假设存在该表就已具备正式 1536 维检索。

旧写入路径却无条件向 PostgreSQL `knowledge_chunks.embedding_dimension` 插入数据，产生 `42703`。旧正式 Provider 检索同样在 WHERE 中使用该列。检索代码的宽泛异常捕获又会把 SQL 错误变为空结果并转向旧文本检索，掩盖存储故障。

现有调用链保持不变：

```text
登录的 enterprise_admin 上传 TXT
  → main.upload_knowledge_file
  → storage_provider + ProductStore.create_knowledge_file
  → 原 local task 或 Redis knowledge queue
  → KnowledgeProcessingService
  → DocumentParser → ChunkingStrategy → EmbeddingProvider
  → ProductStore.replace_knowledge_chunks → ready
  → KnowledgeRetrievalService → pgvector <=> → 原置信度与 Query Guard
  → PlatformMCPService.knowledge_search → Agent Runtime 工具结果 → AgentService Trace
```

## 证据边界

总控本轮确认：Test Application=d8a、PostgreSQL16.6、pgvector0.8.6、Embedding Provider=local-hash、Dimension=128、缺列错误42703、Native Guard=PASS。总控同时明确完整脱敏 Schema 快照及精确 Pin 尚未生成。

本轮未连接 PRIMARY。真实 Test 的 `embedding` 类型/typmod/约束、实际 Embedding model 名称及完整 migration ledger **仍待快照核对**；本报告不把 d8a Migration 的预期结构冒充现场读取结果。

本机独立测试实际版本：PostgreSQL16.6 / pgvector0.6.0。它是真实 pgvector 执行，不是 SQLite 或向量 SQL Mock，但**不是 Test pgvector0.8.6 的精确环境验证**。既有 TD-045 版本差异不在本轮关闭。

## 最小修复设计

`product_store.py:276` 从 `pg_attribute/pg_type` 读取 embedding 实际类型和 atttypmod，另行探测可选维度列。该函数只读，不调用任何建表/补列逻辑。

`replace_knowledge_chunks()` 在同一事务内校验文件 Tenant 归属、knowledge_base_id、向量长度及有限数值，再替换目标文件 chunks。缺列时不向它写入；如果已有经批准的部署包含该列，仍填入真实长度，保留旧读取端兼容。错误向量或跨 Tenant 文件请求不会删除旧 chunks。

PG 检索统一使用 `vector_dims(c.embedding)`，并绑定 Tenant、文件 Tenant、ready 状态、provider、model 和维度。local-hash 也经过真实 pgvector 距离查询；这仍是 hash 功能检索，不是语义模型验收。原 Query Guard、置信阈值和排序规则保持。

PG SQL 异常明确抛出 `KNOWLEDGE_RETRIEVAL_STORAGE_UNAVAILABLE`，不静默回落。无匹配结果且 fallback=false 时返回空结果；SQLite 与显式允许的旧记录 fallback 仍保留。错误信息不回传 SQL/DSN/凭据。

健康检查新增只读 `embedding_schema`。配置维度与 vector 固定维度不一致时，在 Provider 调用前拒绝。local-hash 仍显示 degraded，不能因 TXT 管线通过就显示正式语义检索健康。

`ensure_pgvector_schema()` 继续沿用原正式入口，不增加 Test 自动执行路径；增加现存 vector 固定维度检查，防止把 vector(128) 当作 vector(1536) 已完成初始化。需要正式维度升级时，必须另行批准已有 Schema/检索路径的兼容变更，不能静默 ALTER、清空数据或占用公众号016。

## 隔离验证结果

最终统一运行：**99 passed / 0 failed / 0 errors / 0 skipped**，2 项既有依赖弃用警告。不是 Full pytest、Stage2 或 PRIMARY Native Acceptance。

其中 20 项新增真实 PG 测试覆盖：

- 原缺列 SQLSTATE42703，完整 Migration001–015 初始化。
- TXT HTTP 上传202、正式解析与入库 ready、chunk_count>0、原文件与 chunk ID 可追溯。
- 原 migration ledger、列与索引快照不变，既有历史文件行保留。
- 跨 Tenant 查询无结果，跨 Tenant 文件替换拒绝，provider/model 不匹配无结果。
- 不相关内容无引用，Query Guard 拒绝不支持的事实要求。
- 127/129/1536 配置维度不匹配在 Provider/DDL 前拒绝。
- 错误长度、NaN、Infinity、空向量拒绝；旧 chunks 保留。
- Embedding 返回少批、多批、错维拒绝，原 ready 数据保留。
- 已有可选维度列的兼容写入。
- PG SQL 故障不会被旧文本 fallback 掩盖。
- OpenAI-compatible **Mock Embedding** 128 维正式 SQL 路径验证，无网络调用。
- local-hash 健康状态没有冒充正式语义 readiness。
- 文案及活动策划 AgentService → 签名 Runtime token → 真实 PlatformMCPService → PG 检索 → Trace/结果联动。

其余定向回归：test_knowledge_ingestion、test_reindex_knowledge_v1_4、test_rag_v1_3_eval、test_grounded_writing_runtime、test_activity_plan_runtime、test_image_generation_provider。

Agent 联动使用脚本化 Runtime，**不是实际模型 E2E**。输入只问“星槐计划的独有核验代码”，没有把答案或文档塞进 Prompt。Runtime double 实际调用 MCP 检索，从文档取得 `RAGZETA4729`、蓝榆实验室，再由 AgentService 记录结果及 knowledge_context_used。它证明工具与结果链路，不证明模型会自主正确检索/引用；后者等待 06 独立 Provider 授权。

复现入口：

```text
在已安装对应本机依赖的独立 Linux 源码快照内执行：
PYTHONDONTWRITEBYTECODE=1 python -B tests/run_knowledge_pg16.py \
  tests/test_knowledge_ingestion.py tests/test_reindex_knowledge_v1_4.py \
  tests/test_rag_v1_3_eval.py tests/test_grounded_writing_runtime.py \
  tests/test_activity_plan_runtime.py tests/test_image_generation_provider.py --tb=short
```

Launcher 仅创建 `/private/tmp/ky-web-stage1-postgres.rag-*`、禁用 TCP、使用 owner-only socket，校验 PG16.6。每项 PG fixture 使用独立数据库并在结束时移除自身数据库；PG 在 finally 停止，日志目录保留。没有删除历史环境资源。

本轮最终证据：Linux `/var/tmp/knowledge-rag.RguoU4/rag-final.xml`，PG 日志 `/private/tmp/ky-web-stage1-postgres.rag-9j0gq052/postgres.log`。新 PG 测试对真实 HTTPTransport 显式拒绝；ASGI 上传在进程内执行。

测试期间两类已修正测试设施问题：Runtime double 缺少 MCP server 字段；Windows复制的 Skill 字节与锁不一致。前者只修测试，后者从精确 d8a Git archive 恢复隔离快照中的原始 Skill 字节，未修改 Skill/锁或降低校验。最终汇总不把这些早期失败计为成功。

## Native Guard 兼容性及部署条件

现有交接源码仍严格检查 Schema015 fingerprint 与完整 table set，例如 `active_extra/wechat_persistent_config_guard.py:203` 的 `PERSISTENT_NATIVE_SCHEMA_DRIFT` 和 `active/primary_guard.py:502`。本候选不补列，不改变该 schema 合同。隔离前后列/索引/ledger 相等是兼容性支持证据，**不是实际 Native Guard PASS**。

历史交接包包含旧 Schema015 Pin，但总控要求 06 提取当前真实 Pin；不能拿旧 Pin 代替本轮 attestation。还须核对 internal-test-staging-v1 的合法知识表写入范围，不能仅凭 schema 不变推断所有新业务行都被 Guard 接受。

不能把两个 Python 文件直接覆盖运行中的 d8a。即使无需 DDL，Source/Tree 与 Code Pins 仍变化，必须走精确后继发布：

1. 06 在独立只读事务提取实际 knowledge_chunks 类型/typmod/约束、vector 扩展版本、Migration001–015 checksum、无敏感值的 Embedding provider/model/dimension、当前 Guard/Policy SHA 与 Schema Pin。
2. 用 pgvector0.8.6 的合法独立 fixture 重跑本候选；核对原 Native 的 Schema、Source、合法知识写入与 Tenant 规则，不放宽 Guard。
3. 02/06 批准新的精确 Application successor、Seeding/Code Pin/启动恢复绑定。旧 d8a Approval/Seal 不改写；不夹带公众号016、Trial Tooling 或其他分支。
4. 单独批准 Test Source switch/启动操作和知识上传验收；本轮未执行。按既有正式 Release/Recovery 流程切换，不手工覆盖文件。
5. 运行真实上传、解析、检索和 trace 核验。local-hash 只能关闭功能管线问题；正式语义 Embedding 及真实 Agent 质量需要独立模型预算与网络授权。

## 安全回退和仍未完成的验收

本候选不引入新 Schema/Data Contract；返回旧 d8a 不需要反向 Migration，也不能删除合法文件/chunks。但旧 d8a 的缺列写入问题会重现，因此不能把旧源回退后的 RAG 宣称可用。是否允许恢复旧 Source 仍须依据真实新数据和既有 Exact Predecessor/Native Guard 核验，未获得本轮线上回退授权。

仍待完成：当前 Schema Pin 与 pgvector0.8.6 精确兼容、完整 Native 后继验收、真实模型自主检索引用。不得承诺现有 vector(128) 可直接承载正式1536维 Provider。没有发布或安装资格。

PRIMARY Changes=0；PRIMARY DDL=0；Source Switch=0；Service Restart=0；Production Changes=0；真实 Provider/WeChat/Image Calls=0。无真实 Secret 读取、修改或提交。
