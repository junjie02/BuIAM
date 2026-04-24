# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python FastAPI MVP for a BuIAM delegation protocol. Core application code lives in `app/`:

- `app/main.py` defines API routes, root task handling, and startup wiring.
- `app/agents/` contains agent handlers and the agent registry.
- `app/delegation/` implements authorization, delegation-chain updates, and service/client logic.
- `app/identity/` stores mock token and authorization data.
- `app/store/` contains SQLite audit-log persistence.
- `app/tools/` and `app/llm/` provide mocked external tools and LLM adapters.

Tests are in `tests/`, demo scripts are in `scripts/`, and runtime/mock data belongs in `data/`. Keep new modules close to the feature they support.

## Build, Test, and Development Commands

Use a local virtual environment before running commands:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

- `uvicorn app.main:app --reload` starts the API locally with auto-reload.
- `python scripts/demo.py` runs the end-to-end delegation demo against the local service.
- `pytest` runs the test suite.

## Coding Style & Naming Conventions

Use Python 3 style with type hints, `from __future__ import annotations`, and Pydantic models for request/response contracts. Follow the existing four-space indentation and concise function names such as `delegate_call`, `agent_task`, and `build_mock_root_auth_context`. Use snake_case for files, functions, variables, and capability-related fields. Keep authorization logic centralized in `app/delegation/`; agents should not duplicate permission checks.

## Testing Guidelines

Tests use `pytest` and FastAPI `TestClient`. Place tests under `tests/` with names matching `test_*.py` and functions named `test_*`. Cover both allow and deny paths when changing delegation, capability intersection, audit logging, or token handling. Prefer focused API-level tests that assert response status, delegation chain contents, and `decision_detail` fields.

## Commit & Pull Request Guidelines

Git history uses short, direct commit messages, often Chinese-language summaries such as capability or permission updates. Keep commits focused on one behavior change. Pull requests should include a brief summary, affected modules, test results such as `pytest`, and screenshots or sample JSON only when API behavior or demo output changes. Link related issues or task notes when available.

## Security & Configuration Tips

The default LLM provider is mock and should remain reproducible without secrets. For real providers, set environment variables such as `LLM_PROVIDER=openai` and `OPENAI_API_KEY`; do not commit keys, generated databases, or virtual environments. Treat `app/identity/mock_store.py` as mock-only infrastructure unless replacing it with a real registry or token service.
