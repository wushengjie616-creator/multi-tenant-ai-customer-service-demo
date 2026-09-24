# Locust 压测摘要

- 日期：2026-09-23；环境：macOS arm64 + Colima，Compose v2 5.5.1，Locust 2.32.6。
- 稳定接入：20.08 msg/s，P95 29 ms，0 失败。
- 突发接入：100.27 msg/s，P95 80 ms，0 失败。
- 财务查询：100.27 QPS，P95 140 ms，0 失败，满足题目 P95 < 500 ms 目标。
- 20% LLM 超时：5 个 WebSocket 用户完成 69 次问答，0 失败，P95 2.2 s；降级成功但观察到队头阻塞。
- 突发后 NATS 三个 consumer 的 pending / ack pending / redelivered 均为 0。

详细参数、资源抽样、复现命令、局限和原始 CSV 索引见 [`report/压测报告.md`](../report/%E5%8E%8B%E6%B5%8B%E6%8A%A5%E5%91%8A.md)。
