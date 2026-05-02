# BuIAM A2A Security Delegation Demo

BuIAM is a FastAPI implementation of an Agent-to-Agent security delegation protocol. The demo business actions can run with deterministic mock providers, while the Gateway-side security logic is real: DID documents, signed VC-shaped delegation credentials, intent-chain validation, token revocation/expiration, running task cancellation, and audit tracing.

## What Is Implemented

- Formal Gateway entrypoints: `POST /a2a/root-tasks` and `POST /a2a/agents/{target_agent_id}/tasks`.
- Signed `DelegationCredential` records shaped like Verifiable Credentials.
- Recomputable credential hash-chain node IDs.
- DID-based verification methods for RSA and optional ML-DSA signatures.
- Signed intent nodes and parent/root continuity validation.
- Bearer token issue, introspection, revocation, expiration, and credential binding.
- Audit traces that combine logs, auth events, delegation credentials, and intent tree records.
- Demo Agent provider modes:
  - `mock`: deterministic local data, recommended for tests and baseline demos.
  - `lark_cli`: optional real Feishu reads/writes through local `lark-cli`.

## Repository Layout

```text
app/                  Gateway, identity, delegation, intent, registry, store
examples/agent/       Demo Agent services and provider layer
scripts/              Demo/bootstrap helpers
scripts/security/     Manual security verification scripts
tests/                Pytest regression tests
third_party/liboqs    liboqs submodule for optional ML-DSA runtime
data/                 Local runtime DB/key material, not for commits
```

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Initialize the third-party submodule after cloning or pulling:

```powershell
git submodule update --init --recursive
```

If `third_party/liboqs` is empty, the submodule was not initialized.

## Build liboqs For ML-DSA On Windows

The project can use ML-DSA through `liboqs-python`, but the native liboqs runtime must be available. The code automatically checks:

```text
third_party/liboqs/install
```

Recommended Windows toolchain:

- Visual Studio Build Tools 2022
- Workload: `Desktop development with C++`
- Components: MSVC v143 and Windows 10/11 SDK
- CMake installed and available on `PATH`

Open `x64 Native Tools Command Prompt for VS 2022`, then run:

```bat
cd /d F:\AAA飞书挑战赛\Code\third_party\liboqs
rmdir /s /q build

cmake -S . -B build -DCMAKE_INSTALL_PREFIX="%CD%\install" -DBUILD_SHARED_LIBS=ON
cmake --build build --config Release
cmake --install build --config Release
```

Expected installed files include:

```text
third_party/liboqs/install/bin/oqs.dll
third_party/liboqs/install/lib/oqs.lib
third_party/liboqs/install/include/oqs/sig_ml_dsa.h
```

Install the Python binding:

```powershell
cd F:\AAA飞书挑战赛\Code
.venv\Scripts\activate
python -m pip install liboqs-python
```

Verify ML-DSA:

```powershell
python -c "from app.identity.keys import ensure_agent_mldsa_keypair; ensure_agent_mldsa_keypair('test-agent'); print('ML-DSA ok')"
```

To enable ML-DSA signatures instead of the default RSA path:

```powershell
$env:BUIAM_USE_MLDSA='true'
$env:BUIAM_AUTH_SIGNATURE_ALG='BUIAM-MLDSA-65'
```

## Run The Demo

Mock mode is the stable local baseline:

```powershell
$env:BUIAM_AGENT_PROVIDER_MODE='mock'
$env:LLM_PROVIDER='mock'
$env:INTENT_GENERATOR_PROVIDER='mock'
$env:INTENT_JUDGE_PROVIDER='mock'
python scripts/demo.py
```

The demo starts the Gateway and three demo Agents if they are not already running.

Manual startup uses four terminals:

```powershell
uvicorn app.main:app --port 8000
uvicorn examples.agent.doc_service:app --port 8011
uvicorn examples.agent.enterprise_data_service:app --port 8012
uvicorn examples.agent.external_search_service:app --port 8013
```

## Test

Run only this repository's tests. Do not let pytest collect `third_party/liboqs/tests`.

```powershell
$env:BUIAM_AGENT_PROVIDER_MODE='mock'
$env:LLM_PROVIDER='mock'
$env:INTENT_GENERATOR_PROVIDER='mock'
$env:INTENT_JUDGE_PROVIDER='mock'
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

Current baseline verified locally:

```text
42 passed
```

## Security Verification Scripts

```powershell
python scripts/security/verify_delegation_chain.py
python scripts/security/verify_intent_chain.py
python scripts/security/verify_chain_binding.py
python scripts/security/verify_token_lifecycle.py
python scripts/security/verify_a2a_identity.py
python scripts/security/verify_identity_vc.py --json
python scripts/security/run_all_security_checks.py
```

`verify_identity_vc.py --json` prints:

- the verification flow,
- registered DID Documents,
- decoded token header and claims,
- token introspection result,
- root VC-shaped credential,
- delegated Agent VC-shaped credential,
- recomputed `content_hash`,
- recomputed `credential_id`,
- proof signature verification result.

## lark-cli Provider Mode

The Gateway security chain is unchanged when using real Feishu data. Only the demo Agent provider layer changes.

Enable real Feishu access:

```powershell
$env:BUIAM_AGENT_PROVIDER_MODE='lark_cli'
$env:BUIAM_LARK_CLI_BIN='lark-cli'
$env:BUIAM_LARK_CLI_AS='user'
$env:BUIAM_LARK_CLI_BITABLE_APP_TOKEN='app_token'
$env:BUIAM_LARK_CLI_BITABLE_TABLE_ID='tbl_id'
```

Install and authenticate `lark-cli` separately:

```powershell
npm install -g @larksuiteoapi/cli
lark-cli auth login --recommend
```

Use `mock` mode for deterministic tests. Use `lark_cli` only when you intentionally want local Feishu API calls.

## Commit Notes

Do not commit:

- `.env`
- real API keys
- generated databases under `data/`
- generated keypairs
- `.venv`
- `third_party/liboqs/build`
- `third_party/liboqs/install`

The correct way to share liboqs is the submodule metadata plus submodule pointer:

```powershell
git add .gitmodules third_party/liboqs
git commit -m "Fix liboqs submodule metadata"
git push
```
