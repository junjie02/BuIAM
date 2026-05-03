# DID + VC 升级实施记录（阶段0 & 阶段1）

日期：2026-05-01
范围：阶段0（基线收敛）+ 阶段1（DID 本地生成/注册）

## 一、目标

- 阶段0：清理冲突与基线不稳定点，恢复认证/审计链路可测状态。
- 阶段1：实现演示级 DID 本地生成与 DID Document 注册能力。

---

## 二、阶段0完成项（基线收敛）

### 1) 清理冲突文件并统一最小模型

- `app/protocol.py`
  - 清理冲突标记。
  - 统一 `AuthContext` 为当前安全链路使用的字段集合：
    - `jti/sub/exp/agent_id/actor_type/delegated_user/capabilities/user_capabilities/credential_id/parent_credential_id/root_credential_id/sig`

- `app/identity/jwt_service.py`
  - 清理冲突标记与重复定义。
  - 统一 `issue_token/inspect_token/verify_token` 流程。
  - 保留并对齐当前 delegation credential 根凭证签发逻辑。

- `app/identity/routes.py`
  - 清理冲突标记。
  - 对齐 token 签发、introspect、revoke 的当前结构。

- `app/main.py`
  - 清理冲突标记。
  - 保留 `lifespan` 启动逻辑（`init_schema + register_demo_agents`）。

### 2) 修复基线回归中断点

- `app/store/audit.py`
  - 修复 `list_logs` 因错误缩进导致返回 `None` 的问题。
  - 恢复审计日志查询正常返回 `list[AuditLog]`，避免安全测试中 `trace["logs"]` 为 `None`。

---

## 三、阶段1完成项（DID 本地生成/注册）

### 1) 新增 DID 构建模块

- 新增 `app/identity/did.py`
  - `build_did(subject_id)`：生成 `did:buiam:<subject_id>`
  - `build_verification_method_id(did, key_label)`：生成 `did#key-1`
  - `build_did_document(subject_id, key_label="key-1")`：
    - 从本地公钥构建 DID Document
    - 输出 `verificationMethod/authentication/assertionMethod/capabilityDelegation`
    - 包含简要 metadata（subject_id + 公钥指纹）

### 2) 新增 DID Registry 持久化

- 新增 `app/store/did_registry.py`
  - `upsert_did_document(...)`
  - `get_did_document(did)`
  - `list_did_documents()`

- `app/store/schema.py`
  - 新增表 `did_documents`：
    - `did`(PK), `subject_id`, `document_json`, `created_at`, `updated_at`

### 3) 接入 demo 启动注册流程

- `app/registry/bootstrap.py`
  - 在 `register_demo_agents()` 内：
    - 为 `USER_ID` + 所有 demo agent 生成 DID Document 并写入 `did_documents`
    - 保留原有 agent 注册逻辑

- `scripts/bootstrap_demo_agents.py`
  - 输出扩展为：
    - `agents`
    - `did_documents`

---

## 四、验证结果

### 1) 静态检查

- 已检查并确认以下文件无 lints：
  - `app/main.py`
  - `app/protocol.py`
  - `app/identity/jwt_service.py`
  - `app/identity/routes.py`
  - `app/identity/did.py`
  - `app/store/did_registry.py`
  - `app/registry/bootstrap.py`
  - `app/store/schema.py`
  - `app/store/audit.py`
  - `scripts/bootstrap_demo_agents.py`

### 2) 回归测试

执行：

- `tests/security/test_a2a_identity.py`
- `tests/security/test_delegation_chain.py`

结果：

- **15 passed**

---

## 五、兼容性与影响说明

- 本次 DID 能力为“增量接入”：
  - 未强制切换 JWT/Delegation/Intent 到 DID strict 验签；
  - 仅新增 DID 数据与注册流程，保持当前业务链路可运行。

- 阶段0完成后，基线已恢复稳定，可继续推进阶段2（Gateway resolver）和阶段3（JWT DID 化）。

---

## 六、下一步建议

1. 阶段2：新增 `did_resolver`，支持 `did` 与 `did#kid` 解析。
2. 阶段3：JWT 增加 `subject_did/agent_did/signing_kid`，验签从 `agent_id` 切到 resolver。
3. 阶段4：将 `DelegationCredential` 升级为 VC-shaped 字段，同时保持 hash-chain 不变量。
