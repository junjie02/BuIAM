# Agent身份与认证模块实现方案计划
| 项 目 | 内 容 |
| --- | --- |
| 模块名称 | Agent身份与认证模块 |
| 对应技术方案章节 | 5.1 Agent身份与认证模块、6.1 Access Token字段设计、8节相关接口 |
| 版本 | V1.0 |
| 计划周期 | 4天 |
| 依赖模块 | 数据存储层(app/store)、Agent注册中心(app/registry) |

---
## 一、模块概述
本模块是多Agent权限系统的基础核心模块，负责实现Agent唯一身份标识管理、可信令牌全生命周期管理，满足技术方案提出的**"身份可识别、令牌可签发/校验/过期/吊销"**核心要求，为上层授权、委托、网关拦截模块提供身份可信基础。

核心目标：
1. 为每个Agent建立全局唯一、不可篡改的身份标识
2. 实现符合JWT规范的Access Token全生命周期管理
3. 提供标准的身份校验接口，供网关和业务服务调用
4. 满足令牌盗用防范、安全审计等安全要求

---
## 二、现有项目基础
当前项目已具备初步的模块骨架，可基于现有能力扩展：
| 现有文件 | 已有能力 | 需要扩展内容 |
| --- | --- | --- |
| `app/identity/jwt_service.py` | 基础JWT签名/解析框架 | 完善Token字段、增加吊销校验、来源绑定校验 |
| `app/identity/keys.py` | 密钥基础定义 | 增加RSA密钥对生成、持久化、轮换机制 |
| `app/identity/routes.py` | 基础路由骨架 | 实现令牌签发、校验、吊销接口 |
| `app/registry/routes.py` | 基础注册路由骨架 | 完善Agent元数据字段、注册校验逻辑 |
| `app/store/registry.py` | Agent信息存储模型 | 扩展Agent元数据字段、状态管理 |
| `app/store/tokens.py` | Token存储模型 | 增加jti黑名单存储、过期自动清理逻辑 |

---
## 三、实现Roadmap
| 阶段 | 周期 | 核心任务 | 交付物 |
| --- | --- | --- | --- |
| 阶段1：基础能力实现 | 2天 | 1. Agent注册功能完善<br>2. JWT密钥管理实现<br>3. 基础Token签发/校验功能 | 注册接口、Token签发/校验接口、单元测试 |
| 阶段2：增强能力实现 | 1天 | 1. Token吊销与黑名单机制<br>2. 令牌安全增强（来源绑定、短时有效期）<br>3. 过期数据自动清理 | 吊销接口、安全校验逻辑、清理脚本 |
| 阶段3：集成联调 | 1天 | 1. 与A2A网关模块集成<br>2. 与授权/委托模块集成<br>3. 全流程场景测试 | 集成测试报告、demo验证通过 |

---
## 四、核心功能实现细节
### 4.1 Agent注册管理
**修改文件**：`app/registry/routes.py`、`app/store/registry.py`、`app/store/schema.py`
1. 扩展Agent元数据字段（与技术方案一致）：
```python
# Pydantic模型定义
class AgentRegisterRequest(BaseModel):
    agent_name: str
    agent_type: Literal["doc_agent", "enterprise_data_agent", "external_search_agent"]
    description: str
    owner_org: str
    allowed_resource_domains: List[str]
    status: Literal["active", "inactive"] = "active"

# 数据库存储字段增加：agent_id(UUID自动生成)、create_time、update_time、last_seen_time
```
2. 注册逻辑：
- 自动生成全局唯一`agent_id`（UUID v4）
- 校验Agent名称唯一性
- 返回Agent初始凭证，用于后续Token申请

### 4.2 密钥管理
**修改文件**：`app/identity/keys.py`
1. 实现RSA256非对称密钥对生成、持久化到本地文件（部署时可配置到环境变量）
2. 支持密钥轮换机制，旧密钥保留用于历史Token验签
3. 提供公钥查询接口，供外部服务验签使用

### 4.3 Token签发功能
**修改文件**：`app/identity/jwt_service.py`
严格按照技术方案6.1节定义的字段实现Token生成：
```python
def generate_access_token(
    agent_id: str,
    role: str,
    delegated_user: Optional[str] = None,
    task_id: Optional[str] = None,
    scope: List[str] = None,
    aud: str = None,
    source_agent: Optional[str] = None,
    target_agent: Optional[str] = None,
    delegation_depth: int = 0,
    expires_in: int = 300  # 默认5分钟短时有效期
) -> Tuple[str, str]:
    payload = {
        "iss": "buiam-auth-service",
        "sub": agent_id,
        "agent_id": agent_id,
        "role": role,
        "delegated_user": delegated_user,
        "task_id": task_id,
        "scope": scope or [],
        "aud": aud,
        "source_agent": source_agent,
        "target_agent": target_agent,
        "delegation_depth": delegation_depth,
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_in,
        "jti": str(uuid.uuid4())  # 唯一标识用于吊销
    }
    token = jwt.encode(payload, private_key, algorithm="RS256")
    return token, payload["jti"]
```

### 4.4 Token校验功能
**修改文件**：`app/identity/jwt_service.py`、`app/identity/routes.py`
实现`/auth/introspect`校验接口，校验逻辑：
1. 验证JWT签名有效性
2. 校验`exp`过期时间
3. 查询jti黑名单，确认令牌未被吊销
4. 校验来源IP/Agent标识绑定（可选增强）
5. 返回Token解析后的完整上下文信息

### 4.5 Token吊销机制
**修改文件**：`app/store/tokens.py`、`app/identity/routes.py`
1. 实现`/auth/revoke`接口，传入jti加入黑名单
2. 黑名单存储到SQLite，设置与Token有效期相同的TTL
3. 每日定时清理过期的黑名单记录，减少存储占用

---
## 五、接口定义
| 接口 | 请求方式 | 路径 | 参数 | 返回值 |
| --- | --- | --- | --- | --- |
| Agent注册 | POST | `/agents/register` | agent_name, agent_type, allowed_resource_domains等 | agent_id, 初始凭证 |
| 令牌签发 | POST | `/auth/token` | agent_id, agent_secret, 权限参数等 | access_token, expires_in, jti |
| 令牌校验 | POST | `/auth/introspect` | token | 有效返回payload，无效返回错误 |
| 令牌吊销 | POST | `/auth/revoke` | jti | 成功/失败状态 |
| 公钥查询 | GET | `/auth/public-key` | 无 | RSA公钥内容 |

---
## 六、测试方案
### 单元测试（`tests/test_identity.py`）
1. Agent注册测试：正常注册、重复注册、字段合法性校验
2. Token签发测试：字段正确性、签名有效性、过期时间校验
3. Token校验测试：有效Token校验、过期Token校验、篡改Token校验、吊销Token校验
4. 黑名单测试：吊销后校验失败、过期黑名单自动清理

### 集成测试
1. 与网关集成：无效Token直接拦截、有效Token透传上下文
2. 与委托模块集成：委托场景下Token字段正确传递
3. 与审计模块集成：所有身份操作生成审计日志

### 异常场景测试
- Token过期、签名篡改、Agent被禁用、令牌盗用等场景的处理逻辑

---
## 七、验收标准
完全符合技术方案要求的验收点：
1. ✅ 每个Agent具备唯一agent_id，元数据完整可查
2. ✅ Token支持签发、校验、过期自动失效、手动吊销四项能力
3. ✅ 所有下游服务调用校验接口返回信息完整，符合规范
4. ✅ 令牌默认短时有效期（<=5分钟），支持黑名单吊销
5. ✅ 异常场景下返回明确错误码，无安全漏洞
6. ✅ 与demo场景集成正常，文档Agent、企业数据Agent、外部检索Agent身份可正确识别