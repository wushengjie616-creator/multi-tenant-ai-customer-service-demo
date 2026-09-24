# 第 6/7/8 项交付优化方案（暂不实施）

> 状态：设计完成，尚未实施
>
> 范围：容量与消费追平、WebSocket 横向扩展、Prometheus 指标与告警
>
> 非本轮范围：原生 LLM 流式、RAG 多轮指代、人工摘要增强、Mock 协议扩展

## 1. 目标与边界

本文只冻结后续实施方案和验收口径，不修改当前运行代码。三个问题必须分阶段处理，避免用“堆实例”掩盖顺序、幂等或连接路由问题。

| 编号 | 当前问题 | 目标 |
| --- | --- | --- |
| 6 | 200 msg/s 入口达标但 worker 积压；1000 msg/s 入口未达标 | 200 msg/s 持续 5 分钟可追平；1000 msg/s 30 秒无丢失并在约定时间排空 |
| 7 | 多 API 实例共享固定 outbound durable，消息可能落到没有目标连接的实例 | 任意 API 实例接收的出站事件都能送达持有对应 WebSocket 的实例 |
| 8 | Prometheus 仅有部分 HTTP 指标，工具/死信 Counter 未接线 | 覆盖 QPS、延迟、错误、积压、Token、工具成功率、熔断和死信，并具备基础告警 |

## 2. 第 6 项：高并发容量与消费追平

### 2.1 已知基线

- 500 WebSocket：500/500，0 失败。
- 200 msg/s 稳定入口：199.12 msg/s，ACK P95 150ms，入口通过。
- 稳定流结束时：52,836 pending + 1,000 ack pending，消费端未追平。
- 1000 msg/s 突发：370.90 msg/s，P95 3.9s，未达到目标。
- 测试环境：2 vCPU / 约 3.81 GiB，低于题目建议 8C16G。

### 2.2 推荐架构

将消息处理拆成三条成本不同的路径：

1. **确定性快速路径**：固定导航、缓存命中、权限拒绝、纯规则指令；不得进入 LLM 排队池。
2. **RAG 路径**：检索与证据门独立并发，只有需要润色时才进入 LLM。
3. **LLM 路径**：继续使用全局 600 在途 + 20 starts/s，作为供应商保护上限，而不是 worker 并发上限。

Worker 采用有界并发：

- NATS pull consumer 批量拉取；
- worker 内使用 semaphore 控制处理并发；
- 以 `conversation_id` 计算分区键，同一会话串行、不同会话并行；
- 数据库提交批次与外部 HTTP 等待解耦；
- 对固定回复和重复 RAG 查询增加短 TTL 缓存；
- 保留 message lease、outbox 和业务幂等，不通过关闭安全门换吞吐。

### 2.3 分阶段实施

**阶段 A：测量而不改语义**

- 增加每阶段耗时：队列等待、DB、分类、RAG、LLM gate、LLM HTTP、outbox。
- 记录消息进入/完成时间和 backlog drain rate。
- 用现有数据确定 CPU、数据库连接池或 20 starts/s 中哪个是主瓶颈。

**阶段 B：单实例并发化**

- Pull batch 建议从 20 起步；worker 并发从 16/32/64 做阶梯实验。
- 数据库 pool size 与 worker 并发绑定，避免连接池成为隐藏队列。
- 同会话加锁或分区，验证回复顺序不乱。

**阶段 C：横向扩容**

- 在 8C16G 固定环境使用 2/4/8 个 worker 实例重跑。
- 入口 API 和消费 worker 分开扩容。
- 记录每档吞吐、积压峰值、排空时间、CPU、内存和数据库连接数。

### 2.4 验收标准

- 200 msg/s × 5 分钟：ACK P95 < 300ms、错误率 < 1%、停止发送后 60 秒内积压归零。
- 1000 msg/s × 30 秒：入口达到目标量级、消息无丢失、停止发送后在报告约定时间内排空。
- 同一 `conversation_id` 回复顺序稳定。
- 重投不产生重复提醒、重复平台指令或重复财务审计副作用。
- 报告包含 QPS、P50/P95/P99、错误率、积压、排空时间、CPU、内存和数据库连接池。

## 3. 第 7 项：WebSocket 多实例广播

### 3.1 根因

当前每个 API 实例都使用固定 durable `im-outbound-api` 消费出站主题。JetStream 的这一使用方式是竞争消费：一条事件只会交给一个实例。如果客户连接保存在另一个 API 实例的内存中，收到事件的实例找不到该连接，消息不会到达客户端。

### 3.2 推荐方案：JetStream 持久化 + NATS Core 实例广播

保留 JetStream 作为可靠事件源，增加一个出站分发层：

```text
worker -> JetStream im.outbound
       -> outbound dispatcher（单组可靠消费）
       -> NATS Core ws.broadcast
       -> 每个 API 实例独立订阅
       -> 本实例 ConnectionManager 命中后推送
```

关键规则：

- dispatcher 只有在成功广播后 ACK JetStream；
- 每个 API 实例使用实例级 inbox/普通 Core subscription，不共享 queue group；
- 广播事件保留 `event_id`，客户端和 API 做短期去重；
- 客户端断线仍通过 PostgreSQL 消息历史和 `after_message_id` 恢复，不能只依赖瞬时广播；
- ConnectionManager 只负责本实例连接，不假装是全局连接注册表。

### 3.3 备选方案

Redis Pub/Sub 也可作为广播背板，但当前项目已经依赖 NATS；继续使用 NATS Core 能减少新增运行组件。若未来需要跨地域在线状态、连接路由和 presence，再评估专用 WebSocket Gateway。

### 3.4 验收标准

- 启动至少 3 个 API 实例，客户连接随机落在任意实例。
- 从任一入口发送消息，目标连接均收到且只收到一次业务结果。
- 同一会话同时打开两个客户端，两端都收到同步事件。
- 随机重启一个 API 实例，其他实例连接不受影响；重连客户端通过游标补齐。
- dispatcher 在广播失败时重试，不提前 ACK JetStream。

## 4. 第 8 项：Prometheus 指标与告警

### 4.1 指标清单

建议增加并实际接线：

| 指标 | 类型 | 标签建议 |
| --- | --- | --- |
| `eduai_messages_received_total` | Counter | channel、outcome |
| `eduai_message_processing_seconds` | Histogram | stage、outcome |
| `eduai_queue_pending` | Gauge | stream、consumer |
| `eduai_queue_ack_pending` | Gauge | stream、consumer |
| `eduai_llm_requests_total` | Counter | provider、model、outcome |
| `eduai_llm_request_seconds` | Histogram | provider、model |
| `eduai_llm_tokens_total` | Counter | tenant、model、token_type |
| `eduai_llm_gate_inflight` | Gauge | gate |
| `eduai_tool_calls_total` | Counter | tool、outcome |
| `eduai_circuit_breaker_state` | Gauge | downstream |
| `eduai_dead_letters_total` | Counter | subject |
| `eduai_outbox_pending` | Gauge | status |
| `eduai_handoff_pending` | Gauge | tenant |

`tenant_id` 只用于确有租户运营价值的低基数业务指标；HTTP path、错误文本、message_id、conversation_id 不得作为 Prometheus label，防止高基数爆炸和隐私泄漏。

### 4.2 采集方式

- 请求/工具/LLM 指标在实际调用边界递增，不只定义 Counter。
- NATS backlog 由独立后台采集器每 5–10 秒读取 consumer info 并更新 Gauge。
- outbox/dead-letter/handoff Gauge 使用低频 SQL 聚合，避免每次 `/metrics` 触发昂贵查询。
- Token 成本仍以 PostgreSQL 为结算事实源，Prometheus 用于趋势与告警，两者职责分开。

### 4.3 最小告警集

- Worker backlog 持续 5 分钟增长。
- `ack_pending` 接近 consumer `max_ack_pending`。
- LLM 错误率或超时率 5 分钟窗口超过阈值。
- 熔断器持续 open。
- 死信在 10 分钟内出现新增。
- outbox `failed` 或 oldest pending age 超过阈值。
- API 5xx、P95 或 readiness 异常。

### 4.4 验收标准

- 通过一次正常消息可观察 HTTP、消息、工具/LLM、Token 的预期指标变化。
- 注入 LLM 500、NATS 中断和死信后，对应指标与告警进入 firing。
- 恢复后告警在合理窗口自动 resolved。
- 指标端点不得包含明文用户内容、邮箱、手机号、用户 ID 或会话 ID。
- 故障注入报告保存告警 firing/resolved 时间和关键指标截图或机器可读快照。

## 5. 推荐实施顺序

1. 先实施第 8 项中的阶段耗时与 backlog 指标，为容量调优建立可信基线。
2. 再修第 7 项多实例广播，否则 API 横向扩容会制造消息不可达。
3. 最后实施第 6 项 worker 并发化与多实例压测。
4. 每一阶段单独提交、单独回归，不与原生 LLM 流式或 RAG 多轮改造混合。

## 6. 风险控制

- 不取消业务幂等、租户隔离、二次确认和 LLM 全局保护换取吞吐。
- 不直接把同一会话交给多个 worker 并发处理。
- 不把 NATS Core 瞬时广播当作消息历史；断线恢复仍以持久消息为准。
- 不给 Prometheus 标签加入任意 tenant/user/message 文本。
- 未在固定环境完成三轮重复压测前，不更新“容量已达标”的交付结论。
