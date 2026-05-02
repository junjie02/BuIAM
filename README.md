# BuIAM A2A 安全委托 Demo

BuIAM 是一个基于 FastAPI 的 Agent-to-Agent 安全委托协议实现。业务 Agent 的动作可以使用确定性的 mock provider 跑通本地演示和测试；Gateway 侧的安全逻辑是真实实现，包括 DID 文档、VC 形态的签名委托凭证、意图链校验、Token 过期/吊销、运行中任务取消和审计追踪。

## 当前能力

- 正式 Gateway 入口：
  - `POST /a2a/root-tasks`
  - `POST /a2a/agents/{target_agent_id}/tasks`
- 每一跳授权都会生成签名 `DelegationCredential`，结构接近 Verifiable Credential。
- `credential_id` 是可重算的哈希链节点 ID。
- 支持 DID verification method，默认 RSA，也可切换到 ML-DSA。
- Root task 和 Agent-to-Agent 调用都会创建或校验签名 intent node。
- Bearer Token 支持签发、校验、introspection、过期、吊销和 credential 绑定。
- 审计 trace 汇总 audit logs、auth events、delegation credentials 和 intent tree。
- Demo Agent provider 支持两种模式：
  - `mock`：确定性本地数据，推荐用于测试和基线演示。
  - `lark_cli`：可选，通过本机 `lark-cli` 读取/写入真实飞书数据。

## 目录结构

```text
app/                  Gateway、identity、delegation、intent、registry、store
examples/agent/       Demo Agent 服务和 provider 层
scripts/              demo/bootstrap 辅助脚本
scripts/security/     手工安全验证脚本
tests/                pytest 回归测试
third_party/liboqs    liboqs submodule，用于可选 ML-DSA 运行时
data/                 本地运行数据库和密钥材料，不应提交
```

## 基础安装

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

克隆或 pull 后初始化第三方库：

```powershell
git submodule update --init --recursive
```

如果 `third_party/liboqs` 是空目录，说明 submodule 还没有初始化。

## Windows 编译 liboqs 供 ML-DSA 使用

项目通过 `liboqs-python` 调用 ML-DSA，但还需要本机可加载的 liboqs 运行时。代码会自动查找：

```text
third_party/liboqs/install
```

推荐 Windows 工具链：

- Visual Studio Build Tools 2022
- 工作负载：`Desktop development with C++`
- 组件：MSVC v143、Windows 10/11 SDK
- CMake 已安装并加入 `PATH`

打开开始菜单里的：

```text
x64 Native Tools Command Prompt for VS 2022
```

然后执行：

```bat
cd /d F:\AAA飞书挑战赛\Code\third_party\liboqs
rmdir /s /q build

cmake -S . -B build -DCMAKE_INSTALL_PREFIX="%CD%\install" -DBUILD_SHARED_LIBS=ON
cmake --build build --config Release
cmake --install build --config Release
```

安装成功后应能看到：

```text
third_party/liboqs/install/bin/oqs.dll
third_party/liboqs/install/lib/oqs.lib
third_party/liboqs/install/include/oqs/sig_ml_dsa.h
```

安装 Python 绑定：

```powershell
cd F:\AAA飞书挑战赛\Code
.venv\Scripts\activate
python -m pip install liboqs-python
```

验证 ML-DSA：

```powershell
python -c "from app.identity.keys import ensure_agent_mldsa_keypair; ensure_agent_mldsa_keypair('test-agent'); print('ML-DSA ok')"
```

如果要把签名算法从默认 RSA 切到 ML-DSA：

```powershell
$env:BUIAM_USE_MLDSA='true'
$env:BUIAM_AUTH_SIGNATURE_ALG='BUIAM-MLDSA-65'
```

## 运行 Demo

推荐先用 mock 模式跑通基线：

```powershell
$env:BUIAM_AGENT_PROVIDER_MODE='mock'
$env:LLM_PROVIDER='mock'
$env:INTENT_GENERATOR_PROVIDER='mock'
$env:INTENT_JUDGE_PROVIDER='mock'
python scripts/demo.py
```

`scripts/demo.py` 会在本机端口没有服务时自动启动 Gateway 和三个 demo Agent。

也可以手动用四个终端启动：

```powershell
uvicorn app.main:app --port 8000
uvicorn examples.agent.doc_service:app --port 8011
uvicorn examples.agent.enterprise_data_service:app --port 8012
uvicorn examples.agent.external_search_service:app --port 8013
```

## 测试

只测试本项目的 `tests/`，不要让 pytest 扫到 `third_party/liboqs/tests`。

```powershell
$env:BUIAM_AGENT_PROVIDER_MODE='mock'
$env:LLM_PROVIDER='mock'
$env:INTENT_GENERATOR_PROVIDER='mock'
$env:INTENT_JUDGE_PROVIDER='mock'
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

当前本地基线：

```text
42 passed
```

## 安全验证脚本

```powershell
python scripts/security/verify_delegation_chain.py
python scripts/security/verify_intent_chain.py
python scripts/security/verify_chain_binding.py
python scripts/security/verify_token_lifecycle.py
python scripts/security/verify_a2a_identity.py
python scripts/security/verify_identity_vc.py --json
python scripts/security/run_all_security_checks.py
```

其中 `verify_identity_vc.py --json` 会输出：

- 验证流程；
- 已注册的 DID Documents；
- 解码后的 token header 和 claims；
- token introspection 结果；
- root VC 形态凭证；
- 委托给 Agent 的 VC 形态凭证；
- 重算后的 `content_hash`；
- 重算后的 `credential_id`；
- proof 签名验证结果。

## lark-cli Provider 模式

使用真实飞书数据时，Gateway 安全链路不变，只替换 demo Agent 的 provider 层。

启用真实飞书 provider：

```powershell
$env:BUIAM_AGENT_PROVIDER_MODE='lark_cli'
$env:BUIAM_LARK_CLI_BIN='lark-cli'
$env:BUIAM_LARK_CLI_AS='user'
$env:BUIAM_LARK_CLI_BITABLE_APP_TOKEN='app_token'
$env:BUIAM_LARK_CLI_BITABLE_TABLE_ID='tbl_id'
```

单独安装并登录 `lark-cli`：

```powershell
npm install -g @larksuiteoapi/cli
lark-cli auth login --recommend
```

测试和稳定演示推荐使用 `mock`。只有确实需要访问真实飞书数据时再切到 `lark_cli`。

## 提交注意事项

不要提交：

- `.env`
- 真实 API Key
- `data/` 下生成的数据库
- 生成的密钥对
- `.venv`
- `third_party/liboqs/build`
- `third_party/liboqs/install`

共享 liboqs 的正确方式是提交 submodule 元数据和 submodule 指针：

```powershell
git add .gitmodules third_party/liboqs
git commit -m "Fix liboqs submodule metadata"
git push
```
