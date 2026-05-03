# lark-cli Integration Report

Date: 2026-04-27

## Scope

This change integrates `lark-cli` into the demo Agent provider layer without
changing Gateway-side authentication, delegation credentials, intent-chain
validation, revocation, or audit behavior.

## Modified Areas

- Added [examples/agent/provider.py](examples/agent/provider.py) as the provider selection layer
- Added [examples/agent/lark_cli_provider.py](examples/agent/lark_cli_provider.py) as the `lark-cli` adapter
- Updated [examples/agent/enterprise_data_agent.py](examples/agent/enterprise_data_agent.py) to read enterprise data through the configurable provider
- Updated [examples/agent/doc_agent.py](examples/agent/doc_agent.py) to create documents through the configurable provider and propagate downstream provider failures
- Added [tests/test_agent_providers.py](tests/test_agent_providers.py) for provider selection and normalization coverage
- Updated [.env.example](.env.example) and [README.md](README.md) with configuration and usage notes

## Behavior Changes

- Default mode remains `mock`, so the existing demo and security tests keep the
  same deterministic behavior.
- Setting `BUIAM_AGENT_PROVIDER_MODE=lark_cli` switches:
  - `enterprise_data_agent` from mock enterprise data to local `lark-cli` calls
  - `doc_agent` from mock document output to real Feishu doc creation via `lark-cli`
- `external_search_agent` remains mock by design.

## Configuration

Required for `lark_cli` mode:

- `BUIAM_AGENT_PROVIDER_MODE=lark_cli`
- `BUIAM_LARK_CLI_BIN`
- `BUIAM_LARK_CLI_AS`

Recommended:

- `BUIAM_LARK_CLI_BITABLE_APP_TOKEN`
- `BUIAM_LARK_CLI_BITABLE_TABLE_ID`
- `BUIAM_LARK_CLI_DOC_FOLDER_TOKEN`

## Error Handling

- Invalid provider mode returns `PROVIDER_MODE_INVALID`
- Missing `lark-cli` executable returns `LARK_CLI_NOT_FOUND`
- CLI timeout returns `LARK_CLI_TIMEOUT`
- CLI non-zero exit returns `LARK_CLI_FAILED`
- Missing bitable identifiers does not break the full enterprise snapshot; the
  agent returns a provider warning and an empty `bitable_records` list

## Verification

Targeted tests were added for:

- mock mode fallback
- invalid mode rejection
- `lark-cli` enterprise snapshot normalization
- `lark-cli` document creation normalization
- downstream provider error propagation in `doc_agent`
