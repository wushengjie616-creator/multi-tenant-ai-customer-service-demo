# 架构设计与冻结决策

> 本文是 `项目需求拆解.md` §17「开工前需要冻结的实现决策」的落地，技术栈采用已确认的 NATS JetStream + Qdrant + Locust（见 PLAN.md §2）。后续开发以此为准，变更需显式更新本文。

## 1. 技术栈（冻结）

| 维度 | 选择 |
|---|---|
| 语言 / 框架 | Python 3.12 + FastAPI（异步） |
| 数据库 | PostgreSQL 15 |
| 缓存 / 分布式限流 | Redis 7 |
| 消息队列 | NATS JetStream + nats-py（durable consumer、显式 ACK、至少一次投递、有限重试 + 死信） |
| 向量库 | Qdrant + qdrant-client（共享 collection，写入与查询强制 `tenant_id`） |
| 后台任务 | 独立 worker 进程消费 NATS JetStream；scheduler 扫描到期提醒并发布 |
| 数据校验 | Pydantic v2（JSON Schema） |
| 迁移 | Alembic（sync 驱动 psycopg2 跑迁移，async 驱动 asyncpg 跑应用） |
| 测试 | pytest + pytest-asyncio |
| 压测 | Locust |
| 可观测 | Prometheus 指标 + JSON 结构化日志 + OpenTelemetry OTLP/HTTP Collector |
| API 文档 | FastAPI OpenAPI |
| 本地演示前端 | FastAPI StaticFiles + 原生 HTML/CSS/JS（无独立构建链） |

### 1.1 Redis 职责与一致性边界

```text
PostgreSQL（完整 message history，事实源）
       │
       │ Redis miss / expired / error 时按最近 N 条重建
       ▼
Redis（recent N messages + sliding TTL，仅热副本）
       │
       ▼
assistant_service -> LLM（历史正序 + 当前 user message）
```

- 上下文只保存固定 message 窗口，不实现动态扩窗、向量历史检索或长期 memory。
- key 必须同时包含 tenant 与 conversation：`context:{tenant_id}:{conversation_id}`。
- 默认保留最近 20 条 message，每次成功写入刷新 3600 秒 TTL。
- user message 已由入站事务提交到 PostgreSQL；assistant message 与出站 outbox 提交成功后，worker 才更新 Redis。Redis 写失败不会反向影响已提交的业务事实。
- Redis miss 从 PostgreSQL 恢复最近窗口并回填；Redis error 跳过回填、直接使用 PostgreSQL 历史，不做无限重试。
- worker 对发生 Redis 读写错误的 conversation 保留进程内 dirty 标记；连接恢复后的下一次访问强制从 PostgreSQL 重建，避免命中故障前的陈旧 key。Compose 关闭 RDB/AOF，因此 Redis 容器重启后天然以 cache miss 恢复。
- tenant/user 固定窗口限流使用一个 Lua 脚本原子执行两组 `INCR + TTL/EXPIRE`，避免产生永久计数 key。普通聊天在 Redis 故障时 fail-open；`/health/live` 不受影响，`/health/ready` 明确返回 Redis 错误。
- Redis 连接池上限由 `REDIS_MAX_CONNECTIONS` 配置；Compose 默认不向宿主机发布 Redis 端口。

| Key Pattern | 用途 | 类型 | TTL |
|---|---|---|---|
| `context:{tenant_id}:{conversation_id}` | 最近会话消息 | List（JSON message） | 默认 3600 秒，写入刷新 |
| `rate:tenant:{tenant_id}:{minute_window}` | 租户固定窗口计数 | String/Integer | 70 秒 |
| `rate:user:{tenant_id}:{user_id}:{minute_window}` | 用户固定窗口计数 | String/Integer | 70 秒 |

## 2. 队列与重试 / 死信策略

- 入站 subject `im.inbound`，出站 `im.outbound`，提醒 `reminder.due`，重试 `tool.retry`，死信 `deadletter.*`。入站 Worker 使用 pull batch 和有界并发，同一 tenant/conversation 在同一批内串行。
- 使用 JetStream stream + durable consumer + 显式 ACK/NAK + 最大投递次数（max deliver）+ 指数退避重试。
- 至少一次投递；outbox 发布时用 `event_id` 作为 JetStream `Nats-Msg-Id` 压缩崩溃窗口的重复事件，消费端再以业务幂等键（如 `tenant_id + message_id`、`reminder occurrence id`）保证恰好一次业务效果。
- Outbox relay 用 `FOR UPDATE SKIP LOCKED` 批量领取，按 `OUTBOX_PUBLISH_CONCURRENCY` 有界并发发布，并在单事务内批量确认成功项；部分失败仍逐项记录错误与退避。默认 batch=100、并发=20、poll=20ms。
- `im.outbound` 同时被 NATS Core 非 queue-group subscription 广播到每个 API 副本，仅持有目标本地连接的副本推送；断线恢复仍以 PostgreSQL 为事实源。
- 仅重试可恢复错误，指数退避 + 抖动，超过最大投递次数后进入死信 subject 并计数指标。禁止无限重试。

## 3. WebSocket 事件格式（冻结）

- 连接：`WS /ws`。客户端事件：`message.send`、`message.ack`、`connection.resume`。
- 服务端事件：`message.accepted`、`reply.start`、`reply.chunk`、`reply.end`、`reply.error`、`reminder.triggered`、`handoff.status`。
- LLM 的 `reply.chunk` 来自上游 SSE 真实 token 流，经 `im.outbound.stream` 的 NATS Core 广播；完整回复才进 PostgreSQL + outbox + JetStream，兼顾首 token 延迟与断线恢复。
- 统一事件封装 `EventEnvelope`：`event_id`、`trace_id`、`traceparent`、`tenant_id`、`occurred_at`、`schema_version`、`type`、`payload`。`traceparent` 用于 API→NATS→worker 的 W3C trace 上下文续接。
- 入站消息最小字段：`message_id`、`tenant_id`、`user_id`、`conversation_id`、`content`、`timestamp`。见 `app/schemas/message.py`。

## 4. 身份、角色与租户来源（冻结）

- 认证：JWT（HS256，开发用 `JWT_SECRET`；生产换非对称并外部注入）。
- JWT claim：`sub`（user id）、`tenant_id`、`role`、`exp`。
- 角色（RBAC）：`user` / `agent`（坐席）/ `admin`。
- 租户身份**只取自已验证身份的上下文**，禁止信任 LLM 生成的身份字段（NFR3-06 / FR6-02）。
- 所有数据访问强制携带 `tenant_id` 条件；关键表复合唯一约束（`users(tenant_id,id)`、`messages(tenant_id,message_id)`）。

## 5. 高风险操作清单与确认有效期

- 高风险操作：关闭自动续费（当前清单最小集；后续增删在此记录）。
- 状态机：`proposed → pending_confirmation → confirmed → executing → succeeded/failed`，另可 `expired` / `cancelled`。
- 二次确认绑定：`user_id`、`conversation_id`、`action`、`resource`、`expires_at`。仅同一已认证用户在过期前明确确认才可执行；模糊回复或超时不得执行。
- 确认有效期：**5 分钟**（可配置）。

## 6. 提醒调度与时间解析

- 时区：原始时区默认 `Asia/Shanghai`，数据库统一存 UTC；重复规则先在租户 IANA 时区的本地墙钟上推进，再转回 UTC，避免 DST 漂移和 UTC 跨日误判。
- 调度器扫描 `next_run_at <= now` 的到期提醒，以行锁/租约抢占，支持多实例。
- 每条提醒的每次 occurrence 单独幂等（`reminder_deliveries` 表），避免重启或并发重复推送。
- 当前 API 接受明确的本地 ISO 时间 + IANA 时区；自然语言可解析为“待确认候选”，只有用户点击确认后才创建提醒，歧义时间要求澄清，不直接产生副作用。

## 7. Mock 故障注入控制方式

- 所有 mock 通过 HTTP 头或查询参数控制行为（如 `X-Mock-Delay-Ms`、`X-Mock-Status`、`X-Mock-Body`），具体在第 2/3 步 mock 实现时落地并写入 README。
- 故障注入只影响 mock，不改变真实依赖接口契约。

## 8. 压测环境与指标采集

- 压测机器：本机或独立容器；Locust 场景见 `loadtests/`。
- 指标：Prometheus 采集 `/metrics`；报告需含 QPS、P50/P95/P99、错误率、队列积压、CPU/内存。

## 9. 处理链路

```text
IM Client -> API 鉴权与基础校验 -> message_id 去重登记 -> 快速返回 ACK
  -> NATS im.inbound -> Worker 加载会话上下文 -> 意图分类与风险判定
  -> RAG / 平台工具 / 提醒 / 财务 / 人工转接 -> 结果安全检查与话术生成
  -> NATS im.outbound -> mock-im 推送 -> 保存消息/审计/指标/trace
```

ACK 路径不等待 LLM 或下游工具（FR1-03 / NFR1-02）。

### 9.1 实体增强知识链路

```text
文档分块 -> DeepSeek 多表示抽取 -> 实体/别名/主题/摘要/建议问题/search_text
                    \-> 超时/限流/格式错误 -> 确定性规则降级 -> 继续入库

问题 -> 安全业务意图优先 -> DeepSeek 查询改写/同义扩展（失败则规则降级）
     -> 其余知识问题全部进入 RAG
     -> 实体/别名 + 租户内中文子串兜底 + 向量候选
     -> 常规证据不足时对有上限的租户分块做 DeepSeek 语义证据选择
     -> 服务端证据门控 -> LLM 话术 -> 服务端引用
```

- Qdrant 实体字段使用 keyword payload index，并与 `tenant_id/visibility/review_status` 强制过滤同时使用。
- Qdrant 1.9 的 multilingual text tokenizer 对连续中文短语不是可靠契约；全文兜底在限定上限内 scroll 当前租户候选，再本地做无空白中文子串匹配。默认最多 500 个分块，不把它声称为大规模搜索方案。
- 查询实体抽取与文档实体抽取使用同一套“LLM 优先、确定性规则失败开放”策略；抽取失败只降低召回增强能力，不会中断问答。
- 语义证据选择最多读取 `RAG_SEMANTIC_CANDIDATE_LIMIT` 个当前租户公开、已审核、有效分块，只接受候选中已有的 `chunk_id`；空结果和模型异常均保持拒答。
- DeepSeek 的结构化索引、查询改写和证据选择显式关闭 thinking，避免推理 token 挤占短 JSON 输出；最终客服话术仍使用常规模型配置。
- 所有真实 LLM 调用共用 Redis 准入门：最多 600 个已发出的在途请求，滚动 1 秒最多启动 20 个。超额请求在应用内等待，不会提前向 DeepSeek 建立 TCP 连接；在途槽位带租约。Redis 不可用时回退到进程内有界准入，保留单实例的并发/速率保护，但不宣称跨实例全局上限。
- 每次 LLM 调用将 prompt/completion token 与会话、租户关联入库；租户管理端只能汇总本租户会话的用量与按环境单价计算的成本。
- 客户建议问题来自同一租户分块的 `suggested_questions`，不使用跨租户或前端写死的问题模板。
- `unknown` 表示“未命中业务路由”，不再表示“立即拒答”；只有明确人工请求才创建 handoff。
- 租户文档清单继续以 Qdrant 为数据源：完整原文只存于首分块，列表按 token tenant 与 `document_id` 聚合；不为本地演示重复引入 PostgreSQL 文档表或对象存储。

## 10. 关键设计取舍

- **LLM 无执行权限**：LLM 只理解或生成建议；写操作与敏感数据用确定性代码完成鉴权、校验、幂等、审计（§2.4）。
- **安全意图路由**：高确定性规则优先，未知输入 abstain；LLM 仅生成无副作用闲聊或基于已检索证据的话术，不拥有工具权限。知识引用由服务端拼接，LLM 失败时回退至抽取式证据答案。
- **脱敏前置**：所有外部 LLM 请求在 HTTP provider 边界统一扫描邮箱、手机、银行卡和身份证；财务结果在此前仍先进行递归字段脱敏（FR6-06）。
- **一份镜像多入口**：Compose 只构建 `education-ai-service-app:latest` 一次，api/worker/scheduler/mocks 用不同 command 复用该镜像。
- **显式演示边界**：`demo-bootstrap` 幂等运行迁移、固定身份与示例知识导入；可写租户工作台与只读客户样例使用不同 `tenant_id`，客户 JWT 只能检索固定样例库。一键客户会话只在 `DEMO_MODE=true` 时存在，非演示环境必须关闭。
