# 当前版本相对 GitHub 主分支的更新报告

生成日期：2026-04-27

## 对比基线

- 当前工作分支：`main`
- 本地缓存主分支：`origin/main`
- 基线提交：`ba89f552413eafa50d4310e967c683efa90a0688`
- 远端刷新状态：尝试执行 `git fetch origin main`，但 GitHub 连接被重置，错误为 `Recv failure: Connection was reset`。因此本报告基于本地已缓存的 `origin/main` 与当前工作区差异生成。

## 总览

本次更新把真实飞书访问能力接入 demo Agent 的 provider 层，同时保持 Gateway 侧认证、授权委托凭证、意图链校验、审计和越权拦截逻辑不变。

默认模式仍为 `mock`，现有 demo 和安全测试可以继续使用确定性的 mock 数据。设置 `BUIAM_AGENT_PROVIDER_MODE=lark_cli` 后：

- `enterprise_data_agent` 会通过本地 `lark-cli` 读取真实飞书企业数据。
- `doc_agent` 会通过本地 `lark-cli` 创建真实飞书文档。
- `external_search_agent` 仍保持 mock 公共搜索 provider。

## 文件变更

已跟踪文件变更：

| 文件 | 变更概述 |
| --- | --- |
| `.env.example` | 新增 `lark-cli` provider 相关配置项，包括 provider 模式、CLI 路径、身份类型、超时、联系人/wiki/bitable/doc 配置。 |
| `README.md` | 新增 `lark-cli Provider Integration` 使用说明，解释开启真实飞书 provider 的方式和当前支持范围。 |
| `examples/agent/doc_agent.py` | 从固定 mock 文档写入改为调用可配置 provider；增加下游 provider 错误透传。 |
| `examples/agent/enterprise_data_agent.py` | 从固定 mock 企业数据改为调用可配置 provider；增加 provider 错误处理。 |

新增源码、测试和文档：

| 文件 | 作用 |
| --- | --- |
| `examples/agent/provider.py` | provider 选择层，按 `BUIAM_AGENT_PROVIDER_MODE` 在 `mock` 与 `lark_cli` 间切换。 |
| `examples/agent/lark_cli_provider.py` | `lark-cli` 适配层，负责联系人、日程、wiki、bitable 读取和飞书文档创建，并把返回值标准化为现有 Agent 响应结构。 |
| `tests/test_agent_providers.py` | 覆盖 provider 选择、非法模式、真实 provider 数据标准化、文档创建响应标准化、下游错误透传。 |
| `docs/lark_cli_integration_report.md` | lark-cli 集成专项说明。 |

工作区还有运行产物未纳入本报告主体，例如 `.lark-auth-login.*`、`.lark-config-init.*`、`.npm-cache/`。这些应视为本地运行缓存或授权日志，不建议提交。

## 行为变化

### Provider 模式

新增环境变量：

```env
BUIAM_AGENT_PROVIDER_MODE=mock
```

支持值：

- `mock`：默认值，继续使用原有 demo provider。
- `lark_cli`：启用真实飞书 provider。

非法值会返回：

```text
PROVIDER_MODE_INVALID
```

### 企业数据读取

`enterprise_data_agent` 在 `lark_cli` 模式下读取：

- 通讯录用户：`/open-apis/contact/v3/users`
- 当前日程：`lark-cli calendar +agenda`
- wiki 空间：`/open-apis/wiki/v2/spaces`
- 多维表格记录：`/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records`

如果未配置 bitable 的 `app_token` 或 `table_id`，不会中断整个企业数据读取，而是返回空 `bitable_records` 并记录 provider warning。

### 文档创建

`doc_agent` 在 `lark_cli` 模式下仍本地生成报告内容，但文档写入改为：

```text
lark-cli docs +create
```

本次适配中已处理 `docs +create` 不支持全局 `--format json` 的差异：`api` 与 `calendar` 命令继续使用 JSON 输出，`docs` 命令不再附加该参数。

### 错误处理

新增 provider 层错误码：

- `PROVIDER_MODE_INVALID`
- `LARK_CLI_BIN_MISSING`
- `LARK_CLI_NOT_FOUND`
- `LARK_CLI_TIMEOUT`
- `LARK_CLI_FAILED`
- `LARK_CLI_OUTPUT_INVALID`
- `LARK_CLI_BITABLE_CONFIG_MISSING`

`doc_agent` 会透传 `enterprise_data_agent` 的 provider 错误，避免在企业数据读取失败后继续生成错误报告。

## 安全影响

Gateway 侧安全链路未被放宽：

- A2A root task 和 agent-to-agent 调用仍走 Gateway。
- 授权 hop 仍创建签名 `DelegationCredential`。
- intent node 仍创建并参与链路审计。
- `external_search_agent` 仍无法越权读取企业数据。
- `delegation_chain` 仍仅作为可读审计上下文，不作为安全事实来源。

真实飞书访问只发生在 Agent provider 层，且仍受 Gateway 授权链路约束。

## 验证结果

### 单元测试

已执行：

```powershell
D:\anaconda\envs\buiam\python.exe -m pytest tests\test_agent_providers.py -q -p no:cacheprovider
```

结果：

```text
5 passed
```

### 真实飞书 provider 验证

在 `BUIAM_AGENT_PROVIDER_MODE=lark_cli` 下，直接调用 provider 成功：

| 数据类型 | 结果 |
| --- | --- |
| contacts | 10 |
| wiki_pages | 1 |
| calendar_events | 0 |
| bitable_records | 0 |

`bitable_records` 为 0 的原因是尚未配置：

```env
BUIAM_LARK_CLI_BITABLE_APP_TOKEN=
BUIAM_LARK_CLI_BITABLE_TABLE_ID=
```

### 完整三 Agent 流程验证

正常链路：

```text
user_123 -> doc_agent -> enterprise_data_agent
```

结果：

- HTTP `200`
- `doc_agent` 成功生成报告
- 成功创建真实飞书文档，provider 为 `lark_cli_doc_provider`
- `enterprise_data_agent` 使用真实 provider：`lark_cli_enterprise_provider`
- Gateway 审计：
  - `user_123 -> doc_agent`: `allow`
  - `doc_agent -> enterprise_data_agent`: `allow`
- delegation credential 数量：2
- intent node 数量：2

越权链路：

```text
user_123 -> external_search_agent -> enterprise_data_agent
```

结果：

- HTTP `200`
- `external_search_agent` 正常执行公共搜索
- 企业数据越权升级被拒绝
- Gateway 审计：
  - `user_123 -> external_search_agent`: `allow`
  - `external_search_agent -> enterprise_data_agent`: `deny`
- delegation credential 数量：1
- intent node 数量：2

## 配置要求

启用真实飞书 provider 需要：

```env
BUIAM_AGENT_PROVIDER_MODE=lark_cli
BUIAM_LARK_CLI_BIN=C:\Users\13453\AppData\Roaming\npm\lark-cli.cmd
BUIAM_LARK_CLI_AS=user
BUIAM_LARK_CLI_TIMEOUT_SECONDS=30
```

可选但推荐：

```env
BUIAM_LARK_CLI_BITABLE_APP_TOKEN=<app_token>
BUIAM_LARK_CLI_BITABLE_TABLE_ID=<table_id>
BUIAM_LARK_CLI_DOC_FOLDER_TOKEN=<folder_token>
```

飞书授权需至少覆盖当前读取范围。已验证重新授权后可读取通讯录和 wiki；bitable 还依赖目标表格 token 和表级访问权限。

## 风险与注意事项

- 当前 `lark-cli` 集成依赖本机已完成授权，CI 或其他机器默认仍应使用 `mock`。
- `.lark-auth-login.*`、`.lark-config-init.*`、`.npm-cache/` 属于本地运行产物，不应提交。
- 真实飞书返回结构可能随 API 或 CLI 版本变化，当前适配层做了兼容性归一化，但后续仍建议增加少量契约测试或 CLI dry-run 验证。
- Bitable 未配置时当前行为是降级为空列表并给出 warning，这适合 demo，但生产环境可能需要改为强失败。

## 建议后续工作

1. 将本地运行产物加入 `.gitignore`，避免授权日志和 npm cache 进入提交。
2. 配置 `BUIAM_LARK_CLI_BITABLE_APP_TOKEN` 与 `BUIAM_LARK_CLI_BITABLE_TABLE_ID` 后补测 bitable 读取。
3. 如要进入 PR，建议再跑完整测试：

```powershell
D:\anaconda\envs\buiam\python.exe -m pytest -q -p no:cacheprovider
```

4. 网络恢复后重新执行 `git fetch origin main`，确认 GitHub 主分支是否已有新提交，再刷新本报告。
