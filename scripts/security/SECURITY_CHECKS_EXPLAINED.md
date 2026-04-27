# run_all_security_checks.py 输出说明

这个文档解释 `python scripts/security/run_all_security_checks.py` 每一步在验证什么，以及为什么这些输出能说明 BuIAM 的安全机制生效了。

当前业务动作仍然是 mock provider，例如 mock 文档写入、mock 企业数据、mock 公网搜索；但下面验证的认证、授权、委托链、意图链、吊销、过期、审计都是 Gateway 中真实执行的安全链路。

## 总体流程

`run_all_security_checks.py` 会启动或复用四个服务：

```text
Gateway
doc_agent
enterprise_data_agent
external_search_agent
```

然后按顺序运行：

```text
verify_delegation_chain
verify_intent_chain
verify_chain_binding
verify_a2a_identity
verify_token_lifecycle
```

每个 `[PASS]` 表示该脚本构造的正常请求或恶意请求被系统按预期处理。换句话说，不是“请求都成功”才算通过，而是“该允许的允许，该拒绝的拒绝，该吊销的吊销，该取消的取消”。

## 常见字段含义

### trace_id

一次任务链路的全局追踪 ID。用户 root task、agent-to-agent 调用、credential、intent、audit log、auth event 都应该能通过同一个 `trace_id` 串起来。

### request_id

某一跳请求的 ID。比如 `user -> doc_agent` 是一跳，`doc_agent -> enterprise_data_agent` 又是一跳。它用于绑定同一跳里的 credential、intent node 和 audit decision。

### credential_id

委托凭证 ID，也是该 credential 的链式哈希：

```text
credential_id = sha256(parent_credential_id_or_ROOT + canonical_json(self_content))
```

它不是随机 ID，而是由当前凭证内容和父凭证 ID 算出来的。因此篡改父节点、subject、capabilities、exp、trace 等关键字段都会导致哈希或签名校验失败。

### node_id

意图节点 ID，也是 intent node 的链式哈希：

```text
node_id = sha256(parent_node_id_or_ROOT + canonical_json(self_content))
```

因此篡改 intent 内容、actor、target、task_type 或 parent_node_id 都会破坏 node_id 校验。

### human_chain

也就是 `delegation_chain`，只做人类可读审计摘要。它能帮助理解链路，但不是安全事实来源。

真正用于授权判断的是 `delegation_credentials` 中的 signed credential 链。

## 1. verify_delegation_chain

输出示例：

```text
[PASS] verify_delegation_chain
- trace_id: "..."
- credential_path: [...]
- human_chain: [...]
```

### 它验证了什么

这一步验证 signed delegation credential 链是否正确构造：

```text
user_123 -> doc_agent -> enterprise_data_agent
```

典型路径是：

```text
root credential:
  issuer_id=user_123
  subject_id=user_123
  parent_credential_id=null

doc credential:
  issuer_id=user_123
  subject_id=doc_agent
  parent_credential_id=root credential

enterprise credential:
  issuer_id=doc_agent
  subject_id=enterprise_data_agent
  parent_credential_id=doc credential
```

### Gateway 拦截了什么

`doc_agent` 想调用 `enterprise_data_agent` 时，并不是本地 import 或直接绕过授权，而是走：

```text
POST /a2a/agents/enterprise_data_agent/tasks
```

Gateway 在这一步拦截请求，并做这些检查：

- Bearer token 是否有效。
- envelope 里的当前 credential 是否存在。
- credential 的 `subject_id` 是否等于 Bearer token 代表的 agent。
- credential 是否能重算 `content_hash` 和 `credential_id`。
- credential 签名是否能用 `issuer_id` 的公钥验证。
- parent/root credential 是否存在。
- child capabilities 是否没有超过 parent capabilities。
- credential/root/parent 是否 revoked 或 expired。
- requested capabilities 是否在 caller token、target agent、user capabilities 的交集中。

### 为什么说明安全机制生效

你看到：

```json
"signature_valid": true
```

说明每个 credential 的签名都能用签发者公钥验证。

你看到：

```json
"parent_credential_id": "..."
"root_credential_id": "..."
```

说明它不是孤立 token，而是一条能从当前节点溯源到 root 的 hash chain。

你看到 `enterprise_data_agent` 的能力变成：

```json
["feishu.bitable:read", "feishu.calendar:read", "feishu.contact:read", "feishu.wiki:read"]
```

说明能力发生了收缩：`doc_agent` 虽然有更多能力，但转给 `enterprise_data_agent` 的只剩企业数据 agent 自己能执行、且被请求允许的那部分。

## 2. verify_intent_chain

输出示例：

```text
[PASS] verify_intent_chain
- trace_id: "..."
- intent_path: [...]
- intent_tree: [...]
```

### 它验证了什么

这一步验证意图链是否正确构造：

```text
root intent:
  user_123 requests doc_agent to run generate_report

child intent:
  doc_agent requests enterprise_data_agent to run read_enterprise_data
```

child intent 的 `parent_node_id` 必须指向 root intent 的 `node_id`。

### Gateway 拦截了什么

在 root task 阶段，Gateway 为用户请求生成并签名 root intent。

在 A2A 阶段，Gateway 为 agent-to-agent 调用生成或校验 child intent，并做这些检查：

- `node_id` 是否可以由节点内容和 parent id 重算。
- `signature` 是否能用 `actor_id` 的公钥验证。
- root intent 必须由 delegated user 签名。
- child intent 必须由当前 caller agent 签名。
- parent intent 必须存在。
- parent intent 必须属于同一个 trace，不能跨 trace 混用。
- intent judge 结果必须是 `Consistent`，如果是 `Drifted` 就拒绝。

### 为什么说明安全机制生效

你看到：

```json
"actor_id": "user_123",
"actor_type": "user",
"target_agent_id": "doc_agent"
```

说明 root intent 是用户发起的。

你看到：

```json
"actor_id": "doc_agent",
"actor_type": "agent",
"target_agent_id": "enterprise_data_agent",
"parent_node_id": "<root node id>"
```

说明下游调用不是凭空出现的，它承接了用户 root intent。

你看到：

```json
"signature_valid": true
```

说明意图节点有不可抵赖性：谁发起的 intent，就由谁对应的 key 签名。

## 3. verify_chain_binding

输出示例：

```text
[PASS] verify_chain_binding
- caller_credential_id: "..."
- child_credential_id: "..."
- root_intent_node_id: "..."
- child_intent_node_id: "..."
- shared_request_id: "..."
```

### 它验证了什么

这一步验证同一跳 A2A 调用中的三个安全事实是否绑定在一起：

```text
credential
intent node
audit decision
```

以 `doc_agent -> enterprise_data_agent` 为例：

- caller credential 是 `doc_agent` 当前持有的 credential。
- child credential 是 Gateway 给 `enterprise_data_agent` 新生成的 credential。
- child intent 是 `doc_agent` 调用 `enterprise_data_agent` 的意图节点。
- audit decision 记录这次授权决策。

它们必须共享同一个 `trace_id/request_id`。

### Gateway 拦截了什么

这一步主要防止“拼接攻击”：

- 拿另一个 trace 的 credential 塞进当前请求。
- 拿另一个 trace 的 parent intent 塞进当前请求。
- 让 credential 和 intent 各自合法，但二者不属于同一跳调用。

Gateway 当前会拒绝跨 trace credential 和跨 trace parent intent。

### 为什么说明安全机制生效

你看到：

```text
shared_request_id: "..."
```

说明 child credential、child intent 和 audit log 是同一跳产生的，不是事后拼起来的。

这让审计时可以回答：

```text
某个 agent 凭什么调用了某个目标？
它当时声明的意图是什么？
Gateway 为什么允许或拒绝？
```

## 4. verify_a2a_identity

输出示例：

```text
[PASS] verify_a2a_identity
- missing_bearer: ...
- malformed_bearer: ...
- subject_mismatch: ...
- unknown_target: ...
```

这一步专门构造 A2A 身份认证恶意用例。

### missing_bearer

输出：

```json
{"error_code": "AUTH_TOKEN_MISSING"}
```

说明没有 Bearer token 的 A2A 请求被 Gateway 拒绝。

拦截点：

```text
Authorization header 缺失
```

安全意义：

```text
任何 agent-to-agent 请求都必须先证明调用方进程身份。
```

### malformed_bearer

输出：

```json
{"error_code": "AUTH_TOKEN_MALFORMED"}
```

说明伪造或格式错误的 token 被拒绝。

拦截点：

```text
JWT 结构无法解析，或 header/claims/signature 无法进入正常验签流程
```

安全意义：

```text
不能用随便拼出来的字符串冒充 Bearer token。
```

### subject_mismatch

输出：

```json
{"error_code": "AUTH_CREDENTIAL_SUBJECT_MISMATCH"}
```

说明 Bearer token 代表的 agent 和 envelope 中 credential 的 `subject_id` 不一致。

典型攻击是：

```text
external_search_agent 拿自己的 Bearer token，
但在 envelope 里塞 doc_agent 的 credential。
```

Gateway 会检查：

```text
Bearer token agent_id == current credential subject_id
```

安全意义：

```text
不能偷别的 agent 的 credential 来扩大自己的委托权限。
```

### unknown_target

输出：

```json
{"error_code": "AGENT_NOT_REGISTERED"}
```

说明目标 agent 不在 registry 中，Gateway 不会转发。

安全意义：

```text
A2A 调用只能发给已注册、active 的 agent。
```

## 5. verify_token_lifecycle

输出示例：

```text
[PASS] verify_token_lifecycle
- expired_new_request: ...
- cascade_revoke: ...
- running_cancel: ...
- natural_expiry_sleep: ...
```

这一步验证 token 过期和 token 吊销的区别。

## expired_new_request

输出：

```json
{"error_code": "AUTH_TOKEN_EXPIRED"}
```

脚本会签发一个短 TTL token，等它自然过期后，再尝试发起新 root task。

Gateway 拦截点：

```text
token exp <= now
```

安全意义：

```text
过期 token 不能再发起新请求、新委托或新工具访问。
```

注意：自然过期不是撤销，所以不会把 credential 写成 revoked。

## cascade_revoke

输出里会看到：

```json
"revoked_credentials": ["...", "..."],
"response": {
  "revoked": true,
  "trace_ids": ["..."],
  "cancelled_tasks": 0
}
```

脚本先跑一条正常链路，然后调用：

```text
POST /identity/tokens/{jti}/revoke
```

Gateway/identity 会：

- 标记 token revoked。
- 找到 token 绑定的 root credential。
- 递归 revoke descendant credentials。
- 找出受影响 trace。
- 尝试取消该 trace 上仍在运行的 task。

这里 `cancelled_tasks: 0` 是正常的，因为这条普通任务已经结束了，没有运行中的 task 可取消。

安全意义：

```text
撤销不是只撤一张 token，而是撤整条授权委托链。
```

## running_cancel

输出：

```json
"sleep_response": {
  "detail": {
    "error_code": "TASK_CANCELLED",
    "reason": "token_revoked"
  }
},
"revoke_response": {
  "cancelled_tasks": 1
}
```

脚本发起一个 enterprise `sleep` 任务，让它保持运行中，然后在任务未结束时 revoke root token。

Gateway 的运行时 task registry 会按 `trace_id` 找到正在运行的 asyncio task，并调用 cancel。

安全意义：

```text
如果是主动撤销，系统会立刻切断整条委托链，并打断链路上的运行中任务。
```

这对应你之前定的语义：

```text
撤销：强语义，立即撤销并取消运行中任务。
```

## natural_expiry_sleep

输出：

```json
"response": {
  "agent_id": "enterprise_data_agent",
  "task_type": "sleep",
  "result": {
    "slept_seconds": 2.5
  }
}
```

脚本签发一个短 TTL token，马上启动 sleep 任务。任务开始后 token 自然过期，但系统不会主动 cancel 已开始任务。

安全意义：

```text
自然过期只阻止新的安全动作，不打断已经开始执行的任务。
```

这对应你之前定的语义：

```text
过期：弱语义，只阻止新请求、新委托、新工具访问。
```

如果这个测试偶发失败，通常是因为 token TTL 太短，HTTP 请求还没进 Gateway 就已经过期。可以在 `.env` 里调大：

```text
BUIAM_SECURITY_NATURAL_EXPIRY_TOKEN_TTL_SECONDS=3
BUIAM_SECURITY_NATURAL_EXPIRY_SLEEP_SECONDS=4
```

## 为什么这些 PASS 有意义

这些检查覆盖了几个核心攻击面：

```text
1. 没 token 不能调用。
2. 伪造 token 不能调用。
3. token 主体和 credential 主体不一致不能调用。
4. 未注册 agent 不能作为目标。
5. credential 被篡改会破坏 hash/signature 校验。
6. intent 被篡改会破坏 node_id/signature 校验。
7. credential 和 intent 不能跨 trace 拼接。
8. 能力不能越过 caller token、user capabilities、target capabilities 的交集。
9. token 过期不能发起新动作。
10. token 吊销会级联撤销 credential tree。
11. token 吊销会取消相关 trace 的运行中任务。
12. 审计可以按 trace 还原 auth events、delegation chain、credential chain、intent tree 和 decision detail。
```

因此，`run_all_security_checks.py` 不是只在验证 demo 能跑，而是在验证 BuIAM 的关键安全不变量仍然成立。

## 如何定位某个节点

如果你想拿输出里的某个 `credential_id` 或 `intent_node_id` 反查它所在链路，可以运行：

```bash
python scripts/security/find_security_node.py --credential-id <credential_id>
python scripts/security/find_security_node.py --intent-node-id <intent_node_id>
```

它会打印：

- 节点所在 trace。
- 从 root 到当前节点的路径。
- 每个节点的签名验证结果。
- credential 是否 revoked。

## 常用参数

```bash
python scripts/security/run_all_security_checks.py --json
python scripts/security/run_all_security_checks.py --keep-db
python scripts/security/run_all_security_checks.py --trace-id <trace_id>
```

- `--json`：输出 JSON，便于验收或接 CI。
- `--keep-db`：不清空 `data/audit.db`，保留历史 trace。
- `--trace-id`：指定 trace id，方便复现某次实验。

