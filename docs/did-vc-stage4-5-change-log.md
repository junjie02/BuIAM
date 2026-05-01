# DID+VC 升级实施记录（阶段4 & 阶段5）

日期：2026-05-01

## 阶段4：DelegationCredential 升级为 VC-shaped

### 目标
在不破坏现有授权链不变量（hash chain、parent/root、能力缩窄、过期/撤销）的前提下，把委托凭证结构升级为 VC 风格。

### 主要改动

1. `app/protocol.py`
- `DelegationCredential` 新增字段：
  - `issuer_did`
  - `subject_did`
  - `vc_context`
  - `vc_type`
  - `credential_subject`
  - `proof_verification_method`
  - `proof_signature`

2. `app/delegation/credential_crypto.py`
- canonical 内容切换为 VC-shaped 字段集合（仍参与 content hash 与 credential_id 计算）。
- `build_delegation_credential`：
  - 生成 `issuer_did/subject_did`
  - 生成 `proof_verification_method`（`did...#key-1`）
  - 生成 `credential_subject`（capabilities、delegated user、trace/request、parent/root）
  - proof 签名写入 `proof_signature`，并同步到 `signature` 兼容旧链路。
- `verify_credential_integrity`：
  - 增加 `proof_signature` 与 `signature` 一致性校验（若两者都存在）
  - 使用 DID/kid 验签 proof
- `auth_context_from_credential` 补齐 DID 字段映射。

3. `app/store/schema.py`
- `delegation_credentials` 表新增列（增量迁移，使用 `ensure_column`）：
  - `issuer_did`, `subject_did`, `vc_context`, `vc_type`, `credential_subject`, `proof_verification_method`, `proof_signature`

4. `app/store/delegation_credentials.py`
- upsert/get/list 的存取逻辑支持上述新增字段。
- 对旧数据做兼容读取（缺失列时提供默认值）。

---

## 阶段5：Gateway/Delegation DID 一致性与错误映射强化

### 目标
把“Bearer 身份 + 认证上下文 + 凭证主体”一致性从 `agent_id` 扩展到 DID 语义，并确保凭证校验错误稳定映射为 403。

### 主要改动

1. `app/delegation/service.py`
- `validate_auth_context_credential` 增加 DID 一致性校验：
  - `auth_context.subject_did` 与 `credential.subject_did`（或推导 DID）必须一致。
- `build_child_auth_context` 在无父凭证分支也补 DID 字段：
  - `subject_did/agent_did/signing_kid`

2. `app/gateway/routes.py`
- `trusted_auth_context_for_envelope` 增加 DID 交叉校验：
  - bearer DID 与 envelope 当前凭证 DID 不一致时，返回 `AUTH_CREDENTIAL_DID_MISMATCH`（403）。
- 凭证校验异常继续通过 `http_error(...)` 映射为 403，避免落为 500。

---

## 测试与结果

执行：

- `tests/security/test_delegation_chain.py`
- `tests/security/test_a2a_identity.py`
- `tests/security/test_token_lifecycle.py`

结果：

- **19 passed**

说明：
- 中途出现 1 个回归（篡改 `signature` 未被优先拦截，先触发后续 intent mismatch），已通过 `verify_credential_integrity` 中的 `signature/proof_signature` 一致性校验修复。

---

## 当前状态

- 阶段4：已完成。
- 阶段5：已完成。
- 现有安全回归通过，代码处于可继续推进阶段6（Intent DID 化）的状态。
