# P1 数据、安全与身份底座实施指南

## 1. 本阶段目标

建立后续所有业务共同依赖的可信身份、租户边界、持久模型、脱敏和审计。完成后，业务模块只消费 `AuthContext`，不再自行解析身份。

本阶段不要实现意图、RAG、财务或提醒业务。

## 2. 开工前检查

- 读 `PLAN.md` §7、`docs/architecture.md` §4、需求 FR6/NFR3/NFR4。
- 读 `PROGRESS.md`，区分 P1 已实现、存在但未验证和待实现项；开工前重新检查是否有并发写入者。
- 审计 `0001`、`0002` 及当前最新迁移，不能仅根据模型文件判断数据库状态。
- 运行现有 unit/integration 测试并记录基线。

## 3. 目标文件

主要修改：

```text
app/core/security.py
app/core/logging.py
app/api/dependencies.py
app/api/auth.py
app/api/admin.py
app/models/{user,conversation,message,outbox,audit_log}.py
app/services/{identity,audit,conversation}_service.py
app/utils/masking.py
migrations/versions/<new_revision>.py
tests/unit/test_{security,masking}.py
tests/integration/test_{auth,isolation}.py
```

需要同步：`.env.example`、`docs/api.md`；如需改变冻结 claim 或角色，必须先停止实施并取得用户确认，随后同步 `docs/architecture.md` 和偏差记录。

## 4. 实现顺序

### 4.1 模型与约束

逐表核对 `tenants/users/conversations/messages/outbox_events/audit_logs`：

- 所有租户数据表包含非空 `tenant_id` 和合适索引；
- `users(tenant_id,id)`、`users(tenant_id,email)` 唯一；
- `messages(tenant_id,message_id)` 唯一；
- outbox `event_id` 或业务幂等键唯一；
- 审计包含 `trace_id/tenant_id/actor_id/action/target/outcome/created_at`；
- 状态字段有明确默认值，但服务层仍校验合法迁移。

先写迁移测试或空库验证，再新增 revision。不得修改已经执行过的 migration 来伪装升级路径。

### 4.2 密码和 JWT

- 密码只存带随机 salt 的强哈希；错误格式验证返回失败而非抛出内部异常。
- JWT claim 固定为 `sub/tenant_id/role/exp`，算法只接受配置中的 allowlist 值。
- token 缺字段、过期、签名错误、非法角色均映射 401。
- `JWT_SECRET=dev_secret_change_me` 只能用于本地；非 development 环境启动时应拒绝弱默认值或记录明确阻断。

### 4.3 FastAPI 认证与 RBAC

- `get_current_user` 是 Bearer token 的唯一解析入口，并返回不可变 `AuthContext`。
- `require_roles` 只做角色 gate；资源归属仍在 service 查询中以 `tenant_id + resource_id` 校验。
- 401 表示未认证；403 表示身份有效但无权；不要用 404/500 掩盖所有授权错误。
- 请求 body 中即使出现 tenant/user 字段，也只能做一致性校验，不能成为可信来源。

### 4.4 租户安全查询

每个 service 方法把 `tenant_id` 作为必需参数，并把它放进数据库谓词：

```text
get_user(tenant_id, user_id)
get_conversation(tenant_id, conversation_id)
get_message(tenant_id, message_id)
list_audit_logs(tenant_id, ...)
```

禁止先按全局 ID 获取行、再在 Python 中判断 tenant；数据库查询本身必须收窄。

### 4.5 集中式脱敏

`app/utils/masking.py` 负责邮箱、手机号、银行卡、身份证和敏感键递归过滤：

- 使用 literal 期望测试边界长度、非匹配文本和嵌套 dict/list；
- `token/password/secret/authorization` 等键直接替换；
- 日志 formatter、审计 detail 和发送给 LLM 的财务 payload 使用同一能力；
- 不把原值放在异常消息中。

### 4.6 审计

- 审计与敏感业务尽量在同一事务提交；拒绝事件需在抛出 API 异常前提交。
- 记录“谁、对什么、做什么、结果如何”，不记录完整请求体或完整 PII。
- 管理员查询只返回当前租户；列表中的 PII 默认脱敏。

## 5. 必须先红后绿的行为

1. 正确登录和 `/users/me`；错误密码 401。
2. 缺 token、错签名、过期、缺 claim、非法 role 全部拒绝。
3. 普通用户访问 admin API 返回 403。
4. admin A 看不到 tenant B 的用户和审计。
5. 请求体伪造 tenant/user 不能覆盖 token。
6. tenant A 查询 tenant B 会话返回 403，并留下脱敏安全审计。
7. 删除 service 查询中的 tenant 谓词时，隔离测试必须失败。
8. 日志和审计中不出现完整邮箱、手机号、银行卡、身份证、token 或密码。

## 6. 建议测试命令

```bash
./.venv/bin/python -m pytest tests/unit/test_security.py tests/unit/test_masking.py -q
./.venv/bin/python -m pytest tests/integration/test_auth.py tests/integration/test_isolation.py -q
docker compose run --rm api pytest tests/unit tests/integration -q
docker compose run --rm api alembic downgrade -1
docker compose run --rm api alembic upgrade head
```

若 downgrade 会破坏共享开发数据，只能在专用测试库/临时 Compose volume 中执行。

## 7. 常见错误

- 仅在 API 层比较 tenant，service 仍可被 worker 绕过。
- 用请求体 tenant 构造审计，导致攻击者污染审计归属。
- 对管理员开放无 tenant 条件的全局列表。
- 测试只 mock `decode_token`，没有验证真实签名、过期和 claim。
- 脱敏只处理字符串，不处理嵌套响应和日志 fields。
- 为追求覆盖率断言私有实现，而没有跨租户真实反例。

## 8. 退出清单

- [ ] 最新迁移可从空库运行，并可从上一 revision 升级。
- [ ] JWT/RBAC/资源归属的正反路径有行为测试。
- [ ] PostgreSQL 所有现有租户查询都带 tenant 条件。
- [ ] 请求/LLM 伪造身份无法覆盖认证上下文。
- [ ] 集中脱敏覆盖日志、审计和嵌套数据。
- [ ] `docs/api.md` 写明登录、认证 header、401/403 和管理员接口。
- [ ] `PROGRESS.md` 保存容器内测试与迁移证据。
