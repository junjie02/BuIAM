# 错误处理分级

## 失败分类

```
FATAL（不可恢复）—— 立刻终止
├── Token 无效/过期/吊销           → 401
├── 凭证篡改/完整性失败            → 403
├── 意图链断裂                     → 403
├── 角色类型不匹配                 → 403
└── Agent 不可达/未注册            → 404/502

DENIED（可恢复）—— 告诉调用方原因，可重试
├── 能力不足                       → 403 recoverable=true
└── 未知能力名称                   → 403 recoverable=true
```

## HTTP 响应格式

### FATAL 错误
```json
401 {
  "detail": {
    "error_code": "AUTH_TOKEN_EXPIRED",
    "message": "token has expired"
  }
}
```
无 `recoverable` 字段 → 调用方直接报告用户。

### DENIED 错误（可恢复）
```json
403 {
  "detail": {
    "error_code": "AUTH_DELEGATION_DENIED",
    "reason": "...intersection: ['feishu.contact:read']",
    "effective_capabilities": [],
    "missing_capabilities": ["feishu.contact:read"],
    "missing_by": {
      "caller_token": [],
      "target_agent": ["feishu.contact:read"],
      "user": []
    },
    "recoverable": true,
    "suggested_agents": ["enterprise_data_agent", "doc_agent"]
  }
}
```

## 调用方决策树

```
拿到错误响应
  │
  ├── recoverable == true？
  │   ├── 有 suggested_agents → 换 target_agent_id 重试
  │   └── 无 suggested_agents → 修正请求参数重试
  │
  └── 无 recoverable 或 false → FATAL
      → 向上游/用户报告失败原因
      → 所有 FATAL 都有 audit log 可查
```

## suggested_agents 机制

当 `missing_by.target_agent` 非空时（即目标 agent 本身不支持这些能力），Gateway 自动查询注册表中具备所需能力的其他 agent：

`find_agents_with_capabilities(missing_caps, exclude_agent_id=target)` → 列出替代 agent 的 ID 和 endpoint。

## 审计缺口修复

以下之前缺失 audit log 的失败路径已修复：

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| `AGENT_NOT_REGISTERED` | 无 audit | `record_decision` 写入 |
| `AGENT_INACTIVE` | 无 audit | `record_decision` 写入 |
| `AUTH_ACTOR_TYPE_INVALID` (root_task) | 无 audit | `record_decision` 写入 |
| `CredentialValidationError` (append_hop) | 无 audit | `record_decision` 写入 |
