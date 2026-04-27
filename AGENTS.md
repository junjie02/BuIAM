# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python FastAPI implementation of the BuIAM A2A security delegation protocol. It is no longer the old MVP shape: the business Agent actions are still mock demo providers, but authentication, signed delegation credentials, intent chain validation, token revocation/expiration, task cancellation, and audit tracing are real Gateway-side security logic.

Core application code lives in `app/`:

- `app/main.py` wires FastAPI startup, routes, health checks, and audit query endpoints.
- `app/gateway/routes.py` contains the formal A2A Gateway entrypoints:
  - `POST /a2a/root-tasks`
  - `POST /a2a/agents/{target_agent_id}/tasks`
- `app/delegation/` implements capability intersection, signed delegation credentials, credential validation, delegation authorization, and hop creation.
- `app/intent/` implements intent generation, intent judge integration, signed intent nodes, and intent-chain validation.
- `app/identity/` implements development RSA keys, shared crypto helpers, JWT issue/verify, token introspection, and token revocation.
- `app/registry/` and `app/store/registry.py` register and query active A2A agents.
- `app/store/` contains SQLite persistence for agents, tokens, audit logs, auth events, human-readable delegation chain, signed delegation credentials, and intent tree.
- `app/runtime/tasks.py` keeps the current single-process asyncio task registry used to cancel running trace tasks on token revocation.
- `app/sdk/client.py` is the A2A client used by agents to call the Gateway.

Demo Agent code lives in `examples/agent/`:

- `doc_agent.py` coordinates report generation and delegates enterprise data reads through A2A.
- `enterprise_data_agent.py` returns mock enterprise data and exposes a cancellable `sleep` task for revocation tests.
- `external_search_agent.py` returns mock public search data and demonstrates denied enterprise escalation.
- `demo_provider.py` contains mock business action providers. Replace this provider layer when integrating real Feishu APIs; do not bypass the Gateway security chain.
- `*_service.py` files expose each Agent as an independent FastAPI service with `/a2a/tasks`.

Tests are in `tests/`; deeper security regression tests are in `tests/security/`. Demo and validation scripts are in `scripts/` and `scripts/security/`. Runtime local data belongs in `data/`.

Do not reintroduce removed legacy paths such as `local://`, `/delegate/call`, `app/gateway/local_adapter.py`, old `example/`, old `examples/agents/`, or local import-based downstream agent calls.

## Build, Test, And Development Commands

Use a local virtual environment before running commands:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Common commands:

- `python scripts/demo.py` runs the end-to-end A2A demo. It starts Gateway and the three demo Agents automatically if they are not already running.
- `python scripts/bootstrap_demo_agents.py` registers demo Agent metadata and ensures development keypairs.
- `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` runs the full automated test suite.
- `python scripts/security/run_all_security_checks.py` runs manual security verification scripts.
- `python scripts/security/find_security_node.py --credential-id <id>` traces a credential node back to root.
- `python scripts/security/find_security_node.py --intent-node-id <id>` traces an intent node back to root.

Manual service startup uses four terminals:

```bash
uvicorn app.main:app --port 8000
uvicorn examples.agent.doc_service:app --port 8011
uvicorn examples.agent.enterprise_data_service:app --port 8012
uvicorn examples.agent.external_search_service:app --port 8013
```

## Coding Style & Naming Conventions

Use Python 3 style with type hints, `from __future__ import annotations`, and Pydantic models for request/response contracts. Follow four-space indentation and snake_case for files, functions, variables, and capability-related fields.

Keep security logic centralized:

- Authentication and token lifecycle belong in `app/identity/`.
- Delegation authorization and credential-chain checks belong in `app/delegation/`.
- Intent-chain checks belong in `app/intent/`.
- Gateway request interception, auth event recording, audit decisions, and forwarding belong in `app/gateway/`.
- Agents should not duplicate permission checks and should not directly import or call downstream Agent handlers.

Prefer explicit, structured models from `app/protocol.py` over ad hoc dictionaries when crossing module boundaries. Keep comments short and only where they clarify non-obvious security behavior.

## Security Invariants

Do not weaken or remove these invariants:

- Every A2A authorization hop must create a signed `DelegationCredential`.
- `credential_id` is the hash-chain node ID and must be recomputable from parent ID plus canonical credential content.
- Credential signatures must verify with the issuer public key.
- Child capabilities and user capabilities must not exceed the parent credential.
- Child expiration must not exceed parent expiration.
- `delegation_chain` is human-readable audit context only; it is not the security fact source.
- Root task and agent-to-agent calls must create or validate signed intent nodes.
- Intent node IDs and content hashes must be recomputable.
- Intent signatures must verify with the actor public key.
- Intent parent/root continuity must stay within the same trace.
- Bearer token identity must match the current credential subject for A2A calls.
- Token revocation must cascade to root credential descendants and cancel affected running trace tasks.
- Natural expiration blocks new requests, new delegation, and new tool access, but must not actively cancel already-started tasks.
- Failed auth/delegation/intent checks should produce deny audit records and auth events where applicable.

If a test reveals a missing security check, fix the implementation or add a failing regression test that documents the gap. Do not reduce validation strictness just to make tests pass.

## Testing Guidelines

Tests use `pytest`, `httpx`, and real local FastAPI/uvicorn services. Place tests under `tests/` with names matching `test_*.py` and functions named `test_*`.

Security-sensitive changes should cover both allow and deny paths. Relevant cases include:

- normal credential and intent chain construction,
- capability narrowing and `missing_by`,
- tampered credential fields,
- tampered intent fields,
- cross-trace credential or intent reuse,
- missing/malformed Bearer token,
- Bearer/credential subject mismatch,
- unknown or inactive target Agent,
- token expiration,
- token revocation cascade,
- running `sleep` task cancellation,
- audit trace completeness and non-repudiation.

Manual security script behavior is documented in:

```text
scripts/security/SECURITY_CHECKS_EXPLAINED.md
```

## Configuration

Use `.env.example` as the source of truth for supported local environment variables. Important variables include:

- `BUIAM_GATEWAY_URL`
- `BUIAM_DEMO_USER_ID`
- `BUIAM_DEMO_KEEP_SERVERS`
- `BUIAM_DB_PATH`
- `BUIAM_KEY_DIR`
- `DOC_AGENT_ENDPOINT`
- `ENTERPRISE_DATA_AGENT_ENDPOINT`
- `EXTERNAL_SEARCH_AGENT_ENDPOINT`
- `A2A_FORWARD_TIMEOUT_SECONDS`
- `A2A_AGENT_TOKEN_TTL_SECONDS`
- `LLM_PROVIDER`
- `INTENT_GENERATOR_PROVIDER`
- `INTENT_JUDGE_PROVIDER`
- `OPENAI_*`
- `ANTHROPIC_*`
- `BUIAM_SECURITY_*`

Do not commit real API keys, generated databases, generated keypairs, runtime caches, or virtual environments. The repository should remain runnable with mock providers and no external secrets.

## Commit & Pull Request Guidelines

Keep commits focused on one behavior change. Commit messages may be short and direct, including Chinese-language summaries. Pull requests should include:

- brief summary,
- affected modules,
- security behavior changes,
- test results such as `pytest` and `run_all_security_checks.py`,
- sample JSON only when API behavior changes.

