# BuIAM Agent 委托安全服务技术方案

## 1. 文档说明

本文档面向项目交付、技术评审与甲方验收，说明当前 BuIAM Agent 委托安全服务的总体架构、身份认证、Access Token 设计、A2A（Agent-to-Agent）认证与授权流程、意图承诺与意图调用树、审计追踪、API 接口定义、数据表设计、演示流程与后续扩展建议。

当前实现是一个基于 FastAPI + SQLite 的 MVP 原型，核心目标是把 BuIAM 安全服务与示例 Agent 业务逻辑彻底分离，使安全服务可以作为通用网关接入不同 Agent，而不依赖特定业务 Agent。

---

## 2. 建设目标

### 2.1 核心目标

- 将用户到 Agent、Agent 到 Agent 的每一次委托调用统一纳入 BuIAM Gateway。
- 每一跳调用都进行身份认证、权限判定、意图一致性检查与审计记录。
- 支持独立的身份验证审计、授权审计、轻量委托链和意图调用树。
- 防止多 Agent 调用中的权限扩大、身份冒用、意图漂移和链路篡改。
- 保持 BuIAM 安全服务通用化，示例 Agent 仅作为接入方存在。

### 2.2 当前能力范围

当前项目已经实现以下能力：

- Agent 注册表：保存 Agent ID、名称、 endpoint 和静态能力列表。
- 用户/Agent Token 签发与校验：支持 `actor_type=user|agent`。
- Gateway 统一入口：`/delegate/root-task` 和 `/delegate/call`。
- A2A 权限委托：基于调用方 Token 能力、目标 Agent 能力、请求能力和用户授权能力取交集。
- 身份验证审计：独立 `auth_events` 表记录每一次入口 Token 校验细节。
- 授权审计：`audit_logs` 记录每一次委托授权决策详情。
- 独立委托链：`delegation_chain` 记录人类可读的轻量链路。
- 意图承诺与意图调用树：`intent_tree` 记录每一跳意图节点、签名、哈希和 Judge 结果。
- LLM 意图生成与漂移判断：支持 OpenAI-compatible 和 Anthropic-compatible endpoint。
- 本地 Demo Agent 接入：示例 Agent 位于 `examples/`，核心服务位于 `app/`。

---

## 3. 总体架构

### 3.1 逻辑架构

```text
┌──────────────┐
│    User      │
└──────┬───────┘
       │ 1. 用户 Token + 原始任务
       ▼
┌────────────────────────────────────────────┐
│              BuIAM Security Service         │
│                                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Identity │  │ Registry │  │ Gateway  │ │
│  └──────────┘  └──────────┘  └────┬─────┘ │
│                                    │       │
│  ┌────────────┐ ┌───────────────┐ │       │
│  │ Delegation │ │ Intent Service│ │       │
│  └────────────┘ └───────────────┘ │       │
│                                    │       │
│  ┌──────────────────────────────┐ │       │
│  │ Store / Audit / Chain / Tree │ │       │
│  └──────────────────────────────┘ │       │
└────────────────────────────────────┼───────┘
                                     │ 2. 转发已授权请求
                                     ▼
                         ┌────────────────────┐
                         │     Target Agent    │
                         │ local:// 或 HTTP(S) │
                         └────────────────────┘
```

BuIAM Security Service 位于中间层。用户或 Agent 不应直接调用下游 Agent，而是向 BuIAM Gateway 发起请求。Gateway 完成身份验证、意图检查、权限判定、链路记录后，再根据 Registry 中的 endpoint 转发请求。

### 3.2 代码分层

| 目录 | 职责 |
| --- | --- |
| `app/protocol.py` | 协议模型、请求/响应模型、审计模型 |
| `app/identity/` | Token 签发、验签、密钥加载、身份路由 |
| `app/registry/` | Agent 注册接口 |
| `app/gateway/` | Gateway 入口、认证、转发、本地适配器 |
| `app/delegation/` | 权限交集判定、委托链处理 |
| `app/intent/` | 意图生成、意图 Judge、节点哈希与签名、树校验 |
| `app/store/` | SQLite 表结构与数据访问 |
| `app/sdk/` | 调用方 SDK 封装 |
| `examples/` | 示例 Agent、示例工具、示例业务 LLM，不属于安全服务核心 |
| `scripts/demo.py` | 端到端演示脚本 |
| `tests/` | Pytest 测试用例 |

当前核心服务代码原则上不依赖 `examples.*`。唯一例外是 `app/gateway/local_adapter.py`，它用于 demo 环境支持 `local://agent_id` 本地调用；生产环境接入建议使用 HTTP(S) endpoint。

---

## 4. 核心概念

### 4.1 Agent Registry

Registry 保存每个 Agent 的基础信息：

- `agent_id`：全局唯一 Agent 标识。
- `name`：展示名称。
- `endpoint`：调用地址，支持 `local://agent_id` 或 `http://` / `https://`。
- `static_capabilities`：该 Agent 可提供的能力集合。

示例：

```json
{
  "agent_id": "external_search_agent",
  "name": "外部检索 Agent",
  "endpoint": "local://external_search_agent",
  "static_capabilities": ["web.public:read"]
}
```

### 4.2 Capability

Capability 是权限判断的最小粒度。当前 demo 中包含：

- `report:write`：生成报告能力。
- `feishu.contact:read`：读取飞书通讯录。
- `feishu.wiki:read`：读取飞书知识库。
- `feishu.bitable:read`：读取飞书多维表格。
- `web.public:read`：读取公开 Web 信息。

当前权限模型是白名单式能力交集，不支持通配符、不支持隐式继承。新增能力时，需要在 Agent 注册信息、Token 能力和调用请求中明确声明。

### 4.3 DelegationEnvelope

`DelegationEnvelope` 是 A2A 委托请求的核心协议载体。

关键字段：

| 字段 | 说明 |
| --- | --- |
| `protocol_version` | 协议版本，当前为 `buiam.delegation.v1` |
| `trace_id` | 一次用户任务或调用链的全局追踪 ID |
| `request_id` | 当前这一跳调用的唯一 ID |
| `caller_agent_id` | 调用方声明的 Agent ID；服务端会以 Token 身份为准覆盖 |
| `target_agent_id` | 目标 Agent ID |
| `task_type` | 当前任务类型 |
| `requested_capabilities` | 当前这一跳请求使用的能力 |
| `delegation_chain` | 请求携带的轻量委托链上下文 |
| `intent_node` | 当前这一跳的意图树节点 |
| `auth_context` | Gateway 验证 Token 后注入的认证上下文 |
| `payload` | 业务参数，不参与权限算法本身 |

---

## 5. Access Token 设计

### 5.1 Token 类型

当前系统使用本地开发版 JWT-like token，支持两类主体：

- 用户 Token：`actor_type=user`，用于 `/delegate/root-task`。
- Agent Token：`actor_type=agent`，用于 `/delegate/call`。

### 5.2 Token Header

```json
{
  "alg": "BUIAM-RS256",
  "typ": "JWT",
  "kid": "doc_agent"
}
```

说明：

- `alg`：当前项目自定义标识为 `BUIAM-RS256`。
- `typ`：固定 `JWT`。
- `kid`：签名密钥 ID，当前等于用户 ID 或 Agent ID。

### 5.3 Token Claims

```json
{
  "jti": "tok_xxx",
  "sub": "doc_agent",
  "agent_id": "doc_agent",
  "actor_type": "agent",
  "delegated_user": "user_123",
  "capabilities": ["report:write", "web.public:read"],
  "iat": 1777118698,
  "exp": 1777122298,
  "iss": "buiam.local",
  "aud": "buiam.agents"
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `jti` | Token 唯一 ID，用于登记、审计和吊销 |
| `sub` | Token 主体，当前等于 `agent_id` 或用户 ID |
| `agent_id` | 当前调用身份 ID；用户 Token 中为用户 ID |
| `actor_type` | `user` 或 `agent` |
| `delegated_user` | 本次委托链所属用户 |
| `capabilities` | 当前 Token 允许携带/委托的能力集合 |
| `iat` | 签发时间 |
| `exp` | 过期时间 |
| `iss` | 签发方，当前固定 `buiam.local` |
| `aud` | 受众，当前固定 `buiam.agents` |

### 5.4 Token 存储与吊销

签发 Token 时，系统会将 `jti`、主体、能力、过期时间和吊销状态写入 SQLite `tokens` 表。验证时除校验签名与标准声明外，还会检查：

- `jti` 是否已登记。
- Token 是否过期。
- Token 是否已被吊销。
- `iss` 是否匹配。
- `aud` 是否匹配。
- 签名是否有效。

吊销通过 `POST /identity/tokens/{jti}/revoke` 完成。吊销状态持久化在 SQLite，不是纯内存黑名单。

### 5.5 Token 安全审计

系统不会在审计中保存完整 Token。审计表 `auth_events` 只保存：

```text
token_fingerprint = sha256(token)
```

这样既能关联排查同一个 Token 的使用情况，又避免审计日志泄露可直接使用的凭证。

---

## 6. A2A 身份认证流程

### 6.1 用户入口流程：User -> Gateway -> 首个 Agent

用户任务通过 `POST /delegate/root-task` 进入系统。

```text
1. User 携带用户 Bearer Token 调用 /delegate/root-task
2. Gateway 校验 Authorization header
3. Gateway 解析并验签 Token
4. Gateway 检查 actor_type 必须为 user
5. Gateway 调用 LLM 生成 root intent_commitment
6. Gateway 使用 user_123 私钥签名 root IntentNode
7. Gateway 校验 root IntentNode 的 node_id、签名、身份匹配和 Judge 结果
8. Gateway 写入 auth_events、intent_tree、delegation_chain、audit_logs
9. Gateway 根据 Registry 转发给入口 Agent
```

该入口把 `user -> first_agent` 也作为正式链路写入审计，不再是隐式步骤。

### 6.2 Agent 间流程：Agent A -> Gateway -> Agent B

Agent 间调用通过 `POST /delegate/call` 完成。

```text
1. Agent A 携带自身 Bearer Token 调用 /delegate/call
2. Gateway 校验 Token，得到 AuthContext
3. Gateway 使用 Token 身份覆盖 caller_agent_id，防止请求体冒充身份
4. 若请求没有 intent_node 且 payload 中存在 user_task，Gateway 自动生成本跳 child intent
5. Gateway 校验 IntentNode：node_id、签名、actor 与 Token 身份、父节点、分支完整性
6. Gateway 调用 LLM Judge 判断 I_root、I_parent、I_child 是否一致
7. Gateway 查询目标 Agent 静态能力
8. DelegationService 计算权限交集
9. 通过则追加委托链 hop 并转发给目标 Agent
10. 拒绝则返回 403，并写入完整审计
```

### 6.3 认证失败处理

以下情况会返回 401，并写入 `auth_events`：

| 场景 | error_code |
| --- | --- |
| 缺少 Authorization header | `AUTH_TOKEN_MISSING` |
| Bearer 格式错误 | `AUTH_TOKEN_INVALID` |
| Token 结构无法解析 | `AUTH_TOKEN_MALFORMED` |
| 签名错误 | `AUTH_TOKEN_SIGNATURE_INVALID` |
| Token 过期 | `AUTH_TOKEN_EXPIRED` |
| Token 已吊销 | `AUTH_TOKEN_REVOKED` |
| jti 未登记 | `AUTH_TOKEN_JTI_NOT_REGISTERED` |
| issuer 不匹配 | `AUTH_TOKEN_ISSUER_MISMATCH` |
| audience 不匹配 | `AUTH_TOKEN_AUDIENCE_MISMATCH` |

---

## 7. 授权模型

### 7.1 权限交集算法

当前 A2A 授权规则为：

```text
effective_capabilities =
    requested_capabilities
    ∩ caller_token_capabilities
    ∩ target_agent_static_capabilities
    ∩ delegated_user_capabilities
```

判定逻辑：

- 如果 `effective_capabilities` 覆盖全部 `requested_capabilities`，则 `allow`。
- 如果存在任一请求能力不在交集中，则 `deny`。

### 7.2 各能力来源

| 来源 | 说明 |
| --- | --- |
| `requested_capabilities` | 当前请求声明需要使用哪些能力 |
| `caller_token_capabilities` | 调用方 Token 中允许委托的能力 |
| `target_agent_static_capabilities` | 目标 Agent 注册时声明可提供的能力 |
| `delegated_user_capabilities` | 用户在当前链路中授权的能力，当前来自 Token |

### 7.3 拒绝原因解释

授权拒绝时，`decision_detail` 中会给出：

- `missing_capabilities`：整体缺失能力。
- `missing_by.caller_token`：调用方 Token 不具备的能力。
- `missing_by.target_agent`：目标 Agent 不具备的能力。
- `missing_by.user`：用户未授权的能力。

这样可以区分“调用方越权”“目标 Agent 不支持”“用户未授权”等不同失败原因。

---

## 8. 意图承诺与意图调用树

### 8.1 设计目的

多 Agent 委托中可能出现“意图漂移”：用户原本只想查询公开天气，但中间 Agent 可能将任务扩展为读取企业内部数据。为此系统引入：

- 用户意图承诺 `intent_commitment`。
- 支持分支的意图调用树 `intent_tree`。
- 每跳节点哈希与签名。
- 每跳 LLM Judge 意图一致性检查。

### 8.2 IntentCommitment

```json
{
  "intent": "检索今日公开天气信息",
  "description": "用户请求查询今日天气数据",
  "data_refs": [],
  "constraints": ["仅允许读取公开天气数据", "不涉及私有数据访问"]
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `intent` | 简短意图，强调用户想做什么 |
| `description` | 补充说明，不应承载敏感业务数据 |
| `data_refs` | 数据引用标识，而不是直接保存敏感数据 |
| `constraints` | 约束，例如只读、公开数据、禁止访问私有数据 |

### 8.3 IntentNode

```json
{
  "node_id": "...",
  "parent_node_id": "...",
  "actor_id": "doc_agent",
  "actor_type": "agent",
  "target_agent_id": "external_search_agent",
  "task_type": "search_public_web",
  "intent_commitment": {},
  "signature": "...",
  "signature_alg": "BUIAM-RS256"
}
```

### 8.4 节点哈希与签名规则

节点核心内容 `self_content` 不包含 `node_id` 和 `signature`，包含：

```json
{
  "protocol_version": "buiam.intent.v1",
  "parent_node_id": "...",
  "actor_id": "...",
  "actor_type": "user|agent",
  "target_agent_id": "...",
  "task_type": "...",
  "intent_commitment": {}
}
```

计算规则：

```text
signature = sign(actor_private_key, canonical_json(self_content))
node_id = sha256(parent_node_id_or_ROOT + canonical_json(self_content))
content_hash = sha256(canonical_json(self_content))
```

说明：

- `node_id` 不包含 `signature`，避免 “node_id 依赖签名、签名又依赖 node_id” 的循环依赖。
- `signature` 单独证明某个 actor 确实声明了该节点内容。
- `node_id` 绑定父节点 ID 和自身内容，使链路具备不可篡改性。
- `content_hash` 存入数据库，用于后续校验节点内容是否被修改。

### 8.5 分支与校验

意图树支持多分支。每一次新增节点时，只校验新节点所在分支，不影响同一 trace 下其他分支。

校验内容包括：

- `node_id` 是否可重算匹配。
- `signature` 是否可用 actor 公钥验证。
- 当前节点 actor 是否与 Token 身份匹配。
- root 节点必须由用户签名。
- 非 root 节点必须引用已存在父节点。
- 从当前节点回溯到 root 的父链必须完整、无环、哈希一致。
- LLM Judge 返回必须是 `Consistent`，否则拒绝当前分支。

### 8.6 LLM Judge

Judge 输入：

```json
{
  "root_intent": "...",
  "parent_intent": "...",
  "child_intent": "...",
  "task_type": "...",
  "target_agent_id": "..."
}
```

Judge 输出只接受：

```json
{
  "decision": "Consistent",
  "reason": "..."
}
```

或：

```json
{
  "decision": "Drifted",
  "reason": "..."
}
```

如果 Judge 返回 `Drifted`，Gateway 返回 `403 INTENT_DRIFTED`。如果 LLM 调用失败或输出格式错误，返回 `403 INTENT_JUDGE_FAILED`，默认失败关闭，不放行。

---

## 9. 审计与追踪

当前系统将审计拆成三类表，避免把所有信息混在一条链路里。

### 9.1 auth_events：身份验证审计

记录每一次 Gateway 入口 Token 验证过程。包括成功和失败。

关键字段：

| 字段 | 说明 |
| --- | --- |
| `trace_id` / `request_id` | 与其他审计表关联 |
| `caller_agent_id` | Token 验证得到的真实调用方 |
| `claimed_agent_id` | 请求体中声称的调用方 |
| `token_jti` | Token ID |
| `token_sub` | Token 主体 |
| `token_agent_id` | Token 中的 actor ID |
| `delegated_user` | 所属用户 |
| `token_fingerprint` | `sha256(token)` |
| `token_issued_at` / `token_expires_at` | 签发与过期时间 |
| `verified_at` | 验证时间 |
| `is_expired` | 是否过期 |
| `is_revoked` | 是否已吊销 |
| `is_jti_registered` | jti 是否存在于服务端登记表 |
| `signature_valid` | 签名是否有效 |
| `issuer_valid` | issuer 是否有效 |
| `audience_valid` | audience 是否有效 |
| `identity_decision` | `allow` 或 `deny` |
| `error_code` | 失败错误码 |
| `reason` | 说明 |

### 9.2 audit_logs：授权判定审计

记录每一次 root task 或 A2A 委托的授权/拒绝结果。

关键字段：

| 字段 | 说明 |
| --- | --- |
| `trace_id` / `request_id` | 链路关联键 |
| `caller_agent_id` | 调用方 |
| `target_agent_id` | 目标 Agent |
| `requested_capabilities` | 请求能力 |
| `effective_capabilities` | 交集后实际允许能力 |
| `decision` | `allow` 或 `deny` |
| `reason` | 判定原因 |
| `decision_detail` | 完整判定上下文 |

`decision_detail` 包含身份摘要、权限拆解和意图摘要：

- `auth_event_recorded`
- `token_jti`
- `token_agent_id`
- `requested_capabilities`
- `caller_token_capabilities`
- `target_agent_capabilities`
- `user_capabilities`
- `effective_capabilities`
- `missing_capabilities`
- `missing_by`
- `intent_node_id`
- `parent_intent_node_id`
- `root_intent`
- `parent_intent`
- `child_intent`
- `intent_generation_model`
- `intent_judge_decision`
- `intent_judge_reason`

### 9.3 delegation_chain：轻量委托链

`delegation_chain` 用于人类快速查看链路，不保存完整认证与意图细节。

字段：

- `from_actor`
- `to_agent_id`
- `task_type`
- `delegated_capabilities`
- `missing_capabilities`
- `decision`

示例：

```json
[
  {
    "from_actor": "user_123",
    "to_agent_id": "doc_agent",
    "task_type": "ask_weather",
    "delegated_capabilities": ["report:write", "web.public:read"],
    "missing_capabilities": [],
    "decision": "root"
  },
  {
    "from_actor": "doc_agent",
    "to_agent_id": "external_search_agent",
    "task_type": "search_public_web",
    "delegated_capabilities": ["web.public:read"],
    "missing_capabilities": [],
    "decision": "allow"
  }
]
```

### 9.4 intent_tree：意图调用树

保存每个意图节点：

- `node_id`
- `parent_node_id`
- `root_node_id`
- `trace_id`
- `request_id`
- `actor_id`
- `actor_type`
- `target_agent_id`
- `task_type`
- `intent`
- `description`
- `data_refs`
- `constraints`
- `signature`
- `signature_alg`
- `content_hash`
- `judge_decision`
- `judge_reason`

---

## 10. API 接口定义

### 10.1 健康检查

#### `GET /health`

响应：

```json
{
  "status": "ok"
}
```

### 10.2 注册 Agent

#### `POST /registry/agents`

请求：

```json
{
  "agent_id": "doc_agent",
  "name": "飞书文档助手 Agent",
  "endpoint": "local://doc_agent",
  "static_capabilities": ["report:write"]
}
```

响应：

```json
{
  "agent_id": "doc_agent",
  "name": "飞书文档助手 Agent",
  "endpoint": "local://doc_agent",
  "static_capabilities": ["report:write"]
}
```

#### `GET /registry/agents`

返回全部 Agent 注册信息。

#### `GET /registry/agents/{agent_id}`

返回指定 Agent 注册信息。不存在时返回 `404 AGENT_NOT_REGISTERED`。

### 10.3 签发 Token

#### `POST /identity/tokens`

请求：

```json
{
  "agent_id": "doc_agent",
  "delegated_user": "user_123",
  "actor_type": "agent",
  "capabilities": ["report:write", "web.public:read"],
  "ttl_seconds": 3600
}
```

用户 Token 示例：

```json
{
  "agent_id": "user_123",
  "delegated_user": "user_123",
  "actor_type": "user",
  "capabilities": ["report:write", "web.public:read"],
  "ttl_seconds": 3600
}
```

响应：

```json
{
  "access_token": "<token>",
  "token_type": "bearer",
  "jti": "tok_xxx",
  "exp": 1777122298
}
```

### 10.4 吊销 Token

#### `POST /identity/tokens/{jti}/revoke`

响应：

```json
{
  "jti": "tok_xxx",
  "revoked": true
}
```

### 10.5 用户根任务入口

#### `POST /delegate/root-task`

Header：

```text
Authorization: Bearer <user_access_token>
```

请求：

```json
{
  "trace_id": null,
  "request_id": null,
  "target_agent_id": "doc_agent",
  "task_type": "ask_weather",
  "user_task": "帮我查询今天的公开天气信息",
  "requested_capabilities": ["report:write", "web.public:read"],
  "payload": {
    "query": "今天的天气怎么样"
  }
}
```

处理结果：

- 校验用户 Token。
- 生成 root intent。
- 签名并记录 root IntentNode。
- 写入 root delegation hop。
- 转发给 `target_agent_id`。

响应：

```json
{
  "agent_id": "doc_agent",
  "trace_id": "...",
  "task_type": "ask_weather",
  "result": {}
}
```

### 10.6 Agent 委托调用

#### `POST /delegate/call`

Header：

```text
Authorization: Bearer <agent_access_token>
```

请求：

```json
{
  "protocol_version": "buiam.delegation.v1",
  "trace_id": "...",
  "request_id": "...",
  "caller_agent_id": "doc_agent",
  "target_agent_id": "external_search_agent",
  "task_type": "search_public_web",
  "requested_capabilities": ["web.public:read"],
  "delegation_chain": [],
  "intent_node": null,
  "auth_context": null,
  "payload": {
    "user_task": "帮我查询今天的公开天气信息",
    "parent_intent_node_id": "...",
    "query": "今天的天气怎么样"
  }
}
```

说明：

- `caller_agent_id` 仅作为声明值。Gateway 会以 Token 中的 `agent_id` 作为真实身份覆盖该字段。
- 如果 `intent_node=null` 且 `payload.user_task` 存在，Gateway 会自动调用 LLM 生成当前跳意图节点。
- 如果调用方自行构造 `intent_node`，Gateway 仍会重新校验节点哈希、签名、父链和身份匹配。

### 10.7 审计查询

#### `GET /audit/logs`

返回全部授权审计日志。

#### `GET /audit/auth-events`

支持过滤参数：

- `trace_id`
- `request_id`
- `jti`
- `agent_id`
- `decision`

示例：

```text
GET /audit/auth-events?trace_id=xxx&decision=deny
```

#### `GET /audit/traces/{trace_id}`

一次性返回完整复盘信息：

```json
{
  "trace_id": "...",
  "logs": [],
  "chain": [],
  "auth_events": [],
  "intent_tree": []
}
```

#### `GET /audit/traces/{trace_id}/chain`

只返回轻量委托链。

#### `GET /audit/traces/{trace_id}/intent-tree`

只返回该 trace 的意图树节点。

#### `GET /audit/intent-nodes/{node_id}`

按节点 ID 查询单个意图节点。

---

## 11. 数据库表设计

当前使用 SQLite，默认数据库路径为 `data/audit.db`。

### 11.1 agents

保存 Agent 注册信息。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `agent_id` | TEXT PRIMARY KEY | Agent ID |
| `name` | TEXT | Agent 名称 |
| `endpoint` | TEXT | 调用地址 |
| `static_capabilities` | TEXT | JSON 数组 |
| `created_at` | TEXT | 创建时间 |

### 11.2 tokens

保存 Token 登记和吊销状态。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `jti` | TEXT PRIMARY KEY | Token ID |
| `sub` | TEXT | 主体 |
| `agent_id` | TEXT | actor ID |
| `actor_type` | TEXT | `user` 或 `agent` |
| `delegated_user` | TEXT | 所属用户 |
| `capabilities` | TEXT | JSON 数组 |
| `exp` | INTEGER | 过期时间 |
| `revoked` | INTEGER | 是否吊销 |
| `created_at` | TEXT | 创建时间 |

### 11.3 jti_seen

记录已验证过的 jti 首次出现时间。

### 11.4 auth_events

身份认证审计表，字段见第 9.1 节。

### 11.5 audit_logs

授权判定审计表，字段见第 9.2 节。

### 11.6 delegation_chain

轻量委托链表，字段见第 9.3 节。

### 11.7 intent_tree

意图树表，字段见第 9.4 节。

---

## 12. 配置项

当前 `.env` 支持以下配置。

```env
# 示例 Agent 业务 LLM，建议 demo 保持 mock，避免业务结果不可控
LLM_PROVIDER=mock

# BuIAM 意图生成 provider
INTENT_GENERATOR_PROVIDER=openai

# BuIAM 意图漂移 Judge provider
INTENT_JUDGE_PROVIDER=openai

# OpenAI-compatible endpoint
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_API_KEY=<your_api_key>
OPENAI_MODEL=<model_name>

# Anthropic-compatible endpoint
ANTHROPIC_BASE_URL=https://api.example.com/anthropic
ANTHROPIC_API_KEY=<your_api_key>
ANTHROPIC_MODEL=<model_name>
```

说明：

- `LLM_PROVIDER` 用于示例 Agent 的业务生成，不属于安全服务核心判断。
- `INTENT_GENERATOR_PROVIDER` 用于 BuIAM 生成 root/child intent。
- `INTENT_JUDGE_PROVIDER` 用于 BuIAM 判断意图是否漂移。
- 未配置必要 API Key 时，意图生成或 Judge 会失败关闭，不默认放行。

---

## 13. 端到端示例流程

### 13.1 正常委托

链路：

```text
user_123 -> doc_agent -> enterprise_data_agent
```

流程：

1. 用户调用 `/delegate/root-task`，提出生成飞书协作报告。
2. Gateway 验证用户 Token，生成 root intent。
3. Gateway 转发给 `doc_agent`。
4. `doc_agent` 需要企业数据，调用 `/delegate/call` 请求 `enterprise_data_agent`。
5. Gateway 验证 `doc_agent` Token。
6. Gateway 生成并校验 child intent。
7. Judge 判定该意图服务于报告生成，结果为 `Consistent`。
8. 权限交集覆盖请求能力，授权通过。
9. Gateway 转发给 `enterprise_data_agent`。
10. 审计中可看到两个 auth event、两条 audit log、两跳 chain、两个 intent node。

### 13.2 越权/漂移委托

链路：

```text
user_123 -> doc_agent -> external_search_agent -> enterprise_data_agent
```

流程：

1. 用户任务是查询今日公开天气。
2. `doc_agent -> external_search_agent` 请求公开 Web 搜索，意图一致且能力满足，允许。
3. `external_search_agent -> enterprise_data_agent` 尝试读取企业数据。
4. Gateway 生成并校验该跳 intent node。
5. Judge 结合 `root_intent`、`parent_intent`、`child_intent`、`task_type=read_enterprise_data` 和 `target_agent_id=enterprise_data_agent` 判定漂移，返回 `INTENT_DRIFTED`。
6. 如果 Judge 未判漂移，权限交集也会因为 `external_search_agent` Token 只有 `web.public:read` 而拒绝企业数据能力。
7. 拒绝分支仍会记录 auth event、audit log、chain hop 和 intent tree node，不影响其他合法分支。

---

## 14. 安全特性总结

### 14.1 已实现特性

- 每跳强制 Gateway 入口认证。
- 请求体声明身份不可信，以 Token 身份为准。
- Token 持久化登记，支持过期、吊销和 jti 检查。
- 审计日志不保存完整 Token，只保存 SHA256 指纹。
- Agent 静态能力与 Token 能力、用户授权能力做交集，避免权限扩大。
- 每跳意图节点签名，支持不可抵赖。
- 节点 ID 绑定父节点和自身内容，支持篡改检测。
- 意图树支持分支，单分支失败不影响其他分支。
- LLM Judge 失败关闭。

### 14.2 当前 MVP 限制

- 当前 RSA 签名实现是开发版简化实现，不是生产级密码库封装。
- SQLite 适合 MVP 和本地演示，生产环境建议替换为 PostgreSQL/MySQL 等托管数据库。
- 本地 demo 的 `local://` 适配器仅用于演示，生产应使用 HTTP(S) Agent endpoint。
- 当前没有强制网络层阻止 Agent 之间绕过 Gateway 直连；生产需要通过网络策略、服务网格、mTLS 或 Agent SDK 强制执行。
- 当前 Capability 是静态字符串列表，后续可扩展为策略 DSL 或 ABAC/RBAC 混合模型。

---

## 15. 部署与运行

### 15.1 安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 15.2 启动服务

```bash
uvicorn app.main:app --reload
```

### 15.3 运行 Demo

```bash
python scripts/demo.py
```

### 15.4 运行测试

```bash
pytest tests -p no:cacheprovider
```

注意：如果测试中启用了真实 LLM 调用，需要 `.env` 中配置可用的 API Key、Base URL 和模型名。

---

## 16. 交付验收建议

建议按以下用例验收：

1. Agent 注册：能注册 `local://` 和 HTTP endpoint。
2. Token 签发：能签发用户 Token 和 Agent Token。
3. Token 验证：缺失、格式错误、过期、吊销、签名错误均写入 `auth_events`。
4. 正常委托：`user_123 -> doc_agent -> enterprise_data_agent` 成功。
5. 越权委托：`external_search_agent -> enterprise_data_agent` 被拒绝。
6. 意图漂移：公开天气任务转向企业数据访问时返回 `INTENT_DRIFTED` 或权限拒绝。
7. 审计关联：同一个 `trace_id/request_id` 可关联 `auth_events`、`audit_logs`、`delegation_chain`、`intent_tree`。
8. 安全边界：核心模块除 `local_adapter` 外不依赖示例 Agent。

---

## 17. 后续演进路线

### 17.1 生产级身份体系

- 替换开发版签名为标准 JWT/JWS 库。
- 引入 JWKS、KMS 或 HSM 管理密钥。
- 支持 mTLS 绑定 Agent 身份。
- 支持短 Token + Refresh Token 或 Token Exchange。

### 17.2 Gateway 强制接入

- 所有 Agent 只暴露给 Gateway 网络域。
- 使用服务网格或 API Gateway 禁止 Agent 之间直连。
- Gateway 转发时注入网关签名上下文。
- Agent SDK 验证网关签名，拒绝非 Gateway 请求。

### 17.3 策略能力增强

- Capability 从静态列表升级为策略 DSL。
- 加入资源级约束，例如具体文档、表格、字段、租户、时间窗口。
- 引入审批流与人工确认。
- 增加 Prompt Injection 与数据外泄检测。

### 17.4 审计与合规

- 审计日志写入不可变存储或日志平台。
- 支持审计导出、报表、告警。
- 对 intent tree 增加 Merkle Root 或批量上链摘要。
- 对关键拒绝事件触发告警。

---

## 18. 结论

当前 BuIAM 原型已经形成“用户/Agent 身份认证 + A2A 权限委托 + 意图承诺防漂移 + 独立审计追踪”的完整闭环。系统通过 Gateway 统一接管每一跳调用，在调用前验证身份、校验意图链、执行权限交集判定，并把认证、授权、委托链和意图树分别落库，能够满足 Demo 阶段对可解释、可审计、可扩展的多 Agent 安全委托需求。

下一阶段建议重点建设生产级密钥体系、网络层强制接入、标准策略引擎和企业级审计平台，以便从 MVP 演示升级为可落地的企业安全中间件。
