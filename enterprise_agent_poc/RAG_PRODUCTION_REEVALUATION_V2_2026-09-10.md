# RAG Production Re-evaluation V2

验收日期：2026-09-10
范围：Enterprise Knowledge Phase A / P0 的生产部署、实际策略加载与固定 40 条数据集复测。

## 可确认的发布事实

| 项目 | 结果 |
| --- | --- |
| 已推送远程提交 | `9fc5ea8` 已到达 `origin/master` |
| 本地后续路径修正 | `2c74bf2` 已本地提交；远程推送在网络重置后未确认成功 |
| 生产代码发布方式 | 保留生产历史工作树，仅备份并叠加本次 RAG 源文件 |
| 生产回滚备份 | `/opt/enterprise-agent-workbench/backups/rag-p0-a038d82-20260910` |
| 服务状态 | API、MCP、Worker 重启后均为 `active` |
| 健康检查 | `http://127.0.0.1:18090/api/health` 为 `ok` |

## 实际运行策略状态

生产工作树优先加载后的脱敏诊断输出：

```json
{
  "guard_policy_version": "query-guard-v2",
  "min_final_score": 0.31,
  "min_vector_score": 0.42,
  "query_guard_enabled": true,
  "result_margin": 0.02,
  "runtime_config_fingerprint": "5958f0ed7b94d9432d90f22b2432dd88229054c6cc84b9c5197fcc3c7f9648b1"
}
```

这确认先前“代码已部署但评测仍表现为旧策略”的一个根因：直接执行评测脚本时，venv 已安装的旧 `app` 包可能优先于生产工作树。P0 修正以脚本内仓库根目录置顶及包装器 `PYTHONPATH` 固定工作树优先级。

## 40 条复测情况

用户提供的生产终端输出确认，第二次运行已完整完成：`status=completed`、固定数据集版本为 `rag-v1.3`、`section_alias_version=none`、case 数量为 40。

| 指标 | P0 前基线 | 本次生产复测 | 变化 |
| --- | ---: | ---: | ---: |
| answerable_acceptance_recall | 1.00 | 1.00 | 0.00 |
| section_recall_at_k | 0.00 | 0.00 | 0.00 |
| top_1_section_accuracy | 0.00 | 0.00 | 0.00 |
| grounded_precision | 0.00 | 0.00 | 0.00 |
| no_answer_rejection_rate | 0.10 | 1.00 | +0.90 |
| case_pass_rate | 0.025 | 0.25 | +0.225 |

负例行为：`n01` 至 `n10` 均为 `accepted=false`、`rejection_reason=query_guard`、结果列表为空。类别覆盖为：价格无依据（2）、私密或凭据（2）、绝对承诺（2）、未公开企业信息（2）、明显越界（1）、显式不存在实体（1）。

`section_alias_version` 保持 `none`，因此该次结果不得被解读为正例 Grounding 已通过。候选审核清单见 [RAG_SECTION_ALIAS_REVIEW.md](RAG_SECTION_ALIAS_REVIEW.md)。

## 阶段结论

**BLOCKED。**

Query Guard 的实际加载和同一固定 40 条数据集完整生产复测均已确认，且负例拒答目标已达成。Phase A 仍被正例章节 Grounding 阻塞：正式 section alias 尚未审核，`section_recall_at_k`、`top_1_section_accuracy`、`grounded_precision` 均为 0。不得开始 Tenant B、浏览器 E2E、Skill Registry 或 Agent 扩展。

## 解除阻塞所需动作

1. 由知识库内容负责人审核 [RAG_SECTION_ALIAS_REVIEW.md](RAG_SECTION_ALIAS_REVIEW.md)，发布受控的 `rag-section-aliases-v1`。
2. 使用已审核 alias 对完全相同的 40 条数据集复跑，确认正例章节命中、Top-1 与 Grounded Precision。
3. 在 alias 指标达到可接受门槛前，不得以 Query Guard 的成功替代完整 RAG 生产验收。
