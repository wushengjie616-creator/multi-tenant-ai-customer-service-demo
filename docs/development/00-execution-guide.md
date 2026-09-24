# 开发执行总指南

> 作用：告诉开发 Agent 如何从 `PLAN.md` 进入对应板块、如何判定完成。本文不替代需求和架构决策。

## 1. 权威顺序

发生冲突时按以下顺序处理：

1. 原始 Word 与 `../项目需求拆解.md`：功能范围和验收要求；
2. `PLAN.md`：冻结技术选型、实施顺序、阶段依赖和退出条件；
3. `docs/architecture.md`：在 PLAN 约束下细化冻结契约；
4. 本目录的板块指南：实现方法、目标文件和测试清单；
5. `PROGRESS.md`：当前实际进度和新发现的偏差；
6. `docs/reference/`：参考设计，不是强制实现范围。

禁止为了迎合本指南而修改更高层权威文档的含义。发现矛盾时先记录到 `PROGRESS.md`，再由负责人决定。

## 2. 板块导航

| PLAN 阶段 | 实现指南 | 主要产物 |
|---|---|---|
| P0 | `PLAN.md` §6 | 技术栈、Compose、健康检查基线 |
| P1 | [01-foundation-security.md](01-foundation-security.md) | 数据模型、迁移、JWT、RBAC、租户、脱敏、审计 |
| P2 | [02-messaging-and-session.md](02-messaging-and-session.md) | HTTP/WS、Outbox、NATS、worker、重连、上下文 |
| P3/P5/P6 | [03-intent-tools-and-business.md](03-intent-tools-and-business.md) | 意图路由、工具安全、财务、平台指令 |
| P4 | [04-knowledge-rag.md](04-knowledge-rag.md) | 知识导入、Qdrant、证据门控、引用校验 |
| P7 | [05-reminders-and-handoff.md](05-reminders-and-handoff.md) | 提醒调度、投递幂等、人工转接 |
| P8 | [06-reliability-observability.md](06-reliability-observability.md) | 限流、超时、重试、熔断、指标、trace、故障注入 |
| P9/P10 | [07-verification-and-delivery.md](07-verification-and-delivery.md) | E2E、覆盖率、Locust、评测、文档、演示 |

`docs/reference/whynotai-customer-service-design-reference.md` 是上述指南的设计来源之一；其中标记为延期的高级能力不得进入关键路径。

## 3. 每个阶段的固定执行循环

开发 Agent 每次只能推进 `PROGRESS.md` 指定的当前阶段：

1. **核基线**：读 `PLAN.md` 当前阶段、本板块指南、`PROGRESS.md`、相关实现和现有测试。
2. **列差距**：把“已存在且已验证”“存在但未验证”“缺失”“与契约冲突”分开，不以文件存在推断功能完成。
3. **切小闭环**：一次实现一个可观察行为，不按目录批量填空文件。
4. **RED**：先写能因缺失行为而失败的测试，运行并确认失败原因正确。
5. **GREEN**：写最小实现，运行聚焦测试。
6. **REFACTOR**：只在绿色保护下去重或抽象，再运行聚焦测试。
7. **阶段验证**：运行本指南列出的集成、反向安全和 Compose 验证。
8. **同步契约**：有 API、事件、环境变量或运行方式变化时，同步 `docs/api.md`、README、`.env.example` 或架构文档。
9. **更新进度**：在 `PROGRESS.md` 记录真实命令、退出码、证据、偏差与未验证项。

不得使用“测试文件存在”“mock 被调用”“代码能 import”代替真实行为验证。

## 4. 跨模块硬约束

### 4.1 身份与租户

- `tenant_id`、`user_id`、`role` 只能来自已验证 JWT 或可信内部事件；请求体和 LLM 输出不能覆盖。
- PostgreSQL 查询、Redis key、Qdrant filter、事件 payload 和审计均必须保持同一租户上下文。
- 跨租户拒绝必须有反向测试；至少一项 mutation 检查应证明删除 tenant 条件会使测试失败。

### 4.2 副作用

- LLM 输出仅是候选意图或候选工具计划。
- 固定执行顺序：Schema → allowlist → RBAC → 资源归属 → 风险确认 → 幂等 → 执行 → 审计。
- 任一前置步骤失败时，下游工具调用次数为 0。

### 4.3 可靠消息

- 数据库写入和 outbox 写入同事务；JetStream ACK 后才能把 outbox 标成功。
- 队列保证至少一次投递，业务层用唯一键保证恰好一次效果。
- 所有重试必须有可恢复错误分类、最大次数和最终失败去向。

### 4.4 安全输出

- 财务原始 PII 在进入 LLM 前脱敏。
- 知识回答只展示服务端验证过的引用。
- 无证据、下游失败或身份不清时使用确定性降级，禁止模型补全业务事实。

## 5. 文件归属约定

| 层 | 放什么 | 不放什么 |
|---|---|---|
| `app/api/` | 传输、依赖注入、状态码和响应 Schema | 业务规则、直接拼复杂 SQL |
| `app/schemas/` | API/事件/工具的 Pydantic 契约 | ORM 行为 |
| `app/models/` | SQLAlchemy 持久模型和数据库约束 | 下游调用 |
| `app/services/` | 用例编排、状态机、授权后业务行为 | HTTP 连接细节 |
| `app/clients/` | httpx 下游协议、超时和错误映射 | 业务授权决策 |
| `app/core/` | 配置、数据库、NATS、安全、日志、指标、trace | 单一业务场景 |
| `app/workers/` | 消费/调度入口和 ACK/NAK 生命周期 | 重复实现 service 规则 |
| `mocks/` | 确定性成功与故障模式、调用记录 | 生产业务逻辑 |
| `tests/unit/` | 纯规则、状态机、Schema、错误分类 | 真实基础设施假装测试 |
| `tests/integration/` | PostgreSQL/Redis/NATS/Qdrant/API 组合 | 付费或公网依赖 |
| `tests/e2e/` | 用户可见完整场景 | 只断言内部函数调用 |

## 6. 数据库与迁移规则

- 每次模型变化必须有新的 Alembic revision，不回写已验证的历史迁移。
- 新迁移同时验证：空库 `upgrade head`、上一 revision 升级到 head、必要时 downgrade。
- 幂等、安全和状态机边界尽量使用数据库约束兜底，而不是只靠应用层 `if`。
- 多实例抢占使用 `FOR UPDATE SKIP LOCKED` 或带过期时间的明确 lease。

## 7. 测试证据标准

每项关键行为至少回答四个问题：

1. 哪个真实故障会让这个测试失败？
2. 期望值是否来自 literal/fixture/手算，而非被测实现？
3. 是否观察到用户可见结果、持久化副作用或下游调用边界？
4. 把 tenant filter、确认检查、幂等键或引用校验故意删掉时，哪个测试会红？

统一命令基线：

```bash
./.venv/bin/python -m pytest tests/unit -q
./.venv/bin/python -m pytest tests/integration -q
docker compose run --rm api pytest -q
docker compose config
```

涉及真实 Compose 链路时，以容器内结果为最终证据；本机绿色不能替代 fresh container 验证。

## 8. 完成声明模板

阶段完成时在 `PROGRESS.md` 写清：

```text
阶段：P?
实现：文件/迁移/API/事件
RED：命令、失败原因、退出码
GREEN：命令、通过数量、退出码
集成/Compose：命令、可观察结果
安全反例：测试名与被阻止的真实风险
文档同步：更新位置
未验证/偏差：没有则写“无”
下一阶段前置：依赖和风险
```

只有 `PLAN.md` 对应退出条件全部有新鲜证据，才能把 `PROGRESS.md` 推进到下一阶段。
