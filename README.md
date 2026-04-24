# BuIAM Delegation Protocol MVP

飞书校园挑战赛最小验证系统：3 个 Agent 协作，权限委托与审计由外置 Delegation Service 统一处理。

## 架构

- `doc_agent`：飞书文档助手，编排报告生成，并通过外置协议委托企业数据 Agent。
- `enterprise_data_agent`：唯一拥有企业通讯录、知识库、多维表格 mock 访问能力的 Agent。
- `external_search_agent`：只能访问公开网页 mock 检索能力。
- `delegation_service`：外置安全协议层，负责能力解析、权限交集、委托链追加和 SQLite 审计。

Agent 不直接做权限判定；Agent 间调用必须走 `POST /delegate/call`。

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

另开终端运行演示：

```bash
python scripts/demo.py
```

也可以不启动服务，直接运行测试：

```bash
pytest
```

## LLM 配置

默认使用 mock LLM，保证无 Key 可复现。

```bash
set LLM_PROVIDER=mock
set LLM_PROVIDER=openai
set OPENAI_API_KEY=你的_KEY
set LLM_PROVIDER=anthropic
set ANTHROPIC_API_KEY=你的_KEY
```

## 核心协议

统一请求 Envelope 字段：

- `trace_id`：完整任务链路 ID。
- `request_id`：当前请求 ID。
- `caller_agent_id`：调用方 Agent。
- `target_agent_id`：目标 Agent。
- `task_type`：任务类型。
- `requested_capabilities`：本次请求需要的能力集合。
- `delegation_chain`：从用户到当前 Agent 的链路。
- `payload`：业务参数。

最小 Capability：

- `report:write`
- `feishu.contact:read`
- `feishu.wiki:read`
- `feishu.bitable:read`
- `web.public:read`

授权规则：

```text
effective_capabilities = caller_delegatable ∩ target_static ∩ requested
```

若 `effective_capabilities` 覆盖全部请求能力，则允许；否则拒绝并写审计日志。

## API

- `GET /health`：健康检查。
- `POST /delegate/call`：唯一 Agent 间授权调用入口。
- `GET /audit/logs`：查看全部审计日志。
- `GET /audit/traces/{trace_id}`：查看指定调用链。

## 验收流程

1. 正常委托：`doc_agent` 生成报告，委托 `enterprise_data_agent` 读取企业数据，返回报告。
2. 越权拦截：`external_search_agent` 尝试委托 `enterprise_data_agent` 读取企业数据，返回 `403 delegation_denied`。
3. 审计追踪：访问 `/audit/logs` 或 `/audit/traces/{trace_id}` 查看 allow/deny 决策上下文。

## 后续扩展点

- 在 `delegation_service` 替换真实外置安全模型。
- 增加 JWT、签名验签、Token 撤销、策略 DSL、Prompt Injection 防护。
- 将单进程路由拆分成多服务部署，Agent 业务代码无需改变。

## Mock 前置认证闭环

当前 `DelegationEnvelope` 会携带 `auth_context`，模拟前置 Agent 身份认证与授权结果：

```json
{
  "jti": "tok_001",
  "sub": "doc_agent",
  "exp": 9999999999,
  "delegated_user": "user_123",
  "agent_id": "doc_agent",
  "capabilities": ["feishu.contact:read"],
  "sig": null
}
```

外置 `DelegationService` 的最小授权算法已升级为：

```text
effective = caller_token_caps ∩ target_agent_caps ∩ requested_caps ∩ user_caps
```

同时会做这些 mock 校验：

- `jti` 是否在内存黑名单 `blacklist` 中。
- token 来源校验占位 `verify_token_source()`，当前直接返回 `true`。
- 伪签名校验 `verify_sig()`，缺少 `sig` 时兼容 mock 根请求。
- 委托链连续性检查：最后一跳 `to_agent_id` 必须等于当前 `caller_agent_id`。
- 授权通过后追加 `DelegationHop`，并将下一跳 `auth_context.capabilities` 收缩为 `effective_capabilities`。

当前 Agent Registry、Token、Chain、Audit 均由 SQLite 存储；示例 Agent 的注册逻辑只存在于 `scripts/demo.py`。

### DelegationHop 与 DecisionDetail

`delegation_chain` 只保留轻量、人类友好的链路：

- `from_actor` / `to_agent_id`：这一跳从谁到谁。
- `task_type`：这一跳要执行的任务。
- `delegated_capabilities`：这一跳最终委托出去的能力；拒绝时为空。
- `missing_capabilities`：这一跳缺少的能力；拒绝时用于解释这一跳为什么失败。
- `decision`：`root/allow/deny`，其中 `root` 表示用户入口 mock 前置授权。

完整授权复盘放在审计日志的 `decision_detail` 中，包括 `requested_capabilities`、`caller_token_capabilities`、`target_agent_capabilities`、`user_capabilities`、`effective_capabilities`、`missing_capabilities`、`missing_by`、`decision` 和 `reason`。

## 即插即用服务化结构

当前代码已拆成两层：

- `app/`：BuIAM 安全服务核心，只放 Identity、Registry、Gateway、Delegation、Audit、SDK、Store、Protocol。
- `examples/`：示例 Agent、mock tools 与 LLM 适配器，不属于安全服务核心。

唯一允许安全服务引用示例 Agent 的地方是 `app/gateway/local_adapter.py`，用于支持 `local://agent_id` demo endpoint。真实接入时，外部 Agent 应注册 HTTP endpoint。

### 核心接口

- `POST /registry/agents`：注册 Agent 的 `agent_id/name/endpoint/static_capabilities`。
- `GET /registry/agents`：查看 Agent 注册表。
- `POST /identity/tokens`：使用 `data/keys/{agent_id}_private.pem` 签发开发版 RSA JWT。
- `POST /identity/tokens/{jti}/revoke`：吊销 token。
- `POST /delegate/call`：Gateway 统一入口，必须带 `Authorization: Bearer <token>`。
- `GET /audit/logs`：查询审计日志。
- `GET /audit/traces/{trace_id}`：查询审计日志与独立 chain。
- `GET /audit/traces/{trace_id}/chain`：只查询 delegation chain。

### RSA Key

启动时会为当前三个 demo Agent 准备 key 文件：

```text
data/keys/doc_agent_private.pem
data/keys/doc_agent_public.pem
data/keys/enterprise_data_agent_private.pem
data/keys/enterprise_data_agent_public.pem
data/keys/external_search_agent_private.pem
data/keys/external_search_agent_public.pem
```

这些 key 是开发演示用，已被 `.gitignore` 忽略。

### 外部 Agent 接入流程

1. 启动 BuIAM 服务。
2. 调 `POST /registry/agents` 注册 Agent endpoint。
3. 调 `POST /identity/tokens` 为调用方 Agent 签发 token。
4. Agent 调用其它 Agent 时统一请求 `POST /delegate/call`。
5. 通过 `/audit/traces/{trace_id}` 或 `/audit/traces/{trace_id}/chain` 复盘链路。
