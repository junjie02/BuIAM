# BuIAM A2A 安全委托系统技术方案

## 1. 文档说明

本文档合并并替代原 `buiam_delegation_technical_solution.md` 与 `Implementation_scheme_of_identity_and_authentication.md`。原两份文档中的旧路径、旧接口、`local://` 调用、`/delegate/call`、MVP mock store 等内容已经不再代表当前项目状态。

当前 BuIAM 项目是一个基于 FastAPI + SQLite 的 A2A 安全委托系统。业务 Agent 的执行动作仍然使用 mock provider，以便在没有真实飞书 API 的情况下稳定演示和测试；但认证、授权、签名化委托凭证链、意图链、Token 吊销、Token/credential 过期、运行中任务取消和审计追踪都是真实实现。

本文档面向项目交付、技术评审、后续开发和安全验收，描述当前代码中的真实设计与实现边界。

## 2. 建设目标

### 2.1 核心目标

- 用户到 Agent、Agent 到 Agent 的每一跳调用都必须经过 BuIAM Gateway。
- Gateway 统一完成身份认证、授权判断、意图校验、credential 链生成和审计落库。
- JWT 证明“调用进程是谁”，signed delegation credential 证明“该主体在当前 trace 中凭什么能做这件事”。
- 意图链证明“为什么调用”，授权链证明“能否调用”。
- Token 自然过期和主动撤销采用不同语义：过期只阻止新动作，撤销会级联撤销并取消运行中任务。
- `delegation_chain` 只做人类可读摘要，不能作为安全事实来源。

### 2.2 当前能力范围

当前项目已经实现：

- Agent 注册与发现。
- 开发版 RSA keypair 生成和加载。
- 共享 crypto helper：canonical JSON、base64url、RSA sign/verify、SHA256。
- JWT-like access token 签发、验签、过期、吊销、jti 注册检查。
- Token 签发时同步创建 root delegation credential。
- A2A Gateway 正式入口：
  - `POST /a2a/root-tasks`
  - `POST /a2a/agents/{target_agent_id}/tasks`
- 每一跳授权生成 signed delegation credential。
- 每一跳生成或校验 signed intent node。
- 能力交集授权：请求能力、调用方能力、目标 Agent 静态能力、用户授权能力取交集。
- credential hash/signature、parent/root、capability narrowing、trace 连续性校验。
- intent hash/signature、actor、parent/root、trace 连续性和 judge 结果校验。
- Token revoke 级联撤销 descendant credentials，并按 trace 取消进程内运行任务。
- Token/credential 过期阻止新请求、新委托、新工具访问，但不主动取消已开始任务。
- 完整审计查询：auth events、audit logs、delegation chain、delegation credentials、intent tree。
- 三个独立 demo Agent 服务：
  - `doc_agent`
  - `enterprise_data_agent`
  - `external_search_agent`
- 安全验证脚本与 pytest 回归测试。

## 3. 总体架构

### 3.1 运行拓扑

```text
User
  |
  | Bearer user token + root task
  v
Gateway: http://127.0.0.1:8000
  |
  | signed credential + signed intent + forwarded envelope
  v
doc_agent: http://127.0.0.1:8011/a2a/tasks
  |
  | A2A call through Gateway
  v
Gateway
  |
  | child credential + child intent + audit
  v
enterprise_data_agent: http://127.0.0.1:8012/a2a/tasks

external_search_agent: http://127.0.0.1:8013/a2a/tasks
```

三 Agent 是独立 FastAPI 服务。Agent 之间不能通过本地 import 直接调用下游 Agent handler，必须使用 `app/sdk/client.py` 走 Gateway 的 A2A 入口。

### 3.2 代码分层

| 路径 | 职责 |
| --- | --- |
| `app/protocol.py` | 协议模型、请求响应模型、审计模型、credential 和 intent 数据模型 |
| `app/main.py` | FastAPI 应用、startup、路由挂载、审计查询接口 |
| `app/gateway/routes.py` | A2A 入口、Bearer 校验、授权/意图编排、审计、转发、任务取消响应 |
| `app/identity/` | Token 签发/验签/吊销、开发版 RSA key、共享 crypto helper |
| `app/delegation/` | capability 解析与交集、credential 构造/签名/校验、委托授权服务 |
| `app/intent/` | intent 生成、judge、intent node 签名、hash chain 校验 |
| `app/registry/` | demo Agent 注册启动逻辑和 registry API |
| `app/store/` | SQLite schema 和各类安全事实/审计数据访问 |
| `app/runtime/tasks.py` | 单进程 asyncio task registry，用于 revoke 时按 trace 取消任务 |
| `app/sdk/client.py` | Agent 调用 Gateway 的 A2A client |
| `examples/agent/` | 三个 demo Agent 服务与 mock provider |
| `scripts/` | demo、bootstrap、安全验证脚本 |
| `tests/` | smoke、集成和安全回归测试 |

### 3.3 当前 demo Agent

| Agent | Endpoint | 能力 | 当前业务实现 |
| --- | --- | --- | --- |
| `doc_agent` | `http://127.0.0.1:8011/a2a/tasks` | `report:write`、`feishu.doc:write`、企业数据读能力、`web.public:read` | 编排报告生成，委托企业数据读取，写 mock 文档 |
| `enterprise_data_agent` | `http://127.0.0.1:8012/a2a/tasks` | `feishu.contact:read`、`feishu.calendar:read`、`feishu.wiki:read`、`feishu.bitable:read` | 返回 mock 企业数据；提供 `sleep` 可取消长任务 |
| `external_search_agent` | `http://127.0.0.1:8013/a2a/tasks` | `web.public:read` | 返回 mock 公网搜索结果；用于演示越权读取企业数据被拒绝 |

后续接入真实飞书 API 时，优先替换 `examples/agent/demo_provider.py` 或各 Agent provider 层，不应改动 Gateway 安全链路。

## 4. 协议与核心模型

### 4.1 Capability

当前 capability 是静态字符串白名单：

```text
report:write
feishu.doc:write
feishu.contact:read
feishu.calendar:read
feishu.wiki:read
feishu.bitable:read
web.public:read
```

授权时不支持通配符，也不支持隐式继承。新增能力需要同时在协议模型、能力解析、Agent 注册、Token 和请求中明确声明。

### 4.2 DelegationEnvelope

`DelegationEnvelope` 是 A2A 请求载体：

| 字段 | 说明 |
| --- | --- |
| `protocol_version` | 当前为 `buiam.delegation.v1` |
| `trace_id` | 一次用户任务或委托链路的全局追踪 ID |
| `request_id` | 当前这一跳请求 ID |
| `caller_agent_id` | 请求声称的调用方；Gateway 会用 Bearer token 的真实身份覆盖 |
| `target_agent_id` | 目标 Agent |
| `task_type` | 当前任务类型 |
| `requested_capabilities` | 当前请求需要使用的能力 |
| `delegation_chain` | 人类可读委托摘要，仅用于审计展示 |
| `intent_node` | 当前跳的 signed intent node；缺失时 Gateway 可生成 |
| `auth_context` | Gateway 验证 Token 或 credential 后得到的认证上下文 |
| `payload` | 业务参数 |

### 4.3 AuthContext

`AuthContext` 是 Gateway 信任后的身份上下文：

| 字段 | 说明 |
| --- | --- |
| `jti` | Token ID 或 credential ID，用于兼容 token 逻辑 |
| `sub` | 主体 ID |
| `agent_id` | 当前调用身份 |
| `actor_type` | `user` 或 `agent` |
| `delegated_user` | 委托链所属用户 |
| `capabilities` | 当前主体可继续携带/委托的能力 |
| `user_capabilities` | 用户授权能力边界 |
| `exp` | 过期时间 |
| `credential_id` | 当前 signed credential ID |
| `parent_credential_id` | 父 credential ID |
| `root_credential_id` | root credential ID |
| `sig` | credential 签名摘要 |

## 5. 身份认证与 Token 设计

### 5.1 Token 角色

系统支持两类 token：

- 用户 token：`actor_type=user`，只能调用 `POST /a2a/root-tasks`。
- Agent token：`actor_type=agent`，只能调用 `POST /a2a/agents/{target_agent_id}/tasks`。

Gateway 会拒绝 user token 冒充 agent 调用 A2A，也会拒绝 agent token 发起 root task。

### 5.2 Token Header

```json
{
  "alg": "BUIAM-RS256",
  "typ": "JWT",
  "kid": "doc_agent"
}
```

当前 `BUIAM-RS256` 使用项目内开发版 RSA helper，不是生产级标准 JWS/JWT 库封装。后续生产化应替换为标准库与 JWKS/KMS/HSM。

### 5.3 Token Claims

```json
{
  "jti": "tok_xxx",
  "iss": "buiam.local",
  "aud": "buiam.a2a",
  "sub": "doc_agent",
  "agent_id": "doc_agent",
  "actor_type": "agent",
  "delegated_user": "user_123",
  "capabilities": ["feishu.contact:read"],
  "user_capabilities": ["feishu.contact:read"],
  "iat": 1777118698,
  "exp": 1777122298
}
```

### 5.4 签发流程

`issue_token()` 完成：

1. 生成 `jti`、`iat`、`exp` 和 claims。
2. 使用 `kid=agent_id/user_id` 对 JWT signing input 签名。
3. 创建 root `DelegationCredential`：
   - `issuer_id = agent_id`
   - `subject_id = agent_id`
   - `parent_credential_id = None`
   - `root_credential_id = credential_id`
   - `request_id = jti`
4. 写入 `delegation_credentials`。
5. 写入 `tokens`，绑定 `jti -> root credential_id`。

### 5.5 验证流程

`inspect_token()` 检查：

- token 结构是否可解析。
- header `alg` 与 `kid` 是否有效。
- RSA signature 是否有效。
- `iss == buiam.local`。
- `aud == buiam.a2a`。
- `exp > now`。
- `jti` 是否已在服务端登记。
- token 是否 revoked。
- token 绑定的 root credential 是否存在、未 revoked、hash/signature 可验。

验证成功后返回 `AuthContext`。验证失败时返回明确错误码，并由 Gateway 写入 `auth_events` 和 deny audit。

### 5.6 Token 错误码

| 场景 | error_code |
| --- | --- |
| 缺少 Authorization header | `AUTH_TOKEN_MISSING` |
| Bearer 格式错误 | `AUTH_TOKEN_INVALID` |
| token 结构不可解析 | `AUTH_TOKEN_MALFORMED` |
| 签名错误 | `AUTH_TOKEN_SIGNATURE_INVALID` |
| issuer 不匹配 | `AUTH_TOKEN_ISSUER_MISMATCH` |
| audience 不匹配 | `AUTH_TOKEN_AUDIENCE_MISMATCH` |
| token 过期 | `AUTH_TOKEN_EXPIRED` |
| token 已吊销 | `AUTH_TOKEN_REVOKED` |
| jti 未登记 | `AUTH_TOKEN_JTI_NOT_REGISTERED` |
| 绑定 credential 无效 | `AUTH_CREDENTIAL_INVALID` |
| 绑定 credential 已撤销 | `AUTH_CREDENTIAL_REVOKED` |

## 6. 签名化委托授权链

### 6.1 设计目标

旧方案中的 `auth_context` 字段继承已经升级为 signed delegation credential 链。JWT 只证明当前请求进程身份，credential 链证明该身份在某个 trace 中获得了哪些可委托能力。

### 6.2 DelegationCredential

| 字段 | 说明 |
| --- | --- |
| `credential_id` | credential 节点 ID，即链式哈希 |
| `parent_credential_id` | 父 credential ID，root 为 `None` |
| `root_credential_id` | root credential ID |
| `issuer_id` | 签发者，root 为用户/agent 自身，child 为上游 agent |
| `subject_id` | 被授权主体 |
| `delegated_user` | 所属用户 |
| `capabilities` | 当前凭证允许携带的能力 |
| `user_capabilities` | 用户授权边界 |
| `iat` / `exp` | 签发与过期时间 |
| `trace_id` / `request_id` | 所属 trace 和 hop |
| `content_hash` | `self_content` 的哈希 |
| `signature` | issuer 对 `self_content` 的签名 |
| `signature_alg` | 当前为 `BUIAM-RS256` |
| `revoked` / `revoked_at` / `revoke_reason` | 撤销状态 |

### 6.3 哈希与签名规则

签名内容只包含稳定业务字段，不包含 `credential_id`、`content_hash`、`signature` 和 revoked 状态。

```text
self_content = {
  protocol_version,
  parent_credential_id,
  root_credential_id,
  issuer_id,
  subject_id,
  delegated_user,
  capabilities,
  user_capabilities,
  iat,
  exp,
  trace_id,
  request_id
}

content_hash = sha256(canonical_json(self_content))
credential_id = sha256(parent_credential_id_or_ROOT + canonical_json(self_content))
signature = rsa_sign(canonical_json(self_content), issuer_id)
```

当前结构是 signed hash chain，不是完整 Merkle Tree。它没有把多个 children 聚合成一个 Merkle root，也没有 sibling proof；它更适合当前项目的 A2A 单路径溯源和审计需求。

### 6.4 Credential 校验规则

Gateway 在 A2A 委托前校验：

- `content_hash` 可重算。
- `credential_id` 可重算。
- `signature` 可用 `issuer_id` 公钥验证。
- current credential 未 revoked、未 expired。
- parent/root credential 存在。
- parent/root 未 revoked、未 expired。
- `root_credential_id` 与 parent/root 连续。
- child `exp <= parent.exp`。
- child capabilities 不超过 parent capabilities。
- child user capabilities 不超过 parent user capabilities。
- credential subject 必须等于 `auth_context.agent_id/sub`。
- 当前 credential 所属 `trace_id` 必须与 envelope `trace_id` 一致。

### 6.5 委托授权算法

能力授权使用交集：

```text
effective_capabilities =
    requested_capabilities
    ∩ caller_token_capabilities
    ∩ target_agent_static_capabilities
    ∩ delegated_user_capabilities
```

如果 `effective_capabilities` 覆盖全部 `requested_capabilities`，则 allow；否则 deny，并在 `decision_detail.missing_by` 中说明缺失来自：

- `caller_token`
- `target_agent`
- `user`

### 6.6 Credential 错误码

| 场景 | error_code |
| --- | --- |
| hash、签名、parent、root、subject、capability 等校验失败 | `AUTH_CREDENTIAL_INVALID` |
| 当前 credential 已撤销 | `AUTH_CREDENTIAL_REVOKED` |
| parent/root credential 已撤销 | `AUTH_PARENT_CREDENTIAL_REVOKED` |
| 当前 credential 已过期 | `AUTH_CREDENTIAL_EXPIRED` |
| parent/root credential 已过期 | `AUTH_PARENT_CREDENTIAL_EXPIRED` |
| Bearer 主体与 envelope credential subject 不一致 | `AUTH_CREDENTIAL_SUBJECT_MISMATCH` |

## 7. 意图链设计

### 7.1 设计目标

委托链证明“can”，意图链证明“why”。多 Agent 协作中，某个 agent 可能把用户任务扩展到不相关目标，例如从“查公开信息”扩展到“读取企业内部数据”。意图链用于记录和校验每一跳调用是否承接上游意图。

### 7.2 IntentCommitment

```json
{
  "intent": "user_123 requests doc_agent to run generate_report",
  "description": "Deterministic demo intent commitment.",
  "data_refs": ["topic"],
  "constraints": ["demo intent provider", "preserve delegated capability boundary"]
}
```

当前 provider 默认为 mock，可切换到 OpenAI-compatible 或 Anthropic-compatible provider。

### 7.3 IntentNode

| 字段 | 说明 |
| --- | --- |
| `node_id` | intent 节点 ID，即链式哈希 |
| `parent_node_id` | 父 intent node，root 为 `None` |
| `actor_id` | 声明该意图的用户或 agent |
| `actor_type` | `user` 或 `agent` |
| `target_agent_id` | 目标 agent |
| `task_type` | 任务类型 |
| `intent_commitment` | 意图承诺 |
| `signature` | actor 对 self_content 的签名 |
| `signature_alg` | 当前为 `BUIAM-RS256` |

### 7.4 哈希与签名规则

```text
self_content = {
  protocol_version,
  parent_node_id,
  actor_id,
  actor_type,
  target_agent_id,
  task_type,
  intent_commitment
}

content_hash = sha256(canonical_json(self_content))
node_id = sha256(parent_node_id_or_ROOT + canonical_json(self_content))
signature = rsa_sign(canonical_json(self_content), actor_id)
```

### 7.5 Intent 校验规则

Gateway 在记录和放行前校验：

- `node_id` 可重算。
- signature 可由 `actor_id` 公钥验证。
- root intent 必须由 delegated user 签名。
- child intent 必须由当前 caller agent 签名。
- parent intent 必须存在。
- parent/ancestor intent 必须属于同一个 trace。
- parent content hash 与存储记录一致。
- 分支不能成环，且能回溯到 root。
- Judge 结果必须为 `Consistent`，`Drifted` 直接拒绝。

### 7.6 Intent 错误码

| 场景 | error_code |
| --- | --- |
| node_id、parent hash、trace 连续性、环等失败 | `INTENT_CHAIN_INVALID` |
| 签名失败 | `INTENT_SIGNATURE_INVALID` |
| parent 不存在 | `INTENT_PARENT_NOT_FOUND` |
| actor 与当前身份不匹配 | `INTENT_ACTOR_MISMATCH` |
| judge 返回漂移 | `INTENT_DRIFTED` |
| judge 调用失败 | `INTENT_JUDGE_FAILED` |
| 意图生成失败 | `INTENT_GENERATION_FAILED` |

## 8. Gateway 调用流程

### 8.1 Root Task: User -> Gateway -> First Agent

入口：

```text
POST /a2a/root-tasks
Authorization: Bearer <user_token>
```

流程：

1. Gateway 创建 provisional envelope。
2. 校验 Bearer user token。
3. 要求 `actor_type=user`。
4. 查询 target agent，要求已注册且 active。
5. 生成 root intent commitment。
6. 使用 delegated user key 签名 root intent node。
7. 校验并记录 root intent。
8. 基于用户 root credential 构建 user -> first agent child credential。
9. 记录 root delegation hop 和 allow audit。
10. 将注入 auth context、root hop、root intent 的 envelope 转发给首个 Agent。

### 8.2 A2A Task: Agent A -> Gateway -> Agent B

入口：

```text
POST /a2a/agents/{target_agent_id}/tasks
Authorization: Bearer <agent_token>
```

流程：

1. 查询 target agent，要求已注册且 active。
2. 校验 Bearer token。
3. 校验 envelope credential，并要求 credential subject 与 Bearer token agent 一致。
4. 要求 `actor_type=agent`。
5. 如 envelope 未携带 intent node，则根据 `payload.user_task` 和 `payload.parent_intent_node_id` 生成 child intent。
6. 校验并记录 intent node。
7. 执行 delegation authorization。
8. 写入 allow/deny audit。
9. allow 时创建下一跳 credential，并追加 human-readable chain hop。
10. 转发给 target agent。
11. target 调用失败或 revoke cancel 时写入 deny audit。

### 8.3 Gateway 信任边界

Gateway 不信任请求体中的声明身份。A2A 时：

- `caller_agent_id` 会被 Bearer token 的 `agent_id` 覆盖。
- envelope credential 必须存在于服务端 store。
- credential subject 必须等于 Bearer token subject。
- credential trace 必须等于 envelope trace。
- target agent 必须来自 registry，且状态为 active。

## 9. Token 过期与撤销语义

### 9.1 自然过期

过期是弱语义：

- 新 root task 拒绝。
- 新 A2A call 拒绝。
- 新工具访问应拒绝。
- 审计记录 expired。
- 不把 credential 标记为 revoked。
- 不主动取消已经开始的任务。

### 9.2 主动撤销

撤销是强语义：

- `POST /identity/tokens/{jti}/revoke` 标记 token revoked。
- 找到 token 绑定的 root credential。
- 递归 revoke root credential 及所有 descendants。
- 收集受影响 `trace_id`。
- 通过 `app/runtime/tasks.py` 的进程内 task registry 取消运行中任务。
- 运行中任务被取消时返回：

```json
{
  "detail": {
    "error_code": "TASK_CANCELLED",
    "reason": "token_revoked"
  }
}
```

### 9.3 当前边界

当前取消机制是单进程内 `asyncio.Task.cancel()`。如果后续部署为多进程或分布式 worker，需要引入集中式 trace task registry、消息队列或工作流引擎取消协议。

## 10. 审计与追踪

### 10.1 auth_events

记录每次 Bearer token 验证，包括成功和失败。

关键字段：

- `trace_id`
- `request_id`
- `caller_agent_id`
- `claimed_agent_id`
- `token_jti`
- `token_sub`
- `token_agent_id`
- `delegated_user`
- `token_fingerprint = sha256(token)`
- `token_issued_at`
- `token_expires_at`
- `verified_at`
- `is_expired`
- `is_revoked`
- `is_jti_registered`
- `signature_valid`
- `issuer_valid`
- `audience_valid`
- `identity_decision`
- `error_code`
- `reason`

系统不在审计中保存完整 token。

### 10.2 audit_logs

记录授权和拒绝决策。

关键字段：

- `trace_id`
- `request_id`
- `caller_agent_id`
- `target_agent_id`
- `requested_capabilities`
- `effective_capabilities`
- `decision`
- `reason`
- `delegation_chain`
- `decision_detail`

`decision_detail` 记录 token、credential、intent、capability narrowing 和 judge 结果摘要。

### 10.3 delegation_chain

人类可读链路摘要：

- `from_actor`
- `to_agent_id`
- `task_type`
- `delegated_capabilities`
- `missing_capabilities`
- `decision`

它只用于展示，不作为授权事实来源。

### 10.4 delegation_credentials

安全事实来源之一，保存 signed credential 链。支持按 parent、root、trace、subject 查询，并支持 root/descendant 级联撤销。

### 10.5 intent_tree

安全事实来源之一，保存 signed intent node。支持按 trace 查询完整 tree，按 node id 定位节点。

### 10.6 Trace 查询接口

```text
GET /audit/traces/{trace_id}
GET /audit/traces/{trace_id}/chain
GET /audit/traces/{trace_id}/credentials
GET /audit/traces/{trace_id}/intent-tree
GET /audit/intent-nodes/{node_id}
GET /audit/auth-events
GET /audit/logs
```

`GET /audit/traces/{trace_id}` 一次性返回：

```json
{
  "trace_id": "...",
  "logs": [],
  "chain": [],
  "delegation_credentials": [],
  "auth_events": [],
  "intent_tree": []
}
```

## 11. 数据库表设计

当前默认数据库路径为 `data/audit.db`，可通过 `BUIAM_DB_PATH` 配置。

| 表 | 说明 |
| --- | --- |
| `agents` | Agent registry，包含 endpoint、状态、静态能力和元数据 |
| `tokens` | token 登记、过期、撤销、绑定 root credential |
| `jti_seen` | 已验证过的 jti 首次出现时间 |
| `delegation_credentials` | signed credential 链 |
| `audit_logs` | 授权/拒绝审计 |
| `delegation_chain` | 人类可读委托链摘要 |
| `auth_events` | Bearer token 身份验证审计 |
| `intent_tree` | signed intent node 树 |

重要索引：

- `delegation_credentials(parent_credential_id)`
- `delegation_credentials(root_credential_id)`
- `delegation_credentials(trace_id)`
- `delegation_credentials(subject_id)`

## 12. API 定义

### 12.1 Health

```text
GET /health
```

### 12.2 Registry

```text
POST /registry/agents
GET /registry/agents
GET /registry/agents/{agent_id}
```

`POST /registry/agents` 请求字段：

- `agent_id`
- `name`
- `agent_type`
- `endpoint`
- `description`
- `owner_org`
- `allowed_resource_domains`
- `static_capabilities`
- `status`

### 12.3 Identity

```text
POST /identity/tokens
POST /identity/tokens/introspect
GET /identity/public-key/{key_id}
POST /identity/tokens/{jti}/revoke
```

签发 token 示例：

```json
{
  "agent_id": "user_123",
  "delegated_user": "user_123",
  "actor_type": "user",
  "capabilities": ["report:write", "feishu.contact:read"],
  "user_capabilities": ["report:write", "feishu.contact:read"],
  "ttl_seconds": 3600
}
```

吊销 token 示例：

```json
{
  "reason": "manual_revoke"
}
```

### 12.4 A2A

用户入口：

```text
POST /a2a/root-tasks
```

请求示例：

```json
{
  "trace_id": null,
  "request_id": null,
  "target_agent_id": "doc_agent",
  "task_type": "generate_report",
  "user_task": "生成一份飞书协作报告",
  "requested_capabilities": [
    "report:write",
    "feishu.doc:write",
    "feishu.contact:read",
    "feishu.calendar:read",
    "feishu.wiki:read",
    "feishu.bitable:read"
  ],
  "payload": {
    "topic": "A2A Delegation Demo Report"
  }
}
```

Agent 入口：

```text
POST /a2a/agents/{target_agent_id}/tasks
```

Agent 请求由 `app/sdk/client.py` 构造，携带 `DelegationEnvelope` 和 agent Bearer token。

## 13. 配置项

`.env.example` 是当前支持环境变量的来源。

重要配置：

| 变量 | 说明 |
| --- | --- |
| `BUIAM_GATEWAY_URL` | Gateway 地址 |
| `BUIAM_DEMO_USER_ID` | Demo 用户 ID |
| `BUIAM_DEMO_KEEP_SERVERS` | demo 结束后是否保留脚本启动服务 |
| `BUIAM_DB_PATH` | SQLite 数据库路径 |
| `BUIAM_KEY_DIR` | 开发版 RSA key 目录 |
| `DOC_AGENT_ENDPOINT` | doc_agent endpoint |
| `ENTERPRISE_DATA_AGENT_ENDPOINT` | enterprise_data_agent endpoint |
| `EXTERNAL_SEARCH_AGENT_ENDPOINT` | external_search_agent endpoint |
| `A2A_FORWARD_TIMEOUT_SECONDS` | Gateway 转发超时 |
| `A2A_AGENT_TOKEN_TTL_SECONDS` | SDK 自动申请 agent token 最大 TTL |
| `LLM_PROVIDER` | 默认 LLM provider |
| `INTENT_GENERATOR_PROVIDER` | 意图生成 provider |
| `INTENT_JUDGE_PROVIDER` | 意图 judge provider |
| `OPENAI_*` | OpenAI-compatible 配置 |
| `ANTHROPIC_*` | Anthropic-compatible 配置 |
| `BUIAM_SECURITY_*` | 安全验证脚本参数 |

默认 provider 为 `mock`，无需外部密钥即可跑通 demo 和测试。

## 14. Demo 与验证

### 14.1 快速运行

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts/demo.py
```

`scripts/demo.py` 自动启动或复用四个服务，并运行：

- 正常链路：`user -> doc_agent -> enterprise_data_agent`
- 越权链路：`user -> external_search_agent -> enterprise_data_agent`
- trace audit 摘要

### 14.2 手动启动服务

```bash
uvicorn app.main:app --port 8000
uvicorn examples.agent.doc_service:app --port 8011
uvicorn examples.agent.enterprise_data_service:app --port 8012
uvicorn examples.agent.external_search_service:app --port 8013
```

### 14.3 自动化测试

```bash
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

当前测试覆盖：

- 正常委托链、意图链和审计。
- 越权委托与 capability narrowing。
- credential 篡改、跨 trace credential 复用。
- intent 篡改、签名错误、parent 缺失、actor mismatch、drift、跨 trace parent intent。
- Token 签发、过期、吊销、级联撤销。
- 运行中 sleep task 被 revoke 取消。
- A2A Bearer 缺失、伪造、主体不一致、user token 冒充 agent、未知 target agent。
- credential 和 intent 签名不可抵赖。

### 14.4 人工安全验证脚本

```bash
python scripts/security/verify_delegation_chain.py
python scripts/security/verify_intent_chain.py
python scripts/security/find_security_node.py
python scripts/security/verify_chain_binding.py
python scripts/security/verify_token_lifecycle.py
python scripts/security/verify_a2a_identity.py
python scripts/security/run_all_security_checks.py
```

脚本解释文档：

```text
scripts/security/SECURITY_CHECKS_EXPLAINED.md
```

## 15. 典型安全场景

### 15.1 正常委托

```text
user_123 -> doc_agent -> enterprise_data_agent
```

结果：

- root user token 验证成功。
- root credential 创建成功。
- user -> doc_agent credential 创建成功。
- root intent 创建并验证成功。
- doc_agent 调用 enterprise_data_agent 时，Gateway 创建 child intent 和 child credential。
- enterprise_data_agent 只获得企业数据读能力子集。
- audit trace 可还原完整链路。

### 15.2 越权委托

```text
user_123 -> external_search_agent -> enterprise_data_agent
```

`external_search_agent` 只有 `web.public:read`。当它试图请求企业数据读能力时：

- Bearer token 可以证明它是 `external_search_agent`。
- credential 可以证明它只有 public web 能力。
- capability intersection 无法覆盖企业数据能力。
- Gateway deny，并在 `missing_by.caller_token` 与 `missing_by.user` 中记录缺失来源。

### 15.3 Bearer 与 credential subject 不一致

攻击方式：

```text
external_search_agent 使用自己的 Bearer token，
但 envelope 中塞入 doc_agent 的 credential。
```

Gateway 拒绝：

```text
AUTH_CREDENTIAL_SUBJECT_MISMATCH
```

### 15.4 跨 trace 拼接

攻击方式：

- 拿另一个 trace 的 credential 放入当前 envelope。
- 拿另一个 trace 的 parent intent node 作为当前 intent parent。

Gateway 拒绝：

```text
AUTH_CREDENTIAL_INVALID
INTENT_CHAIN_INVALID
```

### 15.5 Token 过期

过期 token 发起新请求会被拒绝：

```text
AUTH_TOKEN_EXPIRED
```

已开始任务不会因自然过期被主动 cancel。

### 15.6 Token 撤销

撤销 root token 后：

- token revoked。
- root credential 和 descendants revoked。
- 后续使用 descendant credential 会失败。
- 正在运行的 trace task 会收到 cancel。
- audit 记录 `TASK_CANCELLED/token_revoked`。

## 16. 安全不变量

后续开发不能破坏以下不变量：

- 所有正式 A2A 调用必须经过 Gateway。
- Bearer token 是调用进程身份来源。
- signed credential 链是授权事实来源。
- signed intent 链是意图事实来源。
- `delegation_chain` 不能参与安全决策。
- child credential 能力不得超过 parent。
- child credential 过期时间不得晚于 parent。
- credential 与 intent 必须绑定同一 trace。
- A2A Bearer subject 必须匹配当前 credential subject。
- Token revoke 必须级联 revoke credential tree。
- Token revoke 必须取消相关 trace 的运行中任务。
- Token/credential 过期不得主动取消已经开始的任务。
- 失败请求也要尽量记录 deny audit 和 auth event。

## 17. 当前限制

- RSA/JWT 实现是开发版简化实现，不是生产级密码库封装。
- SQLite 适合 demo 和本地验证，生产建议替换为 PostgreSQL/MySQL 等托管数据库。
- 运行中任务取消目前只支持单进程内 asyncio task registry。
- 当前 capability 是静态字符串集合，尚未支持资源级策略、ABAC/RBAC 或策略 DSL。
- 当前未在网络层强制阻止 Agent 直接互连，生产应配合服务网格、mTLS、网络策略或 Gateway-only ingress。
- 当前业务执行是 mock provider，真实飞书 API 接入仍需替换 Agent provider 层。
- 当前 hash chain 不是完整 Merkle Tree，不提供 sibling proof 或批量 Merkle root。

## 18. 后续演进路线

### 18.1 生产级身份与密钥

- 替换开发版 RSA 为标准 JWS/JWT 库。
- 引入 JWKS、KMS 或 HSM。
- 支持 key rotation。
- 支持 mTLS 绑定 Agent 身份。
- 支持标准 OAuth2 Token Exchange 或短 token + refresh token。

### 18.2 分布式任务撤销

- 将进程内 task registry 升级为集中式 trace task registry。
- 引入消息队列或工作流引擎取消信号。
- 支持跨进程、跨节点、跨 worker 的 revoke cancel。

### 18.3 策略能力增强

- capability 从静态字符串升级为策略 DSL。
- 支持资源级约束，例如具体文档、表格、字段、租户、时间窗口。
- 支持审批流与人工确认。
- 支持风险评分和动态降权。

### 18.4 审计与合规

- 审计日志写入不可变存储或日志平台。
- 支持审计导出、报表、告警。
- 对关键 deny 事件触发安全告警。
- 为 credential chain 和 intent tree 增加可选 Merkle root 批量摘要。

### 18.5 真实飞书接入

- 保持 Gateway 安全链路不变。
- 替换 `examples/agent/demo_provider.py`。
- 为 doc/contact/calendar/wiki/bitable 等真实 API 增加资源级 capability。
- 引入飞书 API 错误审计和权限映射。

## 19. 结论

当前 BuIAM 已形成以 Gateway 为中心的 A2A 安全委托闭环：JWT 负责进程身份认证，signed delegation credential 负责可授权能力证明，signed intent node 负责调用意图证明，audit store 负责可追溯与验收。

相比旧方案，当前实现已经从简单字段继承和本地 mock 调用升级为独立 Agent 服务、正式 A2A Gateway 入口、签名化授权链、签名化意图链、明确的过期/撤销语义和系统化安全测试。下一阶段应重点推进生产级密码体系、分布式撤销、资源级策略和真实飞书 provider 接入。

