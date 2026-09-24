# 已知问题

## 当前已知限制（2026-09-24）

- 死信已持久化、按租户查看并可从原始消息重放；尚未提供批量重放和死信保留期清理任务。
- WebSocket 已实现并验证 start/chunk/end 与游标恢复；当前 chunk 是服务端对完整 mock 回复的模拟分片，不是上游 LLM token 原生流。
- Qdrant 已按 Markdown 块存储多表示索引并执行租户/治理/有效期过滤；确定性 embedding 仍只负责低成本候选召回，语义弹性依赖 DeepSeek 查询改写和有上限的语义重排。该方案适合面试演示和小型租户库，不代表大规模专用 embedding 模型的容量或延迟表现。
- 课程表、学习报告、请假、课程提醒和自动续费接口均已暴露；mock 的课程/报告数据仍是固定演示数据。
- 结构化日志、下游熔断和 OpenTelemetry API→HTTPX/NATS→worker trace 已接入；Prometheus 目前只完整接线 HTTP 请求与延迟，队列积压、LLM token、工具结果、熔断和死信指标仍需补齐。Redis/NATS 中断与 worker 积压恢复已由 `make faulttest` 自动验证，尚未进行长时间网络分区、数据库故障和多实例竞争的生产级混沌测试。
- 已在本地 2C4G 环境执行正式目标场景：500 WS 通过；200 msg/s 稳定入口达到 199.12 msg/s、ACK P95 150ms，但 worker 未追平；1000 msg/s 突发仅达到 370.90 msg/s。该结果不代表生产容量，扩容方案见 `docs/optimization/06-08-delivery-optimization-plan.md`。
- 当前 API 出站消费者使用固定 durable；多 API 实例可能由没有目标 WebSocket 的实例取得消息。单实例演示不受影响，多实例广播方案已设计但尚未实施。
- 自动评测包含 50 条固定路由/工具/转人工/越权样本，以及 5 条连接真实 Qdrant 的 RAG 黄金问题；本机结果均为 100%。5 条 RAG 探针用于交付回归，不等同于大规模人工盲评。
- 长会话会把热窗口之前的消息压缩为确定性摘要并回填上下文；该实现避免额外模型成本，摘要质量不等同于 LLM 语义总结。
- Docker Desktop 环境缺 buildx 插件，当前由 classic builder 完成本机 arm64 镜像；多架构镜像未验证。
