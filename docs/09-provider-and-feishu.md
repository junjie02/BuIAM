# 飞书 Provider 集成

Demo Agent 的业务逻辑通过真实飞书 provider 层实现，Gateway 侧认证、签名委托凭证、意图链、吊销和审计逻辑不需要改动。

```text
Agent Handler (doc_agent.py / enterprise_data_agent.py)
    -> provider.py
        -> lark_cli_provider.py   通过本机 lark-cli 访问真实飞书
```

## 安装和登录 lark-cli

项目使用飞书官方开源 CLI: `https://github.com/larksuite/cli`。

```powershell
npm install -g @larksuite/cli
lark-cli config init
lark-cli auth login --recommend
```

可用下面的脚本做本地自检：

```powershell
python scripts/check_lark_cli_provider.py
python scripts/check_lark_cli_provider.py --json
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `BUIAM_LARK_CLI_BIN` | `lark-cli` 可执行文件，默认 `lark-cli` |
| `BUIAM_LARK_CLI_EXTRA_ARGS` | 传给 CLI 的额外全局参数 |
| `BUIAM_LARK_CLI_AS` | `user` 或 `bot` |
| `BUIAM_LARK_CLI_TIMEOUT_SECONDS` | 单次 CLI 调用超时 |
| `BUIAM_LARK_CLI_CONTACT_QUERY` | 可选联系人查询条件 |
| `BUIAM_LARK_CLI_CONTACT_PAGE_SIZE` | 联系人读取数量 |
| `BUIAM_LARK_CLI_WIKI_PAGE_SIZE` | Wiki 空间读取数量 |
| `BUIAM_LARK_CLI_BITABLE_APP_TOKEN` | 多维表格 app token |
| `BUIAM_LARK_CLI_BITABLE_TABLE_ID` | 多维表格 table ID |
| `BUIAM_LARK_CLI_BITABLE_PAGE_SIZE` | 多维表格读取数量 |
| `BUIAM_LARK_CLI_DOC_FOLDER_TOKEN` | 可选文档创建目标文件夹 |
| `BUIAM_LARK_CLI_DOC_API_VERSION` | 文档创建 API 版本，默认 `v2` |
| `BUIAM_LARK_CLI_DOC_FORMAT` | 文档内容格式，默认 `markdown` |

## 飞书数据覆盖

`enterprise_data_agent` 会读取：

- 通讯录：`api GET /open-apis/contact/v3/users`
- 日历：`calendar +agenda`
- Wiki：`api GET /open-apis/wiki/v2/spaces`
- 多维表格：`api GET /open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records`

`doc_agent` 会创建文档：

- `docs +create --api-version v2 --doc-format markdown --content ...`

多维表格的 `app_token` 和 `table_id` 不配置时不会阻断整条链路，结果中会返回空 `bitable_records` 和 provider warning。其他数据源也采用部分失败降级：只要至少一个真实数据源成功，就返回已取得的数据和 warnings；如果全部读取失败，Agent 返回 `LARK_CLI_ENTERPRISE_READ_FAILED`。

## 运行真实飞书 Demo

```powershell
$env:BUIAM_LARK_CLI_BIN='lark-cli'
$env:BUIAM_LARK_CLI_AS='user'
$env:LLM_PROVIDER='mock'
$env:INTENT_GENERATOR_PROVIDER='mock'
$env:INTENT_JUDGE_PROVIDER='mock'

python scripts/check_lark_cli_provider.py
python scripts/demo.py
```

如果需要多维表格数据：

```powershell
$env:BUIAM_LARK_CLI_BITABLE_APP_TOKEN='<app_token>'
$env:BUIAM_LARK_CLI_BITABLE_TABLE_ID='<table_id>'
```
