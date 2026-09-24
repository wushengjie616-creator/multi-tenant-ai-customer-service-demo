# 教育平台 AI 客服后端实施计划（v2 重写版）

> 日期：2026-09-23  
> 状态：可执行；新 agent 开始实施后冻结正文  
> 需求基准：`../项目需求拆解.md`，其上游为原始 Word 题目  
> 冻结技术基线：**NATS JetStream + Qdrant + Locust**  
> 接手原则：现有代码和“已完成”标记都不是完成证据。必须从 P0 开始重新检查、测试和验收。
> 导航补充：2026-09-23 仅新增 `docs/development/` 实施指南入口；未改变原计划范围、顺序或退出条件。

## 1. 目标与权威顺序

目标是在 5 个自然日内交付一套可启动、可测试、可压测、可演示的多租户教育平台 AI 客服机器人后端。

执行时按以下优先级判断：

1. 原始 Word 决定题目边界。
2. `../项目需求拆解.md` 决定功能编号、验收标准和交付物。
3. 本计划冻结技术选型、执行顺序和阶段出口。
4. `docs/architecture.md`、README、配置、依赖和代码必须与本计划一致。
5. `docs/development/` 提供各板块的具体实现顺序、目标文件、失败路径和测试清单，但不得改变本计划范围。
6. 现有实现只是待审计资产；fresh 测试和 Compose 验证才是完成证据。

新 agent 必须创建并持续更新 `PROGRESS.md`，记录当前阶段、基线检查、RED/GREEN 命令及退出码、Compose/迁移/E2E/压测证据、偏差与未验证项。

本计划进入实施后冻结。实际偏差写入 `PROGRESS.md`；若要改变冻结技术栈，必须停止实施并取得用户确认，不能悄悄回写计划掩盖变化。

## 2. 冻结技术栈

| 类别 | 选型 | 约束 |
|---|---|---|
| 语言 | Python 3.12 | 类型标注；异步链路不得混入阻塞调用 |
| Web | FastAPI + Uvicorn | HTTP、WebSocket、OpenAPI、健康检查 |
| Schema/配置 | Pydantic v2 + pydantic-settings | API、事件、工具参数、环境配置统一校验 |
| 数据库 | PostgreSQL 15 | 业务状态、消息、提醒和审计的最终事实源 |
| ORM/驱动 | SQLAlchemy 2.x Async + asyncpg | 请求与消费任务使用独立 `AsyncSession` |
| 迁移 | Alembic + psycopg2 | 支持从空库重复迁移到最新版本 |
| 缓存 | Redis 7 + `redis.asyncio` | 短期上下文、缓存和限流；不是最终事实源 |
| 消息队列 | **NATS JetStream + nats-py** | durable consumer、显式 ACK、至少一次投递、有限重试、死信 |
| 向量库 | **Qdrant + qdrant-client** | 共享 collection；所有写入和查询强制 `tenant_id` |
| HTTP Client | httpx `AsyncClient` | 生命周期内复用；下游独立超时 |
| 测试 | pytest + pytest-asyncio | unit/integration/E2E/fault；核心覆盖率 ≥ 70% |
| 压测 | **Locust** | HTTP 与 WebSocket 场景；结果可复现 |
| 容器 | Docker Compose | 固定镜像版本；`make up` 一键启动 |
| 可观测 | JSON 日志 + Prometheus + OpenTelemetry | trace 跨 HTTP、NATS、worker 和下游传播 |

### 2.1 本次技术修改

仓库此前发生了半迁移：第二步运行代码主要使用 NATS；配置、健康检查和部分文档改成了 RabbitMQ；Compose 仍含 pgvector，文档又声明 Qdrant；压测文档已写 Locust，但脚本没有有效实现。

本计划统一为 `NATS JetStream + Qdrant + Locust`：

- 三项都符合原始 Word 的可选范围。
- NATS 已进入消息闭环代码，保留它可避免高成本重写。
- Qdrant 和 Locust 尚未形成业务耦合，现在冻结成本最低。
- 不再引入 RabbitMQ、aio-pika、pgvector 或 k6。

`../项目需求拆解.md` 中 pgvector/k6 只代表早期推荐方案。P0 应把工具特定描述同步到当前选型，但不得改变 FR/NFR/E2E 的业务含义和编号。

## 3. 当前状态：只作审计线索

以下来自 2026-09-23 静态检查，新 agent 必须独立复核：

- NATS 代码已涉及 lifespan、WebSocket 出站、worker、outbox relay 和 mock IM。
- `config.py`、`health.py` 和部分文档曾切到 RabbitMQ/Qdrant。
- Compose、依赖、文档和代码不是同一套技术栈。
- README 曾把第 1、2 步标为完成，但测试仅有少量单元测试，缺少消息闭环集成证据。
- 项目此前不是 Git 仓库，不能依赖历史提交恢复工作基线。
- 当前机器此前未发现 Docker；若仍不可用，不得声称 Compose、集成或 E2E 已验证。

因此所有阶段复位为未完成，P0 之前不得继续开发业务功能。

## 4. 执行纪律

### 4.1 接手和并发

1. 先确认没有其他 agent/process 在写同一目录；同一时间只允许一个执行者修改。
2. 读取需求拆解、本计划、README、架构、Compose、配置、依赖、迁移和测试入口。
3. 不使用 `git reset --hard`、批量覆盖或删除处理未知改动。
4. 对现有文件分类为：保留、修正、重写、暂不使用；记录到 `PROGRESS.md`。
5. 不提交 `.env`、密钥、真实 PII、缓存、虚拟环境和测试产物。

### 4.2 测试优先

可测试的生产行为必须执行 RED → Verify RED → GREEN → Verify GREEN → REFACTOR：

- RED 必须因目标行为缺失或错误而失败，不能是 import、fixture、语法或环境错误。
- 先跑最窄测试，再跑相关回归集。
- 断言调用者可见的输出、副作用、边界和状态；源码字符串测试不能冒充行为证据。
- 每个关键测试说明能抓住的真实故障，并做一个轻量 mutation 判断。
- 遗留代码先补 characterization/regression test；test-after 不得伪称纯 TDD。

### 4.3 固定验证

有 Docker 时，每阶段至少执行：

```bash
docker compose config
docker compose build --no-cache
docker compose up -d
docker compose ps
docker compose run --rm api alembic upgrade head
docker compose run --rm api pytest <本阶段聚焦测试> -q
docker compose run --rm api pytest tests/unit tests/integration -q
```

E2E 建立后追加：

```bash
docker compose run --rm api pytest tests/e2e -q
```

还必须从 fresh volume 验证迁移和启动，检查日志无 import error/循环重启，并同步 README、`.env.example`、架构和 API 文档。

Docker 不可用时，把相关检查标记为 `BLOCKED-ENV`；可继续不依赖 Docker 的安全工作，但不得勾选相关退出条件。

### 4.4 完成声明

任务只有同时满足以下条件才可完成：生产实现存在；自动化测试证明核心行为；错误和安全边界有反向测试；必要日志/指标/审计已加入；文档同步；当前代码上有 fresh 验证结果。

## 5. 阶段总览

开发 Agent 先读 [执行总指南](docs/development/00-execution-guide.md)，再进入下表对应分册。

| 阶段 | 目标 | 具体实施指南 | 退出标志 |
|---|---|---|---|
| P0 | 从零审计与技术栈收敛 | 本计划 §6 | NATS/Qdrant/Locust 一致，基础 Compose/测试可运行 |
| P1 | 数据、安全与身份底座 | [基础与安全](docs/development/01-foundation-security.md) | 空库迁移、JWT/RBAC、租户隔离、脱敏、审计通过 |
| P2 | IM 与异步消息闭环 | [消息与会话](docs/development/02-messaging-and-session.md) | HTTP/WS、ACK、JetStream、worker、outbox、mock 闭环通过 |
| P3 | 会话、意图与工具安全 | [意图、工具与业务](docs/development/03-intent-tools-and-business.md) | 非法 LLM 输出不能触发工具 |
| P4 | Qdrant 知识问答 | [知识与 RAG](docs/development/04-knowledge-rag.md) | E2E-01、E2E-10 通过 |
| P5 | 财务查询 | [意图、工具与业务](docs/development/03-intent-tools-and-business.md) | E2E-02、03、07 通过 |
| P6 | 平台指令 | [意图、工具与业务](docs/development/03-intent-tools-and-business.md) | E2E-04、08、09 通过 |
| P7 | 提醒与人工转接 | [提醒与转人工](docs/development/05-reminders-and-handoff.md) | E2E-05、06 通过 |
| P8 | 可靠性与可观测 | [可靠性与可观测](docs/development/06-reliability-observability.md) | 故障注入、限流、死信、指标、trace 通过 |
| P9 | 测试、Locust 与评测 | [验证与交付](docs/development/07-verification-and-delivery.md) | 10 E2E、覆盖率、压测、50 条评测完成 |
| P10 | 文档、演示与最终验收 | [验证与交付](docs/development/07-verification-and-delivery.md) | 全新环境完整走查通过 |

## 6. P0：从零审计与技术栈收敛

### 6.1 步骤

1. 记录 Python、Docker/Compose、Git、端口和操作系统环境。
2. 枚举所有非空文件、入口、依赖、环境变量、Compose service、迁移和测试。
3. 运行静态 import 和现有测试，先记录原始失败，不先改代码。
4. 创建 `PROGRESS.md`，把此前第 1/2 步完成结论降为待验收。
5. 保留 `nats-py`，加入 `qdrant-client`、Locust 和测试依赖；移除 aio-pika、pgvector、k6 运行依赖。
6. 配置统一为 `NATS_URL`、`QDRANT_URL`；Settings、`.env.example` 和 Compose 一一对应。
7. ready 检查 PostgreSQL、Redis、NATS、Qdrant；live 只检查进程。
8. Compose 统一为 PostgreSQL 15、Redis 7、NATS JetStream、Qdrant、api、worker、scheduler 和 5 个 mock。
9. 同步 README、架构、Makefile、需求拆解中的工具特定描述。
10. 补配置消费与 ready 行为测试：依赖不可用应返回 503，而不是 import 崩溃。
11. 从空环境验证 Compose、迁移、健康检查和测试入口。

### 6.2 一致性门槛

当前运行面不得存在旧选型：

```bash
rg -n -i 'rabbitmq|aio[-_]?pika|pgvector|k6' \
  app mocks tests docker-compose.yml requirements.txt .env.example \
  README.md docs/architecture.md Makefile loadtests
```

预期零命中。`PLAN.md`、`PROGRESS.md`、历史 agent log 可保留旧名，但必须标为历史方案。

### 6.3 退出条件

- [ ] 所有入口可 import，不存在缺包或 Settings 字段缺失。
- [ ] `docker compose config`、fresh build、空库迁移成功。
- [ ] PostgreSQL、Redis、NATS、Qdrant 健康。
- [ ] live 返回 200；依赖全开时 ready=200；任一依赖关闭时 ready=503 且指出依赖。
- [ ] README、架构、环境变量、依赖和 Compose 只描述活动技术栈。
- [ ] `make test` 稳定运行，不因基础配置错误中断。

## 7. P1：数据、安全与身份底座

具体实现步骤、目标文件和反向测试见 [P1 实施指南](docs/development/01-foundation-security.md)。

### 7.1 范围

1. 审计并补齐 tenants、users、conversations、messages、outbox_events、audit_logs 等模型。
2. 为 `(tenant_id, message_id)` 和业务幂等键建立数据库唯一约束。
3. 实现 JWT；身份和 tenant 只能来自已验证 token。
4. 建立 `user / agent / admin` RBAC 和资源归属校验。
5. 建立集中式 PII 脱敏及日志过滤。
6. 迁移既能从空库运行，也能从上一版本升级。

### 7.2 测试与出口

- [ ] 租户 A 无法读取租户 B 的用户、会话、消息和审计。
- [ ] 普通用户不能执行坐席或管理员操作。
- [ ] 请求体/LLM 伪造身份不能覆盖认证身份。
- [ ] 邮箱、手机号、银行卡、身份证按 literal 期望脱敏。
- [ ] 日志和审计不含完整 token、密码和 PII。
- [ ] 删除 tenant filter 会使至少一个隔离测试失败。

## 8. P2：IM 与 NATS 异步消息闭环

具体实现步骤、消息可靠性和崩溃恢复测试见 [P2 实施指南](docs/development/02-messaging-and-session.md)。

### 8.1 范围

1. 定义版本化 `EventEnvelope`：event_id、trace_id、tenant_id、occurred_at、schema_version、type、payload。
2. 实现 HTTP webhook 与 WebSocket 入站。
3. 同一事务写消息、幂等状态和 outbox，提交成功后快速返回 durable ACK。
4. relay 收到 JetStream publish ACK 后才把 outbox 标为 published。
5. 建立 stream、subjects、durable consumers、显式 ACK/NAK、最大投递次数和死信主题。
6. worker 消费 `im.inbound`，调用 mock LLM，通过 outbox 产生 `im.outbound`。
7. mock IM 保存推送，支持在线、离线和重连查询。
8. WebSocket 支持 `reply.start/chunk/end/error` 与游标补取。
9. trace_id 贯穿 API → outbox → NATS → worker → downstream。

### 8.2 测试与出口

- [ ] mock LLM 延迟 5 秒时 ACK 仍满足阈值。
- [ ] 数据库提交后 relay 崩溃，重启可发布 pending outbox。
- [ ] worker 成功后才 ACK，异常退出后可重投。
- [ ] 重投不产生重复回复或副作用。
- [ ] 多连接收到相同出站事件，游标重连可补取遗漏消息。
- [ ] 最大投递次数后进入死信并可查看原因。
- [ ] HTTP 与 WebSocket 各有集成测试，完整异步回复可在 mock IM 观察。

## 9. P3：会话、意图与工具安全

P3/P5/P6 共用的安全执行管线见 [意图、工具与业务实施指南](docs/development/03-intent-tools-and-business.md)。

1. Redis 保存最近 N 轮上下文，PostgreSQL 保存完整历史和摘要。
2. 意图包括平台指令、知识问答、提醒、财务、闲聊、人工、高风险和未知。
3. 高确定性规则优先，mock LLM 兜底；输出固定 Schema。
4. 工具注册表包含参数 Schema、权限、风险、超时和幂等策略。
5. 执行管线固定为：Schema → allowlist → 鉴权 → 资源归属 → 风险确认 → 执行。
6. 高风险确认绑定 user、conversation、action、resource 和过期时间，默认 5 分钟。

退出条件：

- [ ] 非法 JSON、未知工具、缺参、越权参数均使工具调用次数为 0。
- [ ] Prompt Injection 不能覆盖策略、身份或 allowlist。
- [ ] 低置信度会澄清或转人工。
- [ ] 未确认、错误用户、模糊确认和过期确认都不执行。
- [ ] Redis 故障有明确降级，完整历史不丢。

## 10. P4：Qdrant 知识问答

知识导入、租户安全适配器、证据门控和引用校验见 [P4 实施指南](docs/development/04-knowledge-rag.md)。

1. 提供文档导入和重新索引命令/API。
2. 共享 collection payload 含 tenant_id、document_id、title、source、version。
3. 为 tenant_id 建 keyword index；所有查询强制 tenant filter。
4. 使用确定性 embedding/mock，测试不依赖付费模型。
5. 命中回答带来源；无命中或低分使用固定拒答。
6. 用户输入和知识片段只作为数据，不作为可执行系统指令。

退出条件：

- [ ] 租户 A 检索不到租户 B 内容；删除 tenant filter 会使测试失败。
- [ ] 有命中返回正确来源；无命中不编造并建议转人工。
- [ ] E2E-01、E2E-10 通过。

## 11. P5：财务查询

财务授权、前置脱敏、下游错误映射和 E2E 设计见 [P3/P5/P6 实施指南](docs/development/03-intent-tools-and-business.md)。

1. 支持订单、账单、发票、退费进度和余额。
2. 身份只取 JWT；调用前检查 RBAC、tenant 和资源归属。
3. 财务响应进入 LLM 前完成脱敏。
4. 超时、500、空响应使用确定性降级，禁止生成金额或状态。
5. 审计记录操作者、目标、查询类型、结果和 trace，不保存完整敏感值。

退出条件：E2E-02 正常查询和脱敏、E2E-03 越权 403 与审计、E2E-07 超时不编造全部通过；日志、审计和 LLM 输入无完整 PII。

## 12. P6：平台指令

工具注册、二次确认、幂等执行和 mock-platform 契约见 [P3/P5/P6 实施指南](docs/development/03-intent-tools-and-business.md)。

1. 接入 mock platform：自动续费、课程提醒、课程表、请假、学习报告。
2. 写操作使用业务幂等键。
3. 高风险操作进入确认状态机，确认前不得调用下游。
4. 只重试可恢复错误，指数退避加抖动；不可恢复错误立即失败。
5. 成功、失败、超时和重试均写审计并返回明确下一步。

退出条件：确认前调用次数为 0；确认后只执行一次；重复确认不重复；E2E-04、08、09 通过。

## 13. P7：提醒与人工转接

提醒调度、occurrence 幂等、人工状态机和 AI 抑制见 [P7 实施指南](docs/development/05-reminders-and-handoff.md)。

### 13.1 提醒

- 支持创建、修改、取消、查询；保存原时区，数据库统一 UTC。
- 支持单次、每天、每周、工作日和提前提醒。
- scheduler 使用 `FOR UPDATE SKIP LOCKED` 或等价租约抢占。
- occurrence 单独幂等，发布 `reminder.due`；失败有限重试后死信。

### 13.2 人工转接

- 明确“转人工”立即触发；连续两次不满触发，并定义计数重置条件。
- 转接包含摘要、意图、已尝试操作、失败原因和风险提示。
- 在线返回排队/接入；离线返回等待或留言；不得泄露不必要 PII。

退出条件：短时提醒 5 秒内送达；scheduler/worker 重启后不丢不重；在线/离线均有测试；E2E-05、06 通过。

## 14. P8：可靠性与可观测

错误分类、限流、超时/重试/熔断、指标、trace 和故障矩阵见 [P8 实施指南](docs/development/06-reliability-observability.md)。

1. tenant/user 双维度限流，返回明确 429 或降级事件。
2. LLM、平台、财务独立超时、有限重试和熔断。
3. 指标包含 QPS、P50/P95/P99、错误率、NATS 积压/重投/死信、LLM 和工具调用。
4. JSON 日志含 trace_id、tenant_id、conversation_id、message_id，并过滤敏感信息。
5. OpenTelemetry 覆盖 API → NATS → worker → downstream。

故障矩阵：LLM 延迟/500/非法 JSON、finance 超时/500、Redis 重启、NATS 暂时不可用、队列积压。每项必须有自动化测试或可重复脚本，并能通过日志、指标或 trace 定位；不得无限重试。

## 15. P9：全量测试、Locust 与评测

10 个 E2E 的可观察断言、覆盖率、Locust 和 50 条评测集见 [P9/P10 实施指南](docs/development/07-verification-and-delivery.md)。

### 15.1 测试

- 单元：路由、权限、租户、幂等、脱敏、确认、时间规则、重试、拒答。
- 集成：PostgreSQL、Redis、NATS、Qdrant、mock LLM、mock finance、scheduler。
- E2E：`项目需求拆解.md` 的 E2E-01 至 E2E-10 全部自动化。
- 核心模块覆盖率 ≥ 70%，覆盖率不替代安全反向测试。

### 15.2 Locust

场景：500 WebSocket 连接；200 msg/s 持续 5 分钟；1000 msg/s 突发 30 秒并观察恢复；财务 100 QPS；LLM 超时率 20%。P2 先完成 50 连接 spike，验证 WebSocket client/gevent 兼容；如用插件必须锁版本。

报告包含机器环境、容器资源、参数、QPS、P50/P95/P99、错误率、积压、CPU/内存、瓶颈和未达标项，禁止隐藏测试条件。

### 15.3 LLM 评测

建立 50 条固定数据集，覆盖知识问答、越权、确认、无依据拒答、不满和转人工；输出事实准确率、引用命中率、越权拒绝率、少 AI 味评分和转人工准确率。

退出条件：`make test` 全绿、10 E2E 全绿、覆盖率达标、`make loadtest` 可复现、50 条评测报告可重复生成。

## 16. P10：文档、演示与最终验收

API/README/报告要求、fresh 环境 Gate、仓库卫生和演示流程见 [P9/P10 实施指南](docs/development/07-verification-and-delivery.md)。

### 16.1 交付物

- README：架构、启动、配置、迁移、测试、压测、演示和排障。
- Compose、`.env.example`、Makefile、迁移、架构图、API/事件契约。
- unit/integration/E2E/fault/load tests。
- Locust 报告、LLM 评测报告、已知问题。
- agent 使用记录：关键 prompt、影响模块、人工复核和重写部分。
- seed 数据、演示账号、`make demo` 和 10–15 分钟演示脚本。

### 16.2 最终门槛

- [ ] `docker compose config` 和 `make up` 在全新环境成功。
- [ ] 空库迁移、live/ready 健康检查正确。
- [ ] `make test` 全部通过，核心覆盖率 ≥ 70%。
- [ ] E2E-01 至 E2E-10 全部通过。
- [ ] 故障注入、Locust 报告和 50 条评测完成。
- [ ] 仓库无密钥、真实 PII、`.env`、缓存和虚拟环境。
- [ ] README、架构、API、报告、agent log、已知问题和演示材料齐全。
- [ ] 活动代码、配置和文档只有 NATS + Qdrant + Locust 一套技术栈。

## 17. 五天压缩安排

| 时间 | 工作 | 当日硬结果 |
|---|---|---|
| 第 1 天上午 | P0 | fresh Compose、迁移、健康检查和测试入口成立 |
| 第 1 天下午 | P1 + P2 | 消息异步回复，重复消息仅处理一次 |
| 第 2 天 | P3 + P4 + P5 + P6 | E2E-01/02/03/04/07/08/09/10 可运行 |
| 第 3 天 | P7 + P8 | 10 E2E 可运行，提醒、人工、故障和指标成立 |
| 第 4 天 | P9 测试与压测 | 覆盖率、故障测试、Locust 和报告完成 |
| 第 5 天 | P9 评测 + P10 | 评测、文档、演示和全新环境验收完成 |

工期不足时优先：P0/P1/P2 → 10 个 E2E → 安全可靠性 → 压测评测 → 加分项。不得删除租户隔离、确认、幂等、拒答或持久提醒换取表面完成。

## 18. 接手 agent 第一轮操作

1. 确认没有其他写入进程。
2. 读取需求拆解、本计划、README、架构和 agent log。
3. 检查 Git、Python、Docker/Compose 环境。
4. 创建 `PROGRESS.md`；不继承 README 完成勾选。
5. 运行现有测试和静态 import，记录原始失败。
6. 列出技术栈影响面：配置、依赖、Compose、健康检查、入口、worker、mock、文档和测试。
7. 只执行 P0；P0 全绿前不得进入业务开发。
8. P0 后按 P1 → P10 推进，每个行为使用 RED/GREEN。

第一轮结束必须向用户报告：当前基线是否可运行、冲突文件及处理决定、实际验证命令与结果、下一阶段风险，以及 Docker/端口/外部环境 blocker。
