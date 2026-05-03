# Provider 模式与飞书集成

## Provider 架构

Demo Agent 的业务逻辑通过可配置的 provider 层实现，与 Gateway 安全链路完全解耦：

```
Agent Handler (doc_agent.py)
    │
    ▼
provider 层 (demo_provider.py / lark_cli_provider.py)
    │
    ├── mock 模式: 返回确定性的本地 mock 数据
    └── lark_cli 模式: 通过 lark-cli 调用真实飞书 API
```

## Provider 模式切换

```powershell
# Mock 模式 (测试和基准演示)
$env:BUIAM_AGENT_PROVIDER_MODE='mock'

# 飞书真实数据模式
$env:BUIAM_AGENT_PROVIDER_MODE='lark_cli'
```

## lark-cli 配置

安装和认证：
```powershell
npm install -g @larksuiteoapi/cli
lark-cli auth login --recommend
```

关键环境变量：

| 变量 | 说明 |
|------|------|
| `BUIAM_LARK_CLI_BIN` | lark-cli 可执行文件路径 (默认 `lark-cli`) |
| `BUIAM_LARK_CLI_AS` | 飞书身份 (user/bot) |
| `BUIAM_LARK_CLI_TIMEOUT_SECONDS` | 命令超时 |
| `BUIAM_LARK_CLI_BITABLE_APP_TOKEN` | 多维表格 app token |
| `BUIAM_LARK_CLI_BITABLE_TABLE_ID` | 多维表格 table ID |
| `BUIAM_LARK_CLI_DOC_FOLDER_TOKEN` | 文档创建目标文件夹 |

## 飞书 API 覆盖

| Agent | 功能 | lark-cli 命令 |
|-------|------|--------------|
| enterprise_data_agent | 读取通讯录 | `lark-cli contact ...` |
| enterprise_data_agent | 读取日历 | `lark-cli calendar ...` |
| enterprise_data_agent | 读取知识库 | `lark-cli wiki ...` |
| enterprise_data_agent | 读取多维表格 | `lark-cli base ...` |
| doc_agent | 创建文档 | `lark-cli doc ...` |

## 错误处理

Provider 层错误映射：

| Provider 错误 | 含义 |
|--------------|------|
| `PROVIDER_MODE_INVALID` | 无效的 provider 模式配置 |
| lark-cli 不可用 | 降级为返回不含对应数据的 enterprise snapshot |
| lark-cli 超时 | 返回部分数据 + 警告 |
| 多维表格配置缺失 | 返回 enterprise data sans bitable + 警告 |
