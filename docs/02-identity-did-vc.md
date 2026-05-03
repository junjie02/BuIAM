# DID 身份体系

## DID 格式

BuIAM 使用自定义 DID 方法：`did:buiam:{subject_id}`

例如：`did:buiam:user_123`、`did:buiam:doc_agent`

## DID Document 结构

符合 W3C DID Core 规范，包含以下字段：

```json
{
  "@context": ["https://www.w3.org/ns/did/v1"],
  "id": "did:buiam:doc_agent",
  "verificationMethod": [{
    "id": "did:buiam:doc_agent#key-1",
    "type": "JsonWebKey2020",
    "controller": "did:buiam:doc_agent",
    "publicKeyJwk": {
      "kty": "RSA",
      "n": "...",
      "e": "AQAB"
    }
  }],
  "authentication": ["did:buiam:doc_agent#key-1"],
  "assertionMethod": ["did:buiam:doc_agent#key-1"],
  "capabilityDelegation": ["did:buiam:doc_agent#key-1"],
  "service": [{
    "id": "did:buiam:doc_agent#a2a-service",
    "type": "A2A-Service",
    "serviceEndpoint": "http://127.0.0.1:8011/a2a/tasks"
  }],
  "metadata": {
    "subject_id": "doc_agent",
    "fingerprint": "<sha256-of-jwk>"
  }
}
```

### 密钥类型

| 类型 | kty | 算法 | 环境变量 |
|------|-----|------|---------|
| RSA (默认) | `RSA` | BUIAM-RS256 | `BUIAM_USE_MLDSA=false` |
| ML-DSA (后量子) | `ML-DSA` | BUIAM-MLDSA-65 | `BUIAM_USE_MLDSA=true` |

## DID 注册流程

```
客户端 (examples/generate_identity.py)          Gateway (POST /identity/did-register)
  │                                                │
  ├─ 1. 本地生成密钥对 (RSA/ML-DSA)                │
  ├─ 2. 构建 DID Document                         │
  ├─ 3. 用私钥签名 DID Document (proof)           │
  │                                                │
  ├─ 4. POST {did_document, proof} ──────────────►│
  │                                                ├─ 5. 校验 DID 格式 (did:buiam:*)
  │                                                ├─ 6. 校验 verificationMethod + JWK
  │                                                ├─ 7. 用 document 内的公钥验证 proof
  │                                                ├─ 8. 重复检查 (409 if exists)
  │                                                └─ 9. 存入 did_documents 表 (白名单)
  │◄────────────────── 200/400/409 ────────────────┤
```

### 客户端生成命令

```powershell
python examples/generate_identity.py --subject-id doc_agent \
    --service-endpoint http://127.0.0.1:8011/a2a/tasks --submit
```

## DID 解析

`app/identity/did_resolver.py` 提供两个核心函数：

- `resolve_did_document(did: str)` → 从 `did_documents` 表查询完整 DID Document
- `resolve_verification_method(vm_id: str)` → 解析 `did:buiam:xxx#key-1` 格式，返回公钥 JWK

解析器用于 JWT 签名验证、委托凭证签名验证等安全关键路径。

## DID Proof 机制

DID Document 提交时附带的 proof 是自签名，证明提交者持有私钥：

- **创建**：`create_did_proof(did_document, subject_id)` — 用 subject 私钥对 canonical JSON 签名
- **验证**：`verify_did_proof(did_document, proof)` — 从 document 内提取公钥验证签名（不依赖数据库）

## Token 与 DID 的绑定

Token 签发 (`POST /identity/tokens`) 前检查：
- `get_did_document(build_did(agent_id))` 必须存在 → 只有已注册 DID 的身份才能获取 Token

Token 验证时：
- JWT header 中的 `kid` 指向 DID verificationMethod (如 `did:buiam:user_123#key-1`)
- 通过 DID 解析器获取公钥 → 验证 JWT 签名
- 验证 `sub_did` 与 DID Document 的 `id` 一致
