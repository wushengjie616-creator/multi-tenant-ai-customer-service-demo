# 已知问题

## 当前已知限制（2026-09-24）

- 死信已持久化、按租户查看并可从原始消息重放；尚未提供批量重放和死信保留期清理任务。
- WebSocket 已实现并验证 start/chunk/end 与游标恢复；当前 chunk 是服务端对完整 mock 回复的模拟分片，不是上游 LLM token 原生流。
- Qdrant 已按 Markdown 块存储多表示索引并执行租户/治理/有效期过滤；确定性 embedding 仍只负责低成本候选召回，语义弹性依赖 DeepSeek 查询改写和有上限的语义重排。该方案适合面试演示和小型租户库，不代表大规模专用 embedding 模型的容量或延迟表现。
- 课程表、学习报告、请假、课程提醒和自动续费接口均已暴露；mock 的课程/报告数据仍是固定演示数据。
- Prometheus、结构化日志、下游熔断和 OpenTelemetry API→HTTPX/NATS→worker trace 已接入；Redis/NATS 中断与 worker 积压恢复已由 `make faulttest` 自动验证。尚未进行长时间网络分区、数据库故障和多实例竞争的生产级混沌测试。
- 已做 5 个持久 WS 连接的 10 秒 Locust smoke，不代表 500 WS / 200 msg/s / 1000 msg/s 的目标容量结论；本机 smoke 观察到约 7.4 秒长尾。
- 自动评测包含 50 条固定路由/工具/转人工/越权样本，以及 5 条连接真实 Qdrant 的 RAG 黄金问题；本机结果均为 100%。5 条 RAG 探针用于交付回归，不等同于大规模人工盲评。
- 长会话会把热窗口之前的消息压缩为确定性摘要并回填上下文；该实现避免额外模型成本，摘要质量不等同于 LLM 语义总结。
- Docker Desktop 环境缺 buildx 插件，当前由 classic builder 完成本机 arm64 镜像；多架构镜像未验证。
