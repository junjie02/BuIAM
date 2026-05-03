# 意图链 (Intent Chain)

## 概念

意图链跟踪用户请求在经过每个 Agent 时的语义演变。每次 A2A 调用都生成一个签名的 IntentNode，链接到父节点形成链。

## IntentNode 结构

```json
{
  "node_id": "sha256(canonical_content)",
  "parent_node_id": null,
  "actor_id": "user_123",
  "actor_type": "user",
  "target_agent_id": "doc_agent",
  "task_type": "generate_report",
  "intent_commitment": {
    "intent": "Generate a Feishu collaboration report",
    "description": "...",
    "data_refs": ["contact", "calendar"],
    "constraints": ["enterprise data only"]
  },
  "signature": "<actor's signature>",
  "signature_alg": "BUIAM-RS256"
}
```

## 意图生成

`app/intent/generator.py`:
- Root Task: 从用户请求生成 root intent（actor_type=user）
- A2A Call: 从 agent 上下文生成 child intent（actor_type=agent）
- Provider 可选：mock（确定性）、openai、anthropic

## 意图漂移检测

`app/intent/judge.py` 比较 child intent 与 root/parent intent：

- **Consistent**: 子意图与父意图/根意图一致 → 允许
- **Drifted**: 子意图偏离 → `INTENT_DRIFTED` 拒绝 (FATAL)

## 链验证

`validate_and_record_intent_node()` (`app/intent/service.py`):

| 检查 | 错误码 | 类型 |
|------|--------|------|
| node_id 哈希不匹配 | `INTENT_CHAIN_INVALID` | FATAL |
| 签名无效 | `INTENT_SIGNATURE_INVALID` | FATAL |
| actor 不匹配 (root 需 user, child 需 agent) | `INTENT_ACTOR_MISMATCH` | FATAL |
| 父节点未找到 | `INTENT_PARENT_NOT_FOUND` | FATAL |
| 跨 trace 复用 | `INTENT_CHAIN_INVALID` | FATAL |
| 循环检测 | `INTENT_CHAIN_INVALID` | FATAL |
| 意图漂移 | `INTENT_DRIFTED` | FATAL |
| Judge 调用失败 | `INTENT_JUDGE_FAILED` | FATAL |
