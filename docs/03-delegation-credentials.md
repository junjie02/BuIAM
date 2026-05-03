# 委托凭证链 (Delegation Credentials)

## VC 结构

每一条委托凭证都是 VC-shaped 结构：

```json
{
  "credential_id": "sha256(parent_id + canonical_content)",
  "parent_credential_id": null,
  "root_credential_id": "<same as credential_id for root>",
  "issuer_id": "user_123",
  "subject_id": "doc_agent",
  "issuer_did": "did:buiam:user_123",
  "subject_did": "did:buiam:doc_agent",
  "capabilities": ["web.public:read"],
  "user_capabilities": ["web.public:read"],
  "iat": 1777733151,
  "exp": 1777736751,
  "proof_verification_method": "did:buiam:user_123#key-1",
  "proof_signature": "<issuer's signature>",
  "signature_alg": "BUIAM-RS256",
  "content_hash": "<sha256 of canonical self-content>"
}
```

## 哈希链 ID

`credential_id = sha256(parent_credential_id + canonical_json(self_content))`

- Root VC: parent = `"ROOT"` 常量
- Child VC: parent = 父凭证的 credential_id

这使得整条链可以通过 credential_id 前后链接、追溯。

## 签名方向

**每次委托都由授权方（issuer）用自己的私钥签名**：

| 场景 | issuer | subject | 签名私钥 |
|------|--------|---------|---------|
| Root VC (Token 签发) | 实体自身 | 实体自身 | 实体私钥 |
| Root Task 委托 | user_123 | doc_agent | user_123 私钥 |
| A2A 委托 | doc_agent | enterprise_data_agent | doc_agent 私钥 |

验证时用 issuer_did 指向的公钥验证，确保"授权方确实签署了这次委托"。

## 凭证构建

`build_delegation_credential()` (`app/delegation/credential_crypto.py`):
1. 计算 parent_credential_id 和 root_credential_id
2. 构建 unsigned credential
3. `canonical_json(credential_self_content(unsigned))` → 签名输入
4. 用 issuer 私钥签名 (`rsa_sign_with_kid` / `mldsa_sign_with_kid`)
5. 计算 content_hash 和 credential_id
6. 返回完整签名凭证

## 链验证

`validate_credential_branch()` (`app/delegation/service.py`) 递归回溯：

1. **完整性**：`verify_credential_integrity()` — 重算 hash + 验证签名
2. **吊销**：检查 `revoked` 标记（当前 + 所有祖先）
3. **过期**：检查 `exp`（当前 + 所有祖先）
4. **能力收缩**：子能力 ⊆ 父能力
5. **根一致性**：所有节点 root_credential_id 一致

## 能力交集

`intersect_capabilities(caller_token_caps, target_caps, requested, user_caps)`:

```
effective = caller_token ∩ target_agent ∩ requested ∩ user
missing = requested - effective
```

失败时通过 `missing_by` 拆分缺失来源：
- `caller_token`: caller 令牌不具备的能力
- `target_agent`: 目标 agent 不支持的能力
- `user`: 用户未被授予的能力
