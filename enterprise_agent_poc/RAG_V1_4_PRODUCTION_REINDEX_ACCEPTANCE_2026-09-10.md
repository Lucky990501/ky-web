# Enterprise Knowledge V1.4 生产 Reindex 验收

验收日期：2026-09-10  
生产租户：`zhiy-e-intelligence`  
代码提交：`2bee63d`

## 1. 结论

**PASS**。生产知识索引已从 `v1` 完整重建为 `rag-index-v2`，重建前后均为 287 个 Chunk；数据库回滚快照、代码备份、版本完整性校验、服务重启与健康检查均通过。

## 2. 发布前基线

| 项目 | 结果 |
| --- | --- |
| Knowledge File | 1 |
| Chunk | 287 |
| Embedding Version | `v1`: 287 |
| 结构化 Metadata 覆盖 | 0 |
| 弱标题（`记录 N`） | 124 |
| V1.4 分类投影 | 259/287（90.2%）可直接归类；其余保留为 unknown，不强行标注 |

源数据诊断发现弱标题记录仍保留“一级分类”或“原始日期 / 标准日期 / 年份 / 序号”等结构字段。V1.4 仅根据源字段和通用结构规则生成 canonical metadata，不使用评测返回结果生成标签，也未扩大 Section Alias。

## 3. 安全与回滚

| 项目 | 结果 |
| --- | --- |
| 生产代码备份 | `/opt/enterprise-agent-workbench/backups/rag-v1-4-2bee63d-20260910` |
| 数据库快照表 | `rag_index_backup_v1_4_2bee63d_20260910` |
| 快照 Chunk 数 | 287 |
| Reindex 策略 | 32 条分批向量化；全部成功后才替换单文件旧索引 |
| 在线可用性 | 强制 Reindex 期间文件保持 `ready`；失败不覆盖旧索引 |

## 4. Reindex 结果

| 项目 | 结果 |
| --- | --- |
| Files Reindexed | 1 |
| Chunks Before / After | 287 / 287 |
| Embedding Version | `rag-index-v2`: 287/287 |
| Metadata Schema | `knowledge-metadata-v1`: 287/287 |
| canonical_section | 287/287 |
| record_type / entity_type | 287/287 |
| source_file_id / source_filename | 287/287 |
| section / sheet_name | 287/287 |
| year | 218/287 |
| event_name | 24/287 |
| person_name | 7/287 |

Canonical 分布：AI知识库 30、使用说明 4、历史日期索引 132、嘉宾档案 6、推荐阅读 52、活动场次 24、活动流程规则 39。

## 5. 运行验证

| 检查 | 结果 |
| --- | --- |
| 本地自动化测试 | 41 passed，1 个既有 Starlette 弃用告警 |
| 本地 Python 编译 | PASS |
| 服务器端发布文件编译 | PASS |
| `enterprise-agent-api.service` | active |
| `enterprise-agent-mcp.service` | active |
| `enterprise-agent-worker.service` | active |
| `http://127.0.0.1:18090/api/health` | `status: ok`、`knowledge: ok` |

## 6. 发布说明

`2bee63d` 已在本地 `master` 创建。向 GitHub `origin/master` 推送时连接被远端重置，生产发布不依赖此次直推，后续仍需重试同步；该网络问题不影响本次生产 Reindex 的技术验收。

