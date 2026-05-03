# BuIAM 系统架构总览

## 概述

BuIAM 是一个基于 FastAPI 的 Agent-to-Agent (A2A) 安全委托协议实现，为 AI Agent 提供身份认证、能力授权、委托链验证、意图审计等 IAM 能力。

## 服务拓扑

```
User (Bearer Token)
    │
    ▼
┌──────────────────────────────────────┐
│           Gateway (port 8000)        │
│  ┌─────────────────────────────────┐ │
│  │ POST /a2a/root-tasks            │ │ ← 用户入口
│  │ POST /a2a/agents/{id}/tasks     │ │ ← Agent 间调用入口
│  │ POST /identity/tokens           │ │ ← Token 签发
│  │ GET  /audit/traces/{trace_id}   │ │ ← 审计查询
│  └─────────────────────────────────┘ │
└──────────┬───────────────────────────┘
           │ A2A Forward
    ┌──────┼──────┐
    ▼      ▼      ▼
┌───────┐ ┌──────────┐ ┌───────────────┐
│doc    │ │enterprise│ │external_search│
│agent  │ │data_agent│ │_agent         │
│:8011  │ │:8012     │ │:8013          │
└───────┘ └──────────┘ └───────────────┘
```

## 请求流程（正常委托链）

```
1. User → POST /a2a/root-tasks (Bearer Token)
2. Gateway 验证 Token → 生成 Root Intent + Root Credential
3. Gateway 构建子委托凭证 (user→doc_agent)
4. Gateway → Forward POST doc_agent:/a2a/tasks
5. doc_agent 调用 A2AClient → POST /a2a/agents/enterprise_data_agent/tasks
6. Gateway 验证委托凭证 → 能力交集 → 意图链校验
7. Gateway → Forward POST enterprise_data_agent:/a2a/tasks
8. enterprise_data_agent 返回结果 → 逐层回传
```

## 模块架构

```
app/
├── gateway/routes.py       # A2A 入口 (root-tasks, agent-tasks)
├── delegation/              # 委托凭证 + 能力授权
│   ├── credential_crypto.py # VC 构建/签名/验证
│   ├── service.py           # 授权决策 + 链管理
│   └── capabilities.py      # 能力交集 + 验证
├── intent/                  # 意图生成 + 校验
│   ├── crypto.py            # 意图节点签名
│   ├── generator.py         # LLM 意图生成
│   ├── judge.py             # 意图漂移检测
│   └── service.py           # 意图链验证
├── identity/                # 身份 + Token + DID
│   ├── jwt_service.py       # JWT 签发/验证/自省
│   ├── keys.py              # RSA/ML-DSA 密钥管理
│   ├── did.py               # DID 文档构建
│   ├── did_proof.py         # DID proof 创建/验证
│   ├── did_resolver.py      # DID 解析
│   ├── crypto.py            # 底层签名/验证
│   └── routes.py            # /identity/* 端点
├── store/                   # SQLite 持久化
│   ├── schema.py            # 数据库表定义
│   ├── registry.py          # Agent 注册
│   ├── did_registry.py      # DID 文档存储
│   ├── tokens.py            # Token 存储
│   ├── delegation_credentials.py # 凭证存储
│   ├── intent_tree.py       # 意图节点存储
│   ├── audit.py             # 审计日志
│   └── auth_events.py       # 认证事件
├── sdk/client.py            # A2A 客户端 SDK
├── runtime/tasks.py         # 运行中任务注册 (撤销取消)
└── protocol.py              # Pydantic 模型
```

## 安全层次

每一跳 A2A 调用经过的安全检查层次：

| 层次 | 检查内容 | 失败处理 |
|------|---------|---------|
| 1. Token 验证 | JWT 签名、过期、吊销、KID 绑定 | FATAL (401) |
| 2. 身份校验 | actor_type、credential subject 匹配 | FATAL (403) |
| 3. 凭证验证 | 完整性哈希、签名、链回溯 | FATAL (403) |
| 4. 能力授权 | 四路交集 (token ∩ target ∩ request ∩ user) | DENIED (403, recoverable=true) |
| 5. 意图验证 | 意图节点签名、漂移检测、链一致性 | FATAL (403) |
| 6. 审计记录 | 所有决策入 audit log | 不阻塞 |
