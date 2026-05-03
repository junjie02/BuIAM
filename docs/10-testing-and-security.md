# 测试架构与安全验证

## 测试结构

```
tests/
├── conftest.py                  # session 级 fixture: 启动 4 个服务 + 重置 DB
├── security_helpers.py          # 共享辅助函数 (token、envelope、trace 等)
├── test_a2a_security_demo.py    # 端到端集成 (6 tests)
├── test_agent_providers.py      # Provider 模式验证 (5 tests)
└── security/                    # 安全专项测试
    ├── test_delegation_chain.py      # 委托凭证链 (6 tests)
    ├── test_intent_chain.py           # 意图链 (3 tests, 参数化)
    ├── test_chain_binding.py          # 链绑定 (2 tests)
    ├── test_a2a_identity.py           # A2A 身份认证 (6 tests)
    ├── test_token_lifecycle.py        # Token 生命周期 (4 tests)
    ├── test_audit_non_repudiation.py  # 审计不可抵赖 (3 tests)
    ├── test_did_registration.py       # DID 注册 (10 tests)
    └── test_failure_handling.py       # 失败处理分级 (9 tests)
```

## 运行测试

```powershell
# 完整回归
$env:BUIAM_AGENT_PROVIDER_MODE='mock'
$env:LLM_PROVIDER='mock'
$env:INTENT_GENERATOR_PROVIDER='mock'
$env:INTENT_JUDGE_PROVIDER='mock'
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider

# 仅安全测试
.venv\Scripts\python.exe -m pytest tests/security/ -v -p no:cacheprovider

# 运行安全检查编排器
python scripts/security/run_all_security_checks.py

# 单文件
.venv\Scripts\python.exe -m pytest tests/security/test_delegation_chain.py -v -p no:cacheprovider
```

## 安全测试分类

| 安全域 | 测试文件 | 覆盖范围 |
|--------|---------|---------|
| 委托凭证 | `test_delegation_chain.py` | 正常链、7 种篡改字段、跨 trace、能力拒绝/建议 |
| 意图链 | `test_intent_chain.py` | 正常链、5 种篡改、意图漂移 |
| 链绑定 | `test_chain_binding.py` | trace/request 绑定、跨 trace 拒绝 |
| A2A 身份 | `test_a2a_identity.py` | Bearer 缺失/畸形/不匹配、actor type、unknown agent |
| Token 生命周期 | `test_token_lifecycle.py` | 过期/吊销/级联/任务取消 |
| 审计 | `test_audit_non_repudiation.py` | 签名证明、trace 完整性、deny 记录 |
| DID 注册 | `test_did_registration.py` | 注册验证、JWK 格式、proof 验证、DID→Token 联动 |
| 失败处理 | `test_failure_handling.py` | FATAL/recoverable 分类、suggested_agents、审计缺口 |

## 运维工具 (scripts/security/)

| 工具 | 用途 |
|------|------|
| `verify_identity_vc.py --json` | DID+Token+VC 端到端验证，输出 VC JSON |
| `find_security_node.py` | 按 credential_id 或 intent_node_id 溯源 |
| `run_all_security_checks.py` | pytest 包装器，运行全部安全测试 |

## 覆盖缺口（待补充）

- ML-DSA 签名路径 (所有测试锁定 RSA)
- Token introspection 独立测试
- `find_agents_with_capabilities` 单元测试
