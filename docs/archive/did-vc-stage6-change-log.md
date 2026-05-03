# DID+VC 升级实施记录（阶段6）

日期：2026-05-01

## 目标
推进 Intent 链路的 DID 化签名与验证，并保证不破坏当前安全回归基线。

## 本次实现

### 1) Intent 签名迁移到 DID/kid 验签（兼容形态）

- 修改 `app/intent/crypto.py`
  - `intent_self_content` 中加入 DID 语义信息：
    - `actor_did = did:buiam:<actor_id>`
    - `proof_verification_method = did:buiam:<actor_id>#key-1`
  - `build_signed_intent_node` 改用 `rsa_sign_with_kid(...)` 签名。
  - `verify_intent_node_signature` 改用 `rsa_verify_with_kid(...)` 验签。

### 2) Intent actor 校验与根/子节点规则保持

- 修改 `app/intent/service.py`
  - `validate_actor` 在 root 节点场景使用 `delegated_user 或 agent_id` 作为预期用户主体，避免 root token 场景兼容问题。
  - child 节点继续要求 `actor_id == auth_context.agent_id`。

### 3) 兼容策略说明

为避免影响现有协议面和大量下游调用，本次先采用“**结构内嵌 DID 语义，模型字段暂不扩展**”策略：

- 暂未在 `IntentNode` 顶层新增 `actor_did` / `proof_verification_method` 持久字段；
- DID/kid 信息当前体现在签名输入内容与验签流程中；
- 下一步可在不破坏兼容的前提下把这两个字段显式落入 `protocol` + `store/intent_tree`。

## 回归验证

执行：

- `tests/security/test_intent_chain.py`
- `tests/security/test_delegation_chain.py`
- `tests/security/test_a2a_identity.py`

结果：

- **22 passed**

## 下一步建议（阶段6.1）

1. 将 `IntentNode` 显式扩展 `actor_did` / `proof_verification_method` 字段；
2. `store/intent_tree` 加列并持久化；
3. 新增针对 DID 字段篡改的安全测试（独立于现有签名篡改测试）。
