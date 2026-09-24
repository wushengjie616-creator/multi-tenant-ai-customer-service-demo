# P8 可靠性与可观测实施指南

## 1. 本阶段目标

把前面各阶段已有的错误处理收敛成可证明、可定位、可恢复的系统行为。本阶段不是在末尾统一给所有异常套 `try/except`，而是补齐限流、超时预算、有限重试、熔断、死信、指标和跨链路 trace。

## 2. 目标文件

```text
app/middleware/{rate_limit,tracing}.py
app/core/{metrics,tracing}.py
app/utils/retry.py
app/clients/*.py
app/core/nats.py
app/services/outbox_relay.py
app/workers/*.py
mocks/*/main.py
tests/unit/test_{rate_limit,retry,circuit_breaker}.py
tests/integration/test_fault_injection.py
docker-compose.yml（Prometheus 如纳入）
.env.example
```

## 3. 超时预算与错误分类

为 LLM、finance、platform、Qdrant 分别配置 connect/read/total timeout；不得使用一个全局无限 timeout。

统一错误分类：

| 类型 | 示例 | 行为 |
|---|---|---|
| validation/auth | 非法 Schema、401/403、资源不属于用户 | 不重试，审计/拒绝 |
| not found/business | 资源不存在、不可执行状态 | 不重试，明确回复 |
| transient | connect timeout、部分 5xx、429 | 有限重试或排队 |
| permanent dependency | 明确 4xx、协议不兼容 | 不重试，降级 |
| malformed response | 非法 JSON、缺字段 | 不执行工具，降级并计数 |

重试只能包围幂等读或带业务幂等键的写；每次尝试共享总时间预算，不能层层重试相乘。

## 4. 限流

当前实现保留简单的 Redis fixed-window，并同时按 tenant 和 user 两个维度计数：

```text
rate:tenant:{tenant_id}:{minute_window}
rate:user:{tenant_id}:{user_id}:{minute_window}
```

- 身份来自 AuthContext；
- tenant/user 的两次计数与 TTL 设置由一个 Lua 脚本原子完成，不能保留无 TTL key；
- user key 不含 conversation，同一用户不能通过新建会话绕过配额；tenant key 聚合同租户所有用户；
- HTTP 超限返回明确 429；
- Redis 故障时限流 fail-open，`/health/ready` 同时暴露依赖异常；高风险写操作仍需经过原有身份、权限和确认 gate。

## 5. 熔断与降级

LLM、finance、platform 各自独立熔断状态，不能一个下游故障拖垮全部业务。状态至少 closed/open/half-open；测试使用注入 clock。

降级必须保持业务真实性：

- LLM 故障：规则意图或固定安全回复；
- finance 故障：暂不可查，不提供金额/状态；
- platform 故障：保留失败状态和下一步，不宣称成功；
- Redis 故障：从 PostgreSQL 加载有限上下文，限流按既定策略；
- NATS 故障：outbox 保持 pending，入口已持久化后仍可 ACK；
- Qdrant 故障：知识问答固定无可用依据，不让 LLM自由回答企业事实。

## 6. 死信

每条终止失败记录原 subject、event_id、trace_id、tenant_id、attempts、错误分类和脱敏摘要。提供只读查看方式或测试辅助接口；不得把完整 payload/PII 无条件复制到日志。

死信发布失败时，数据库中的 failed 状态仍是恢复依据。禁止无限 NAK 造成热循环。

## 7. 指标

Prometheus 至少提供：

- HTTP/WS 请求数、状态码、延迟 histogram；
- 入站 ACK、首响、完整回复延迟；
- NATS publish/consume、redelivery、DLQ、outbox pending/age；
- LLM/finance/platform 调用数、耗时、失败、重试、熔断状态；
- tool 调用和结果；
- Qdrant 命中/拒答/引用校验失败；
- reminder 延迟和投递结果；
- handoff 触发原因；
- LLM input/output token 与 tenant 聚合。

指标 label 禁止使用 user_id、conversation_id、message_id、原始 URL 或错误文本，避免高基数和 PII。

## 8. Trace 与日志

`trace_id` 从 API 接收/生成，经数据库、outbox、EventEnvelope、worker、httpx header 传递。OpenTelemetry span 覆盖 API→outbox/NATS→worker→downstream。

JSON 日志包含必要关联字段：trace_id、tenant_id、conversation_id、message_id、component、operation、outcome、latency_ms；默认不记录完整正文、知识片段、token 或财务值。

上下文变量必须在请求/消息结束后 reset，防止异步任务串租户。

## 9. 故障矩阵

| 注入 | 必须观察到 |
|---|---|
| LLM delay 5s | ACK 不受阻；worker 超时/降级；timeout 指标 |
| LLM 500 | 有限重试后降级；无无限循环 |
| LLM 非法 JSON/工具 | handler 0 调用；malformed 指标 |
| finance timeout/500 | 无虚构；审计失败状态 |
| Redis 重启 | 持久数据不丢；按策略降级后恢复 |
| NATS 暂停 | outbox 累积；恢复后排空；无重复业务 |
| Qdrant 停止 | 知识拒答；ready=503；其他允许业务不编造 |
| 队列积压 | ACK 正常、lag/pending 指标上升并随后恢复 |

## 10. 验证与退出

```bash
./.venv/bin/python -m pytest tests/unit -k 'rate or retry or circuit' -q
docker compose run --rm api pytest tests/integration/test_fault_injection.py -q
curl -fsS http://localhost:8000/metrics
```

- [x] tenant/user 限流有边界和 Redis 故障测试。
- [ ] 所有下游有独立 timeout、错误分类、有限重试。
- [x] 熔断和恢复使用可注入 clock 测试。
- [x] DLQ/failed 记录可定位且已脱敏。
- [ ] 指标无高基数/PII label。
- [x] 一条 trace 可串起 API、消息、worker 和下游。
- [x] 故障矩阵每项有自动化测试或可重复脚本及证据。
