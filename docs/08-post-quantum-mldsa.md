# 后量子密码 — ML-DSA

## 概述

BuIAM 支持通过 ML-DSA-65 (Module-Lattice Digital Signature Algorithm) 进行后量子签名，以抵御量子计算攻击。默认签名算法为 RSA，可通过环境变量切换到 ML-DSA。

## 依赖

- **liboqs** (C 库): 通过 `third_party/liboqs` git submodule 提供
- **liboqs-python**: Python 绑定

### 编译 liboqs (Windows)

```bat
cd third_party/liboqs
cmake -S . -B build -DCMAKE_INSTALL_PREFIX="%CD%\install" -DBUILD_SHARED_LIBS=ON
cmake --build build --config Release
cmake --install build --config Release
```

预期产物：
```
third_party/liboqs/install/bin/oqs.dll
third_party/liboqs/install/lib/oqs.lib
third_party/liboqs/install/include/oqs/sig_ml_dsa.h
```

安装 Python 绑定：
```powershell
pip install liboqs-python
```

## 配置

```powershell
$env:BUIAM_USE_MLDSA='true'
$env:BUIAM_AUTH_SIGNATURE_ALG='BUIAM-MLDSA-65'
```

## 密钥管理

ML-DSA 密钥存储在 `data/keys/` 下：
- `{agent_id}_mldsa_private.pem` — 私钥 (sk)
- `{agent_id}_mldsa_public.pem` — 公钥 (pk)

密钥格式:
```json
{
  "kty": "ML-DSA",
  "alg": "ML-DSA-65",
  "sk": "<base64 secret key>",
  "pk": "<base64 public key>"
}
```

`ensure_agent_mldsa_keypair()` 在密钥缺失或损坏时自动生成新密钥对，包含健康检查（签名+验证探针）。

## 影响范围

启用 ML-DSA 后影响以下安全操作：

| 操作 | 代码路径 |
|------|---------|
| JWT 签发 | `jwt_service.issue_token()` → `mldsa_sign_with_kid()` |
| JWT 验证 | `jwt_service.inspect_token()` → `mldsa_verify_with_kid()` |
| 委托凭证签名 | `credential_crypto.build_delegation_credential()` → `mldsa_sign_with_kid()` |
| 委托凭证验证 | `credential_crypto.verify_credential_integrity()` → `mldsa_verify_with_kid()` |
| DID proof 创建 | `did_proof.create_did_proof()` → `mldsa_sign()` |
| DID proof 验证 | `did_proof.verify_did_proof()` → `_mldsa_verify_with_public()` |
| 意图节点签名 | `intent/crypto.py` → `mldsa_sign_with_kid()` |
| 意图节点验证 | `intent/crypto.py` → `mldsa_verify_with_kid()` |

## 算法不匹配保护

验证时检查 JWT header 的 `alg` 与 DID Document 中的 `kty` 是否一致：
- `alg: BUIAM-MLDSA-65` + `kty: RSA` → `AUTH_TOKEN_KID_INVALID`
- `alg: BUIAM-RS256` + `kty: ML-DSA` → `AUTH_TOKEN_KID_INVALID`

## 测试

所有自动化测试锁定 RSA 模式以确保确定性结果：
```python
os.environ["BUIAM_USE_MLDSA"] = "false"
os.environ["BUIAM_AUTH_SIGNATURE_ALG"] = "BUIAM-RS256"
```

ML-DSA 功能通过手动验证：
```powershell
python -c "from app.identity.keys import ensure_agent_mldsa_keypair; ensure_agent_mldsa_keypair('test'); print('ML-DSA ok')"
```
