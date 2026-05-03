# Token 生命周期

## 签发

`POST /identity/tokens`:
```json
{
  "agent_id": "user_123",
  "actor_type": "user",
  "capabilities": ["web.public:read"],
  "ttl_seconds": 3600
}
```

前置条件：DID Document 必须已在白名单中注册 (`AGENT_DID_NOT_REGISTERED`)。

## JWT 结构

```
Header: {"alg":"BUIAM-RS256","kid":"did:buiam:user_123#key-1","typ":"JWT"}
Claims: {
  "jti": "tok_<uuid>",
  "sub": "user_123",
  "agent_id": "user_123",
  "actor_type": "user",
  "capabilities": ["web.public:read"],
  "sub_did": "did:buiam:user_123",
  "signing_kid": "did:buiam:user_123#key-1",
  "iat": <issued_at>,
  "exp": <expires_at>
}
```

## 验证流程

`inspect_token()` (`app/identity/jwt_service.py`):

| 步骤 | 检查 | 失败错误码 |
|------|------|-----------|
| 1 | 算法允许 (BUIAM-RS256 / BUIAM-MLDSA-65) | `AUTH_TOKEN_INVALID` |
| 2 | JWT 签名验证 (via DID resolver) | `AUTH_TOKEN_SIGNATURE_INVALID` |
| 3 | kid DID 与 claims sub_did 匹配 | `AUTH_TOKEN_SUBJECT_MISMATCH` |
| 4 | issuer = "buiam.local" | `AUTH_TOKEN_ISSUER_MISMATCH` |
| 5 | audience = "buiam.a2a" | `AUTH_TOKEN_AUDIENCE_MISMATCH` |
| 6 | exp 未过期 | `AUTH_TOKEN_EXPIRED` |
| 7 | JTI 已注册 (在 token store 中) | `AUTH_TOKEN_JTI_NOT_REGISTERED` |
| 8 | 未撤销 | `AUTH_TOKEN_REVOKED` |
| 9 | 根凭证完整性 | `AUTH_CREDENTIAL_INVALID` |

## 过期语义（弱语义）

Token 过期后：
- 阻止新请求 (401 `AUTH_TOKEN_EXPIRED`)
- 不撤销已颁发的委托凭证
- **不取消已开始的任务** — 任务在启动时已经过验证

## 吊销语义（强语义）

Token 吊销 (`POST /identity/tokens/{jti}/revoke`)：
- 标记 token 为 revoked
- 级联撤销该 token 关联的所有后代委托凭证
- **取消所有该 trace 中正在运行的任务** (409 `TASK_CANCELLED`)

## Token Introspection

`POST /identity/tokens/introspect`:
```json
// Request: {"token": "<jwt>"}
// Response:
{
  "active": true,
  "agent_id": "user_123",
  "actor_type": "user",
  "capabilities": ["web.public:read"],
  "exp": 1777736751,
  "jti": "tok_<uuid>",
  "credential_id": "<root credential id>",
  "root_credential_id": "<same>"
}
```
