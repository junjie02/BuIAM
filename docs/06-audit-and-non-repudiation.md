# 审计与不可抵赖

## 审计数据表

| 表 | 内容 |
|----|------|
| `audit_logs` | 每次委托决策 (allow/deny)，含 decision_detail JSON |
| `auth_events` | 每次 Token 验证事件，含签名/过期/吊销状态 |
| `delegation_chain` | 人类可读的委托链 (from_actor → to_agent) |
| `delegation_credentials` | 签名的 VC 形态凭证 (完整加密材料) |
| `intent_tree` | 签名的意图节点 (含 intent_commitment) |
| `did_documents` | DID Document (身份白名单) |

## 审计查询 API

```
GET /audit/logs              — 所有审计日志 (可按 trace_id 过滤)
GET /audit/auth-events       — 所有认证事件 (可按 trace_id/agent_id/decision 过滤)
GET /audit/traces/{trace_id} — 完整 trace 视图:
    ├── logs                  — 委托决策
    ├── chain                 — 可读委托链
    ├── delegation_credentials — 签名凭证
    ├── auth_events           — 认证事件
    └── intent_tree           — 意图链
GET /audit/traces/{trace_id}/chain       — 仅委托链
GET /audit/traces/{trace_id}/credentials  — 仅凭证
GET /audit/traces/{trace_id}/intent-tree  — 仅意图树
GET /audit/intent-nodes/{node_id}        — 单个意图节点
```

## 审计覆盖

**所有失败都写审计** (所有 deny 决策 + 所有 auth 拒绝事件):
- Token 失败 (401): auth_event (identity_decision=deny) + audit_log (decision=deny)
- Credential/Intent 失败 (403): audit_log (decision=deny, decision_detail)
- Agent 失败 (404): audit_log (decision=deny)
- 任务取消 (409): audit_log (decision=deny, reason="TASK_CANCELLED")

## 不可抵赖性

委托凭证链和意图链都提供加密学的不可抵赖证明：

1. **凭证不可抵赖**: 每个 DelegationCredential 包含 issuer 的签名 → 授权方不可否认授权行为
2. **意图不可抵赖**: 每个 IntentNode 包含 actor 的签名 → actor 不可否认其意图
3. **哈希链完整性**: 所有 credential_id 通过哈希链互连 → 无法插入/删除中间节点
4. **Content hash**: 每个凭证的 content_hash 可重算 → 内容不可篡改

### 验证工具

```powershell
python scripts/security/verify_identity_vc.py --json  # DID/Token/VC 端到端验证
python scripts/security/find_security_node.py --credential-id <id>  # 凭证溯源
python scripts/security/find_security_node.py --intent-node-id <id>  # 意图溯源
```
