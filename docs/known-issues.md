# 已知问题

## 当前已知限制（2026-09-24）

- 死信已持久化、按租户查看并可从原始消息重放；尚未提供批量重放和死信保留期清理任务。
- WebSocket 已实现并验证 start/chunk/end、游标恢复与 OpenAI 兼容 provider `stream=true` 的 token delta 即时转发；固定话术和不支持 SSE 的降级路径仍会使用本地分片。
- Qdrant 已按 Markdown 块存储多表示索引并执行租户/治理/有效期过滤；确定性 embedding 仍只负责低成本候选召回，语义弹性依赖 DeepSeek 查询改写和有上限的语义重排。该方案适合面试演示和小型租户库，不代表大规模专用 embedding 模型的容量或延迟表现。
- 课程表、学习报告、请假、课程提醒和自动续费接口均已暴露；mock 的课程/报告数据仍是固定演示数据。
- 结构化日志、下游熔断和 OpenTelemetry API→HTTPX/NATS→worker trace 已接入；Prometheus 已接线 API/Worker、队列积压、LLM/Token、工具结果、熔断、死信、Outbox 和人工会话指标及基础告警。Redis/NATS 中断与 worker 积压恢复已由 `make faulttest` 自动验证，尚未进行长时间网络分区和数据库故障的生产级混沌测试。
- 已在本地 2C4G 环境执行正式目标场景：500 WS 通过；Outbox 优化后 200 msg/s 稳定流达到 197.76 msg/s、ACK P95 200ms、零错误且端到端追平；1000 msg/s 经三 API 分流达到 411.22 msg/s、零错误，仍未达到目标。该结果不代表生产容量，扩容方案见 `docs/optimization/06-08-delivery-optimization-plan.md`。
- API 出站已改为 NATS Core 每副本广播，并以 8000 入站、8001 WebSocket 收回复实测通过；Compose 没有内置统一负载均衡入口，生产部署仍需交给 ingress/LB。
- 自动评测包含 50 条固定路由/工具/转人工/越权样本，以及 5 条连接真实 Qdrant 的 RAG 黄金问题；本机结果均为 100%。5 条 RAG 探针用于交付回归，不等同于大规模人工盲评。
- 长会话会把热窗口之前的消息压缩为确定性摘要并回填上下文；RAG 指代追问使用受约束的 LLM 查询改写并带确定性降级，人工转接另生成脱敏的 LLM 摘要与结构化上下文包。
- Docker Desktop 环境缺 buildx 插件，当前由 classic builder 完成本机 arm64 镜像；多架构镜像未验证。
