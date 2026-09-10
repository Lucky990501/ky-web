# Workbench Core V1｜Phase A Stage 10 Thread Resume 验收

日期：2026-09-10  
生产 Release：`20260910-9ffefc6`  
Git commit：`9ffefc669febbc52bb64c1bde4f3dc4bca2398b7`

## 修复说明

生产 Runtime 数据已固定在 `/opt/enterprise-agent-workbench/shared/runtime-data`，不再随不可变 Release 目录变化。历史 rollout 文件可正常读取时直接 `thread_resume`；若 App Server 明确返回 rollout 缺失或旧状态库与 session metadata 映射冲突，则只使用数据库中持久化的用户可见消息重建同 Agent thread，并原子更新 conversation 绑定。隐藏推理、凭据和 Token 不参与恢复。

## Same-Agent Resume 与 Runtime Restart

| Agent | Conversation | Turn 3 Run | 结果 | Thread 处理 |
| --- | --- | --- | --- | --- |
| Copywriting | `eb84ffea-3523-44bb-8174-fe723188c8ea` | `c232e8f4-0174-4d24-9438-a53466a3562a` | completed / final response | 原 thread `01a08b63-d0b3-7ed1-a4e8-571f632bf2c9` 直接恢复 |
| Campaign | `9a627c70-a44e-47c1-adad-cc568765d819` | `5bbc0988-f5bc-4ae6-afdc-49a194d7aefe` | completed / final response | 原 thread `01a08b64-5446-7041-a320-d48416b5ca5d` 直接恢复 |
| Image | `46f5eaa2-b744-4d5d-9c53-d1193529260f` | `b4d1c8eb-5da5-4e12-8c47-42d7c0b84711` | completed / image / final response | 旧 metadata 冲突后受控重建为 `01a08bc5-2872-7ed2-a077-1088d1dde86f` |

Worker Runtime 在 Turn 2 与 Turn 3 之间已显式重启。三个 Turn 3 均保持原 tenant、user、agent、conversation、runtime profile、runtime version 和 skill manifest 的正确绑定。图片 fallback Trace 完整记录：

`thread_resume_requested → thread_resume_unavailable → thread_start_requested → thread_started`

随后 `enterprise_config_get`、`asset_search`、`knowledge_search`、`image_generation` 均 completed，`final_response_received=true`。

## Cross-Agent Resume Denial

| 攻击 | HTTP | 结果 |
| --- | --- | --- |
| Image conversation → Copywriting Agent | 409 | DENY |
| Copywriting conversation → Campaign Agent | 409 | DENY |

两次请求均在任务创建前拒绝，没有跨 Agent thread 复用，也未扣除任务积分。

## 验证

- 本地完整测试：`45 passed`。
- 生产 API / MCP / Worker 健康。
- RAG Retrieval V1.4 未修改。

## 结论

**PASS**。Same-Agent Resume、Runtime Restart Resume 和 Cross-Agent Resume Denial 均满足 Phase A Gate。
