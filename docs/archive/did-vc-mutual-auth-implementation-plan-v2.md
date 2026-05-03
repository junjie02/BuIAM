# BuIAM DID + VC + 双向认证（抗中间人）分步实施方案（基于当前代码现状）

## 0. 背景与目标

你希望把当前项目从“`agent_id` + 本地公钥验签”升级为：

1. **DID 身份体系**：每个主体本地生成密钥对，构建 DID 与 DID Document；
2. **VC 化委托凭证**：保留现有授权链安全不变量，但外形与语义升级为 VC-shaped；
3. **认证升级为 DID/kid 验签**：JWT、委托凭证、IntentNode 都携带 DID + kid；
4. **防 MITM + 双向认证**：不仅验证调用方，也验证被调用方（以及链路绑定），避免 token/消息被中间人转用。

本方案按“**先修基线，再增量改造，再收口 strict**”执行，尽量不破坏已有业务链路和测试框架。

---

## 1. 基线现状（已读代码后的关键结论）

### 1.1 当前已有能力

- 网关核心入口：`app/gateway/routes.py`
- 委托凭证链：`app/delegation/credential_crypto.py`, `app/delegation/service.py`
- Token 认证：`app/identity/jwt_service.py`
- 意图节点签名与校验：`app/intent/*`
- 审计与认证事件：`app/store/audit.py`, `app/store/auth_events.py`

已有的安全基础（可复用）：

- 委托凭证 hash chain + parent/root 约束
- 能力缩窄校验
- 过期/撤销机制
- trace/request 审计链

### 1.2 当前风险与前置问题（必须先处理）

1. `app/identity/jwt_service.py` 与 `app/protocol.py` 存在冲突标记（`<<<<<<<` / `>>>>>>>`），需要先清理并统一数据模型；
2. 认证语义仍以 `agent_id` 为主，尚未统一到 `did + kid`；
3. 双向认证闭环不足（当前主要是单向 bearer 验证路径）。

> 结论：先做“**基线收敛**”，再做 DID/VC 才能避免后续回归爆炸。

---

## 2. 总体设计原则

1. **最小入侵**：优先新增字段和兼容映射，不立即删除旧字段；
2. **双栈期**：短期同时支持旧 `agent_id` 与新 `did/kid`，通过环境变量切 strict；
3. **安全不变量不降级**：
   - credential_id 可重算；
   - 子能力不超父能力；
   - 子 exp 不超父 exp；
   - 撤销级联与审计完整；
4. **双向认证必须绑定身份上下文**：token、proof、mTLS 主体、DID 一致。

---

## 3. 分步实施（建议 10 个阶段）

## 阶段 0：基线收敛（必须先做）

### 目标
清理冲突与模型分叉，恢复稳定测试基线。

### 改动

- 清理冲突文件：
  - `app/identity/jwt_service.py`
  - `app/protocol.py`
- 统一 `AuthContext` 最小字段集：
  - `jti/sub/exp/agent_id/actor_type/delegated_user/capabilities/user_capabilities/credential_id`
- 确保现有安全回归可运行：
  - `tests/security/test_a2a_identity.py`
  - `tests/security/test_delegation_chain.py`
  - `tests/security/test_intent_chain.py`

### 验收

- `pytest -q -p no:cacheprovider` 通过当前基线（或明确失败清单可追踪）。

---

## 阶段 1：DID 数据模型与本地生成流程

### 目标
实现“本地密钥 -> DID -> DID Document -> 注册 resolver”的演示闭环。

### 改动

新增：

- `app/identity/did.py`
  - `build_did(method, subject_name, public_key)`
  - `build_did_document(did, kid, public_key, purposes)`
- `app/store/did_registry.py`
- `app/store/schema.py` 增表 `did_documents`

改造：

- `scripts/bootstrap_demo_agents.py`
  - 为 `user/doc_agent/enterprise_data_agent/external_search_agent/system` 生成 DID 文档并注册。

### 验收

- 可查询所有主体 DID 文档；
- 每个主体至少一个 `verificationMethod`（如 `did:buiam:doc_agent#key-1`）。

---

## 阶段 2：Gateway 本地 DID Resolver

### 目标
统一通过 DID/kid 解析公钥。

### 改动

新增：

- `app/identity/did_resolver.py`
  - `resolve_did_document(did)`
  - `resolve_verification_method(kid_uri)`

改造：

- `app/identity/crypto.py`
  - 增加 `rsa_sign_with_kid(...)`
  - 增加 `rsa_verify_with_kid(...)`
  - 旧 `rsa_sign/rsa_verify` 兼容代理。

### 验收

- 合法 `kid` 正常验签；
- 非法/跨 DID kid 拒绝。

---

## 阶段 3：JWT DID 化（认证入口）

### 目标
JWT 从 `agent_id` 验签升级到 `did + kid` 验签。

### 改动

- `app/identity/jwt_service.py`
  - header `kid = <did>#key-1`
  - claims 增加：`sub_did`, `agent_did`, `signing_kid`
  - `inspect_token` 走 resolver 验签
  - 校验：`kid 所属 DID == claims.sub_did`

- `app/protocol.py`
  - `AuthContext` 增加：`subject_did`, `agent_did`, `signing_kid`

### 验收

- token kid 冒用被拒绝；
- 旧 token 在非 strict 模式仍可兼容。

---

## 阶段 4：委托凭证 VC-shaped 升级（保留链）

### 目标
不推翻现有链式授权逻辑，将 `DelegationCredential` 升级为 VC 语义。

### 改动

- `app/protocol.py`：`DelegationCredential` 增字段
  - `issuer_did`
  - `subject_did`
  - `vc_context`
  - `vc_type`
  - `credential_subject`（含 capabilities、delegated_user、trace/request、parent/root 信息）
  - `proof_verification_method`
  - `proof_signature`

- `app/delegation/credential_crypto.py`
  - canonical content 改为 DID/VC 字段映射
  - `credential_id = hash(parent + canonical_content)` 规则保持

- `app/store/delegation_credentials.py` + `app/store/schema.py`
  - 增加 `issuer_did/subject_did/proof_vm/vc_json`

### 验收

- 新旧凭证都可校验（兼容期）；
- 篡改字段依旧触发拒绝。

---

## 阶段 5：Gateway + Delegation 服务 DID 联动

### 目标
请求身份与凭证身份统一到 DID 语义。

### 改动

- `app/gateway/routes.py`
  - `trusted_auth_context_for_envelope` 改为 DID 主体一致性校验；
  - `CredentialValidationError -> HTTP 403` 显式映射（避免 500）。

- `app/delegation/service.py`
  - `validate_auth_context_credential` 检查 `subject_did` 一致；
  - `authorize/build_child_auth_context` 改用 DID 语义。

- `app/protocol.py`
  - `DelegationEnvelope` 增加 `caller_did`, `target_did`。

### 验收

- Bearer DID 与 credential subject DID 不一致时拒绝；
- 相关测试返回 403，不出现 500。

---

## 阶段 6：Intent 节点 DID 化

### 目标
Intent 节点签名与主体校验也迁移到 DID/kid。

### 改动

- `app/protocol.py`：IntentNode 增 `actor_did`, `proof_verification_method`
- `app/intent/crypto.py`：签验签改走 resolver
- `app/intent/service.py`：`validate_actor` 用 DID 对齐

### 验收

- root/child intent 节点完整入树；
- 篡改 actor_did/kid 触发拒绝。

---

## 阶段 7：抗 MITM + 双向认证（核心安全）

### 目标
把认证从单向升级为双向，并绑定连接身份，防中间人转用。

### 7.1 传输层与 mTLS

- Gateway->Agent 强制 HTTPS 验证：
  - `BUIAM_TLS_CA_FILE`
  - `BUIAM_TLS_CLIENT_CERT`
  - `BUIAM_TLS_CLIENT_KEY`
- mTLS 运行时检查：
  - `BUIAM_MTLS_REQUIRED=true`
  - 校验 `mTLS client subject DID == bearer subject DID`

### 7.2 应用层 DPoP/消息签名

新增 `app/identity/dpop.py`：

- proof 至少含：`htm/htu/iat/jti/ath/nonce`
- 网关校验：签名、method/url/token hash、kid DID 一致性。

### 7.3 重放防护

- 新增 `dpop_nonce_seen` 表（或等价存储）：
  - `jti/nonce/kid/first_seen_at`
- TTL 窗口内重复请求拒绝。

### 7.4 四重绑定

绑定关系：

- token `sub_did`
- DPoP `kid DID`
- mTLS 证书主体 DID
- 当前 credential subject DID

任一不一致，拒绝并落 `auth_events` deny。

### 验收

- 缺证书、证书主体不匹配、DPoP 转发/重放均拒绝；
- 认证结果审计可追溯。

---

## 阶段 8：审计与脚本适配

### 改动

- `app/store/auth_events.py`/schema 增字段：
  - `caller_did/claimed_did/token_sub_did/token_agent_did/signing_kid`
  - `mtls_client_id/mtls_cert_fingerprint/mtls_verified`
  - `dpop_jti/dpop_nonce/dpop_verified`
- 更新 `scripts/security/*` 输出 DID/VC 相关信息。

### 验收

- `/audit/traces/{trace_id}` 能串联 DID + credential + intent。

---

## 阶段 9：测试矩阵补全

新增/补强（`tests/security/`）：

1. DID Resolver：不存在 DID、非法 kid、跨 DID kid；
2. JWT：kid 冒用、claim DID 与 kid DID 不一致；
3. VC Delegation：proof 篡改、subject DID 篡改、parent/root 断链；
4. Intent：actor DID 与 bearer DID 不一致；
5. MITM/双向认证：
   - mTLS 缺失/不一致；
   - DPoP method/url/token hash 篡改；
   - DPoP 重放；
6. 兼容性：双栈模式可运行；strict 模式拒绝旧格式。

---

## 阶段 10：strict 收口与上线策略

### 开关建议

- `BUIAM_DID_ENABLED=true`
- `BUIAM_DID_STRICT=false -> true`（最后切）
- `BUIAM_MTLS_REQUIRED=true`
- `BUIAM_DPOP_REQUIRED=true`

### 上线步骤

1. 开发环境双栈验证；
2. 预发开启 mTLS + DPoP；
3. 观察审计与失败率；
4. 最终切 `DID_STRICT=true`。

---

## 4. 任务拆分建议（按你提到的“边界不好划分”）

建议按“链路纵切”而不是模块横切：

1. **身份链路包**（A）
   - DID 生成/注册/解析 + JWT DID 化
2. **委托链路包**（B）
   - VC-shaped credential + delegation/service 联动
3. **意图与审计包**（C）
   - Intent DID 化 + auth_events 扩展 + 脚本与测试
4. **传输与对抗包**（D）
   - mTLS + DPoP + replay + 四重绑定

每个包都必须带：代码 + 安全测试 + 失败场景证明。

---

## 5. DoD（完成定义）

满足以下全部项才算完成：

1. 全链路使用 DID/kid 验签（JWT、Credential、Intent）；
2. DelegationCredential 完成 VC-shaped 升级，原安全不变量保持；
3. mTLS + DPoP + replay + 四重绑定启用；
4. 双向认证成立（Client<->Gateway 与 Gateway<->Agent）；
5. `pytest -q -p no:cacheprovider` 与 `scripts/security/run_all_security_checks.py` 全绿；
6. strict 模式可开启并稳定运行。

---

## 6. 下一步建议（立刻执行）

1. 先完成 **阶段 0 基线收敛**（清理冲突与模型统一）；
2. 紧接着做 **阶段 1-3**（DID + Resolver + JWT DID 化）；
3. 你关心的“委托链一起改”在 **阶段 4-5** 一次性做完，减少边界摩擦；
4. 最后集中做 **阶段 7 安全收口**（MITM 与双向认证）。

---

> 文档说明：本方案已按当前代码结构编排，优先保证可迁移、可回归、可审计。
