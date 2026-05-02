# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BuIAM is a Python FastAPI implementation of an Agent-to-Agent (A2A) security delegation protocol — an IAM system for AI agents. It demonstrates identity authentication, capability-based authorization, delegation credential chains, intent-chain validation, token lifecycle, and audit trailing. This is a competition entry for the "Passport for AI" challenge using Feishu (Lark) as the demo scenario.

## Build & Run Commands

Activate the venv before anything else:

```powershell
.venv\Scripts\activate
```

**Run the demo** (auto-starts Gateway + 3 agents):

```powershell
$env:BUIAM_AGENT_PROVIDER_MODE='mock'
$env:LLM_PROVIDER='mock'
$env:INTENT_GENERATOR_PROVIDER='mock'
$env:INTENT_JUDGE_PROVIDER='mock'
python scripts/demo.py
```

**Run tests:**

```powershell
$env:BUIAM_AGENT_PROVIDER_MODE='mock'
$env:LLM_PROVIDER='mock'
$env:INTENT_GENERATOR_PROVIDER='mock'
$env:INTENT_JUDGE_PROVIDER='mock'
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

Never let pytest scan `third_party/liboqs/tests` — always scope to `tests`.

**Run a single test file:**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_a2a_security_demo.py -q -p no:cacheprovider
```

**Run security verification scripts:**

```powershell
python scripts/security/run_all_security_checks.py
python scripts/security/verify_identity_vc.py --json
```

**Manual 4-terminal startup:**

```powershell
uvicorn app.main:app --port 8000
uvicorn examples.agent.doc_service:app --port 8011
uvicorn examples.agent.enterprise_data_service:app --port 8012
uvicorn examples.agent.external_search_service:app --port 8013
```

**Initialize liboqs submodule** (for ML-DSA support):

```powershell
git submodule update --init --recursive
```

## Architecture

### Service Layout

- **Gateway** (`app/main.py`, port 8000): Central security service. All A2A calls flow through here.
- **doc_agent** (`examples/agent/doc_service.py`, port 8011): Coordinates report generation, delegates to enterprise_data_agent.
- **enterprise_data_agent** (`examples/agent/enterprise_data_service.py`, port 8012): Only agent with enterprise data access capabilities.
- **external_search_agent** (`examples/agent/external_search_service.py`, port 8013): Only has `web.public:read`, cannot access enterprise data.

### Request Flow (Normal Chain)

```
User (Bearer Token)
  → POST /a2a/root-tasks  → Gateway authenticates token, generates root intent + root credential
    → POST doc_agent:/a2a/tasks  → doc_agent delegates to enterprise_data_agent
      → POST /a2a/agents/enterprise_data_agent/tasks  → Gateway checks delegation credential, capability intersection, intent chain
        → POST enterprise_data_agent:/a2a/tasks  → returns enterprise data
```

### Core Security Modules (in `app/`)

| Module | Responsibility |
|--------|---------------|
| `identity/` | JWT issue/verify/introspect/revoke, RSA/ML-DSA key management, DID documents |
| `delegation/` | Capability intersection (`caller_token ∩ target_agent ∩ requested ∩ user`), signed DelegationCredential with hash-chain IDs, credential validation (recursive parent check) |
| `intent/` | Intent generation (LLM), intent drift judging, signed intent nodes, intent tree validation |
| `gateway/routes.py` | Two A2A entrypoints orchestrating the full auth→delegation→intent→forward pipeline |
| `store/` | SQLite persistence: agents, tokens, delegation_credentials, audit_logs, delegation_chain, auth_events, intent_tree, did_documents |
| `runtime/tasks.py` | In-process asyncio task registry for cancellation on token revocation |
| `protocol.py` | All Pydantic models: `AuthContext`, `DelegationCredential`, `DelegationEnvelope`, `IntentNode`, `DelegationDecision`, etc. |

### Capability Model

Seven capabilities defined as a `Literal` type in `protocol.py`: `report:write`, `feishu.doc:write`, `feishu.contact:read`, `feishu.calendar:read`, `feishu.wiki:read`, `feishu.bitable:read`, `web.public:read`.

Authorization computes `caller_token_caps ∩ target_agent_caps ∩ requested_caps ∩ user_caps`. Missing capabilities are broken down by source (`missing_by`).

### Delegation Credential Chain

Every authorization hop creates a signed `DelegationCredential` with:
- `credential_id` = hash-chain node ID, recomputable from parent ID + canonical content
- Recursive validation: parent must exist, not be revoked/expired, child caps ≤ parent caps, child exp ≤ parent exp
- Stored in SQLite; `delegation_chain` table is human-readable audit context only (not the security source)

### Intent Chain

Root tasks and A2A calls produce signed `IntentNode` entries. The judge (LLM) checks child intent against root/parent for drift. Intent nodes are linked by `node_id` → `parent_node_id` and validated for hash integrity, signature, trace continuity, and cycle detection.

### Provider Modes

Controlled by env vars: `BUIAM_AGENT_PROVIDER_MODE` (mock/lark_cli), `LLM_PROVIDER` (mock/openai/anthropic), `INTENT_GENERATOR_PROVIDER`, `INTENT_JUDGE_PROVIDER`.

- **mock**: Deterministic local data — always use for tests and baseline demos.
- **lark_cli**: Calls real `lark-cli` binary to access Feishu APIs. Requires separate `lark-cli` install and auth.
- **openai/anthropic**: Real LLM providers for intent generation/judging.

### Key Env Vars (see `.env.example` for full list)

- `BUIAM_GATEWAY_URL` — Gateway address for SDK/scripts
- `BUIAM_DB_PATH` — SQLite path (default `data/audit.db`)
- `BUIAM_KEY_DIR` — RSA/ML-DSA key storage (default `data/keys`)
- `BUIAM_USE_MLDSA` + `BUIAM_AUTH_SIGNATURE_ALG` — switch from RSA to ML-DSA post-quantum
- `DOC_AGENT_ENDPOINT`, `ENTERPRISE_DATA_AGENT_ENDPOINT`, `EXTERNAL_SEARCH_AGENT_ENDPOINT` — agent service URLs

## Important Conventions

- **Do not reintroduce** removed legacy paths: `local://`, `/delegate/call`, `app/gateway/local_adapter.py`, old `example/`, old `examples/agents/`, or local import-based downstream agent calls.
- Security logic stays in its module: auth/token → `identity/`, delegation → `delegation/`, intent → `intent/`, gateway orchestration → `gateway/`.
- Agents should not duplicate permission checks or directly import/call downstream agent handlers.
- Use Pydantic models from `app/protocol.py` across module boundaries, not ad hoc dicts.
- Type hints and `from __future__ import annotations` throughout.
- Snake_case for files, functions, variables, and capability fields.
- Tests use `pytest` + `httpx` against real local services; no mocking of the Gateway security chain.
- Windows PowerShell syntax for all commands; `\` path separators in env var examples.
- Never commit: `.env`, real API keys, `data/` databases, generated keypairs, `.venv`, `third_party/liboqs/build`, `third_party/liboqs/install`.
