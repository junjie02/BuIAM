# BuIAM A2A 安全委托 Demo

BuIAM 是一个基于 FastAPI 的 Agent-to-Agent 安全委托演示系统。当前三个业务 Agent 的执行动作仍然使用可控的 mock provider，因此不需要真实飞书 API 配置也能跑通；但安全链路是真实实现：JWT 验证、签名化委托凭证、意图链校验、Token 吊销、过期处理、运行中任务取消和审计追踪都在 Gateway 中完成。

## 安全约束

- 每一跳 A2A 调用都会生成 signed delegation credential，包含父子关系、root 引用、能力收缩、过期时间、哈希和签名。
- root task 和 agent-to-agent 调用都会生成并校验 intent node，保留 intent parent/root 关系。
- Token 吊销会级联撤销 descendant credentials，并取消受影响 trace 的进程内运行任务。
- Token/credential 自然过期只阻止新请求、新委托和新工具访问，不主动取消已经开始的任务。
- `delegation_chain` 只做人类可读审计摘要；真正的授权事实来源是 signed credential 链。
- `/audit/traces/{trace_id}` 可以查询 logs、delegation chain、delegation credentials、auth events、intent tree 和 decision detail。

## 运行结构

```text
Gateway:               http://127.0.0.1:8000
doc_agent:             http://127.0.0.1:8011/a2a/tasks
enterprise_data_agent: http://127.0.0.1:8012/a2a/tasks
external_search_agent: http://127.0.0.1:8013/a2a/tasks
```

用户发起任务：

```text
POST /a2a/root-tasks
```

Agent 之间发送普通 A2A task 请求，统一由 Gateway 拦截、鉴权、授权、生成下一跳 credential/intent，并转发：

```text
POST /a2a/agents/{target_agent_id}/tasks
```

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts/demo.py
```

`scripts/demo.py` 会在本机端口没有服务时自动启动 Gateway 和三个 demo Agent，然后执行：

- 正常链路：`user -> doc_agent -> enterprise_data_agent`
- 越权链路：`user -> external_search_agent -> enterprise_data_agent`
- 两条 trace 的审计摘要

如果希望 demo 结束后保留服务进程：

```bash
set BUIAM_DEMO_KEEP_SERVERS=1
python scripts/demo.py
```

## 手动启动

也可以打开四个终端分别启动：

```bash
uvicorn app.main:app --port 8000
uvicorn examples.agent.doc_service:app --port 8011
uvicorn examples.agent.enterprise_data_service:app --port 8012
uvicorn examples.agent.external_search_service:app --port 8013
```

Gateway 启动时会自动注册三个 demo Agent。也可以手动执行：

```bash
python scripts/bootstrap_demo_agents.py
```

## Demo Agent

- `doc_agent`：编排报告生成，委托企业数据读取，并写入 mock 飞书文档。
- `enterprise_data_agent`：返回 mock 通讯录、日历、知识库信号和多维表格记录。
- `external_search_agent`：返回 mock 公网搜索结果，并用于演示越权读取企业数据被拒绝。

mock 行为集中在 `examples/agent/demo_provider.py`。后续接入真实飞书 OpenAPI 时，优先替换 provider 层，不需要改 Gateway 的安全链路。

## 常用 API

- `GET /health`
- `GET /registry/agents`
- `POST /identity/tokens`
- `POST /identity/tokens/{jti}/revoke`
- `POST /a2a/root-tasks`
- `POST /a2a/agents/{target_agent_id}/tasks`
- `GET /audit/logs`
- `GET /audit/auth-events`
- `GET /audit/traces/{trace_id}`

## 自动化测试

```bash
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

测试覆盖：

- 正常委托链、意图链和审计落库。
- 越权委托、能力收缩和 `missing_by`。
- credential 篡改、跨 trace credential 复用、parent/root 连续性。
- intent 篡改、签名错误、parent 丢失、actor mismatch、intent drift、跨 trace parent intent。
- Token 签发、过期、吊销、级联撤销和运行中 sleep task 取消。
- A2A Bearer 缺失、伪造、主体不一致、user token 冒充 agent、未知 target agent。
- 不可抵赖：credential issuer 签名和 intent actor 签名可用对应公钥验证。

## 人工安全验证脚本

脚本位于 `scripts/security/`，默认会启动或复用本机 Gateway 与三个 Agent。业务动作仍然是 mock，安全校验是真实链路。

```bash
python scripts/security/verify_delegation_chain.py
python scripts/security/verify_intent_chain.py
python scripts/security/find_security_node.py
python scripts/security/verify_chain_binding.py
python scripts/security/verify_token_lifecycle.py
python scripts/security/verify_a2a_identity.py
python scripts/security/run_all_security_checks.py
```

通用参数：

- `--keep-db`：保留 `data/audit.db`，不在脚本开始时清空。
- `--trace-id <id>`：指定 trace id，便于复现实验。
- `--json`：输出 JSON，方便验收或接入 CI。

推荐一键验证：

```bash
python scripts/security/run_all_security_checks.py
```
