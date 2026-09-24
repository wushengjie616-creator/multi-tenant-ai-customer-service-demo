# P2 IM、会话与 NATS 异步闭环实施指南

## 1. 本阶段目标

让 HTTP 和 WebSocket 入站在持久化后快速 ACK，由 Outbox 可靠发布到 NATS，worker 幂等消费并通过出站事件把回复送到 mock IM 和在线 WebSocket。

完成标志不是“能发布消息”，而是数据库提交、进程崩溃、重复投递和断线重连场景都不丢且不产生重复业务效果。

## 2. 依赖与目标文件

前置：P1 迁移、可信身份、租户查询和审计已通过。

```text
app/schemas/{event,message}.py
app/models/{message,outbox}.py
app/services/{conversation_service,outbox_relay}.py
app/core/nats.py
app/api/{messages,websocket}.py
app/workers/message_worker.py
mocks/mock_{im,llm}/main.py
tests/unit/test_events.py
tests/integration/test_{message_ingest,outbox,nats_worker,websocket}.py
```

## 3. 冻结契约

### 3.1 EventEnvelope

所有跨进程事件必须含：

```text
event_id, trace_id, tenant_id, occurred_at,
schema_version, type, payload
```

`event_id` 标识一次事件；业务幂等键放在 payload，如 `tenant_id + message_id`。消费者先校验 envelope 和 payload Schema，再产生副作用。

### 3.2 Subject 与 durable

- `im.inbound`：入站消息；
- `im.outbound`：可推送回复；
- `reminder.due`：提醒实例；
- `tool.retry`：确需异步重试的工具任务；
- `deadletter.*`：终止失败。

stream 初始化必须幂等，consumer 使用稳定 durable 名，成功完成业务事务后 ACK，暂时性失败 NAK，永久非法消息进入死信后 ACK 原消息。

## 4. 实现顺序

### 4.1 入站事务

`conversation_service.ingest_message` 在单个 PostgreSQL 事务中：

1. 验证会话属于认证 tenant/user；
2. 插入 user message，依赖 `(tenant_id,message_id)` 唯一约束去重；
3. 新建 `im.inbound` outbox；
4. commit；
5. 返回 `202 accepted` 或已有状态。

API 路径不直接调用 LLM、Qdrant、NATS 或业务工具。发生唯一键竞争时读取已有记录，不再创建第二个 outbox。

### 4.2 Outbox relay

Outbox 至少具备 `pending/publishing/published/failed/cancelled`、attempts、next_attempt_at、lease/updated_at、last_error：

- `FOR UPDATE SKIP LOCKED` 原子抢占；
- 抢占后提交，再发送 NATS；
- 收到 JetStream publish ACK 才标 `published`；
- 卡死 `publishing` 超过 lease 可回收；
- 达最大次数转 `failed` 并发布/记录死信；
- 错误内容截断并脱敏。

禁止永久把失败事件放回 pending 而没有次数和退避上限。

### 4.3 Worker 消费

worker 顺序：

```text
解析/校验事件
→ 查询入站消息并原子取得 processing 权
→ 加载上下文
→ 调用当前阶段的 mock LLM
→ 同事务保存 assistant message、完成入站状态、写 im.outbound outbox
→ commit
→ ACK inbound
```

在 commit 前崩溃应被重投；commit 后 ACK 前崩溃，重投应因消息状态/唯一键而跳过，不产生第二条回复。

### 4.4 WebSocket 与重连

- 握手使用 `Authorization: Bearer <JWT>` header 认证，conversation 必须属于当前 tenant；不通过 query 参数传 token，也不能信任 query 中的 tenant/user。
- 事件使用完整 `EventEnvelope`，不额外维护另一套裸 JSON 格式。
- 同一 conversation 的多个连接都收到相同出站事件。
- `connection.resume` 带游标或最后 event/message 标识，从 PostgreSQL 补取遗漏事件。
- 回复事件固定 `reply.start/chunk/end/error`；即使 mock 只返回整段，也按 start→chunk→end 顺序模拟。
- 内存连接表仅负责在线 fan-out，不作为可靠消息存储。

### 4.5 Redis 会话上下文

- key 为 `context:{tenant_id}:{conversation_id}`，不得省略 tenant。
- 固定保存最近 `CONTEXT_MAX_MESSAGES` 条 message（默认 20），不以含义模糊的 turn 计数。
- 每次写入执行 append + trim + `EXPIRE`，默认 TTL 为 `CONTEXT_TTL_SECONDS=3600`，只做随活跃刷新的 sliding expiration，不动态扩大窗口。
- cache miss 从 PostgreSQL 按时间正序恢复并回填；Redis 故障直接使用 PostgreSQL 最近历史，不无限重试、不丢持久消息。
- Redis 读写失败时将该 conversation 标记为待重建；连接恢复后的下一次访问不信任旧 key，强制从 PostgreSQL 回填。Compose 不启用 RDB/AOF。
- worker 在 assistant message 与出站 outbox 已提交 PostgreSQL 后，才把当前 user/assistant 消息写入 Redis；Redis 不进入事实源事务。
- LLM 输入为同 tenant、同 conversation 的历史 context 加当前 user message，并按 role 正序排列；当前 message 不重复加入。

## 5. Mock 要求

`mock-llm`：确定性回复，并支持 header/query 控制 delay、500、非法 JSON。

`mock-im`：保存收到的出站事件，提供按 tenant/conversation 查询、在线/离线状态、WebSocket 观察和清理测试数据的接口；调用记录需能断言幂等。

故障开关只改变响应行为，不改变正式接口 Schema。

## 6. 必须验证的行为

- mock LLM 延迟 5 秒时，入站 ACK P95 仍不等待它。
- 相同 message_id 并发两次只产生一条消息、一条 inbound outbox 和一次业务回复。
- 数据库 commit 后 relay 崩溃，重启可继续发布。
- publish 成功但标记前崩溃，重投仍不产生重复业务效果。
- worker 在业务 commit 前退出，JetStream 重投后完成。
- 非法 envelope 不调用 LLM，进入明确失败/DLQ。
- 两个 WS 连接均收到回复；断线后按游标补齐。
- Redis 不可用时仍可从 PostgreSQL 完成最小回复。

## 7. 验证命令与退出清单

```bash
./.venv/bin/python -m pytest tests/unit/test_events.py -q
./.venv/bin/python -m pytest tests/integration -k 'message or outbox or nats or websocket' -q
docker compose run --rm api pytest tests/integration -k 'message or outbox or nats or websocket' -q
make demo
```

- [ ] HTTP 和 WS 均使用认证身份并快速 ACK。
- [ ] 入站和出站都通过事务型 outbox。
- [ ] relay 有 lease、有限重试、失败终态。
- [ ] worker commit/ACK 顺序有崩溃恢复测试。
- [ ] WS 多连接和游标补取有集成测试。
- [ ] `trace_id` 贯穿 API→DB→NATS→worker→outbound。
- [ ] `docs/api.md` 记录 HTTP、WS 和事件示例。
