# 面试题验收对照表

> 对照《教育平台高并发 AI 客服机器人后端系统的构建与测试》。状态以当前代码、可运行测试和报告为准，不用页面展示代替后端证据。

## 结论

项目已具备“能跑、能测、能压、能演示、能解释”的面试交付闭环。核心得分点都有实现和自动化证据；原题的生产级容量指标尚未在固定 8C16G 环境实测，不作虚假承诺。

## 评分维度

| 维度 | 原题权重 | 当前状态 | 验收证据 |
| --- | ---: | --- | --- |
| 功能完成度 | 30% | 通过 | IM/WebSocket、RAG、提醒、财务、指令、人工转接、5 租户策展演示 |
| 高并发与架构 | 20% | 功能通过，容量部分验证 | NATS JetStream、transactional outbox、Redis 限流、熔断、死信、LLM 全局排队池 |
| 可靠性与安全 | 15% | 通过 | JWT/RBAC、tenant + owner 校验、脱敏、审计、幂等、Schema/allowlist/二次确认 |
| 测试与压测 | 15% | 面试规模通过 | `make test` 强制覆盖率≥70%；10 个 E2E；Locust 原始 CSV 与故障矩阵 |
| 语言质量与评测 | 10% | 通过 | 50 条固定集、引用/拒答/越权/少 AI 味评分 |
| 工程规范与文档 | 10% | 通过 | Compose、Alembic、README、架构/API、agent 记录、压测/评测/已知问题、演示剧本 |

## Word 硬要求逐项映射

| 要求 | 状态 | 主要实现/证据 |
| --- | --- | --- |
| ACK、去重、幂等、断线重连、多端同步、流式回复 | 通过 | `messages.py`、`websocket.py`、E2E WebSocket 断线补发与 chunk 重组 |
| 短期记忆与历史摘要 | 通过 | Redis 最近 20 条 + PostgreSQL 全量历史，窗口外消息生成持久化确定性摘要并在后续 LLM 轮次注入 |
| 多租户隔离 | 通过 | JWT 中 tenant_id、所有资源 tenant 过滤、Qdrant tenant filter、越权反例测试 |
| 意图识别与混合路由 | 通过 | 安全业务意图规则优先，未匹配再做 RAG/实体检索，不把所有输入盲目交给 LLM |
| 工具 Schema 校验、权限、二次确认 | 通过 | `tool_service.py`、`command_service.py`、非 allowlist 工具拒绝 |
| RAG、引用、无依据拒答、更新索引 | 通过 | Qdrant tenant 过滤、语义+词法+实体融合、上传后重建文档分块 |
| 提醒 CRUD、时区、重复、提前提醒、持久化、到点 IM | 通过 | PostgreSQL + scheduler `SKIP LOCKED`；once/daily/weekly/weekdays；occurrence 幂等 |
| 财务 5 类查询、鉴权、脱敏、故障拒绝编造、审计 | 通过 | mock-finance + finance API、A 查 B 返回 403、超时 fixed fallback |
| 人工转接与不在线留言 | 通过 | 显式转人工、脱敏摘要/意图/已尝试操作；未购买功能时不伪装转接 |
| 队列削峰、限流、熔断、降级、重试、死信 | 通过 | NATS + outbox；tenant/user 限流；HTTP 熔断；有界重试与死信重放 |
| 全局 LLM 并发保护 | 加分实现 | Redis 全局准入：600 在途、20 starts/s，超额不建立 DeepSeek TCP 连接 |
| 日志、Prometheus、trace、审计 | 通过 | JSON 日志、`/metrics`、OpenTelemetry Collector、traceparent 跨 NATS |
| token 与成本按租户统计 | 通过 | `llm_usages` 迁移与租户工作台会话成本面板 |
| Docker Compose / Mock / 迁移 / 健康检查 | 通过 | `make up`、5 个 Mock、Alembic 0001–0008、live/ready |
| 测试、压测、故障注入、LLM 评测 | 通过/规模边界已标注 | `make test`、Locust CSV、fault matrix、50 条 dataset |

## 不应夸大的边界

1. 本机报告是面试规模，没有在固定 8C16G 环境执行 500 WebSocket、200 msg/s 持续 5 分钟、1000 msg/s 突发 30 秒；因此不宣称达到生产容量。
2. WebSocket 会输出 `reply.start/chunk/end`，但当前 LLM HTTP 边界是完整回复后分块下发，不把它宣称为供应商原生 token streaming。
3. 长会话摘要采用确定性压缩，不会为了摘要再消耗一次 LLM；生产上如需更高语义质量，可在异步低优先级队列中替换为模型摘要。
4. Redis 故障时 LLM 全局准入会 fail-open 以保证可用性；生产发布前应根据供应商限额决定改为 fail-closed 或本地备用 semaphore。

## 一键验收

```bash
make up
make acceptance
make loadtest
make demo
```

人工演示按 `docs/demo-script.md` 执行，不临场自由发挥。
