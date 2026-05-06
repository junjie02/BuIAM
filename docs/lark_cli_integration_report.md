# lark-cli Integration Report

Date: 2026-04-27

## Scope

This change integrates `lark-cli` into the demo Agent provider layer without
changing Gateway-side authentication, delegation credentials, intent-chain
validation, revocation, or audit behavior.

## Modified Areas

- Added [examples/agent/provider.py](examples/agent/provider.py) as the Feishu provider facade
- Added [examples/agent/lark_cli_provider.py](examples/agent/lark_cli_provider.py) as the `lark-cli` adapter
- Updated [examples/agent/enterprise_data_agent.py](examples/agent/enterprise_data_agent.py) to read enterprise data through the Feishu provider
- Updated [examples/agent/doc_agent.py](examples/agent/doc_agent.py) to create documents through the configurable provider and propagate downstream provider failures
- Added [tests/test_agent_providers.py](tests/test_agent_providers.py) for normalization and provider error coverage
- Updated [.env.example](.env.example) and [README.md](README.md) with configuration and usage notes

## Behavior Changes

- Demo Agent business data uses the lark-cli provider directly.
- `enterprise_data_agent` reads enterprise data through local `lark-cli` calls.
- `doc_agent` creates real Feishu documents via `lark-cli`.

## Configuration

Required:

- `BUIAM_LARK_CLI_BIN`
- `BUIAM_LARK_CLI_AS`

Recommended:

- `BUIAM_LARK_CLI_BITABLE_APP_TOKEN`
- `BUIAM_LARK_CLI_BITABLE_TABLE_ID`
- `BUIAM_LARK_CLI_DOC_FOLDER_TOKEN`

## Error Handling

- Missing `lark-cli` executable returns `LARK_CLI_NOT_FOUND`
- CLI timeout returns `LARK_CLI_TIMEOUT`
- CLI non-zero exit returns `LARK_CLI_FAILED`
- Missing bitable identifiers does not break the full enterprise snapshot; the
  agent returns a provider warning and an empty `bitable_records` list

## Verification

Targeted tests were added for:

- `lark-cli` enterprise snapshot normalization
- `lark-cli` document creation normalization
- downstream provider error propagation in `doc_agent`
