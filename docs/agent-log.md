# Agent 开发记录

> 本文保留 agent 介入过程与当时的判断。早期段落中的未完成项为历史快照，当前状态以文末“最终人工复核”、`docs/acceptance-checklist.md` 和可运行测试为准。

## 第 2 步：消息 ACK、队列、worker、mock LLM/IM 最小闭环

**范围**：README「第 2 步」= PLAN.md S2「IM 与异步消息闭环」。打通
`IM Client -> API -> message_id 去重 -> 快速 ACK -> JetStream im.inbound -> Worker ->
mock-llm -> JetStream im.outbound -> mock-im/WS 推送` 最小闭环。

**落地的实现决策**（与 `docs/architecture.md` 冻结基线一致）：

- **事务型 outbox**（`app/models/outbox.py` + `app/services/outbox_relay.py`）：消息与
  `im.inbound`/`im.outbound` 事件同事务落库，后台 relay 以 `FOR UPDATE SKIP LOCKED` 抢占
  `pending` 事件发布到 JetStream，消除「DB 已写、队列未发」双写窗口（PLAN S2 §6.2）。
- **消息去重**：沿用 `messages(tenant_id, message_id)` 唯一约束；webhook 重复 `message_id`
  返回 `duplicate`，不产生新 outbox / 副作用（FR1-05）。
- **至少一次 -> 恰好一次业务**：worker 消费用 `mark_processing` 幂等门，处理后 ACK。
- **trace_id 传播**：HTTP `X-Trace-Id` / 事件 `trace_id` / `messages.trace_id` 全程贯通。
- **ACK 不等待下游**：webhook 返回 `202 accepted`，WS 返回 `message.accepted`（FR1-03 / NFR1-02）。

**新增/改动**：
- 新增 `app/models/outbox.py`、`app/services/outbox_relay.py`、迁移 `0002_outbox_and_trace.py`。
- 改写 `app/core/nats.py`（`connect/ensure_stream/publish/publish_bytes/subscribe/pull_subscribe` +
  `INBOUND_SUBJECT`/`OUTBOUND_SUBJECT`）、`app/clients/llm_client.py`（`LLMClient.generate` +
  `extract_reply` + 单例 `llm_client`）。
- `conversation_service.ingest_message`/`save_reply` 改为 outbox 同事务写入；
  `app/main.py`、`app/workers/message_worker.py` 启动 relay loop。

**与 PLAN.md 的偏差**：PLAN.md §1.2 仍写「RabbitMQ + Qdrant」，属 stale。实际代码与冻结的
`docs/architecture.md` §1/§2 采用 **NATS JetStream**（pgvector 复用 PG，不引入独立向量库）。
按架构冻结文档为准，PLAN.md 未回改。

**验证**：`make up` 后 `make migrate` + `make demo`（seed → 发送 → 幂等 → 轮询回复）。
本机（无 Docker / Python 3.12）未实跑，需在 Docker 环境验证。

## 第 2 步补充：清理残留 RabbitMQ/Qdrant 引用

第 2 步收尾时发现三处仍停留在 PLAN.md §1.2「RabbitMQ + Qdrant」时代的 stale 代码，
与冻结的 NATS 架构矛盾，且会直接阻断启动（`aio_pika` 不在依赖、`settings.nats_url` 缺失）：

- `app/core/config.py`：`rabbitmq_url`/`qdrant_url` → 替换为 `nats_url`
- `app/api/health.py`：`import aio_pika` + RabbitMQ/Qdrant 就绪探针 → 改为 NATS 就绪探针
- `.env.example`：`RABBITMQ_URL`/`QDRANT_URL` → `NATS_URL`

验证：全模块 import 通过 + `pytest` 3 passed（本机 uv 环境）。

## Codex 全面接管与复核（2026-09-23）

用户认为原 P1 执行质量不足并授权按 `PLAN.md` 完整实施。接管后没有继承旧“完成”结论，而是先补反向测试；发现并修复未知 tenant 登录 FK 500、合法签名非 UUID claim 500、同租户跨用户会话读写、outbox 无终态、worker 无 processing lease，以及确认摘要未绑定资源等问题。

本轮主要影响：P1 身份/owner 边界；P2 lease/retry/dead-letter/WS；P3 意图与工具策略；P4 Qdrant；P5 财务；P6 确认幂等；P7 提醒和人工；P8 指标/限流；P9 E2E/Locust/50 条评测；迁移新增 0004、0005。

人工审查重点：LLM 永远不能直接触发工具；财务在任何生成步骤前脱敏；确认绑定全部身份与资源；Qdrant 查询只能经 tenant scoped adapter。被重写的实现包括“所有消息直接送 mock LLM”、只按 tenant 检查会话、卡死 processing 状态和无重试终态的 outbox。

未伪装完成的部分记录在 `docs/known-issues.md`：正式规模压测、OpenTelemetry、真正 WS 分片/游标、完整平台工具和剩余 live E2E 仍需继续。

## 2026-09-23 第二轮收口

继续采用测试优先补齐 WS 分片与游标、知识分块、平台查询/低风险指令、死信管理、熔断器和 OpenTelemetry。新增迁移 0006；Compose 改为单一共享应用镜像并加入 OTLP Collector。实际验证为 107 项全量测试、10 项 live E2E、核心覆盖率 71.79%，以及 5 个持久 WS 用户的 Locust smoke。当时正式容量压测和完整故障矩阵仍保留为未完成项；其中故障矩阵已于后续通过 `make faulttest` 收口。

## 最终人工复核与演示收口（2026-09-24）

### 关键指令 / prompt 意图

- “以 Word 原文为唯一验收来源，不把前端页面等同于后端实现。”
- “先用反例证明 tenant/user 隔离、幂等、脱敏和 LLM 无执行权，再补功能。”
- “所有自动化测试强制使用 Mock LLM，真实 DeepSeek 只用于手工演示。”
- “不虚构生产容量；面试规模压测与原题 8C16G 满规模指标分开报告。”

### Agent 主要生成/修改范围

- FastAPI API、worker/scheduler、NATS outbox、Redis 上下文/限流/LLM 准入、Qdrant RAG、Mock 服务。
- Alembic 0001–0008、单元/集成/E2E/Locust/故障脚本和 50 条评测。
- 平台管理员、租户工作台和客户演示界面，以及 README、架构、API、压测和演示文档。

### 人工审查后纠正的代表性问题

- 原始实现只按 tenant 验证会话，同租户用户可互读；改为 tenant + owner 双重校验并加越权测试。
- 原始 worker 的 processing 状态崩溃后可永久卡死；改为有期租约与重投恢复。
- 原始 outbox 无有界重试和终态；改为退避、失败终态、死信与重放。
- 原始意图过窄，未匹配就转人工；改为安全意图优先，之后进行全文/实体/RAG 检索。
- 前端曾将 API 返回的 `document` 变量覆盖 DOM `document`；重命名并加页面行为测试。
- 历史样例租户曾复用 UUID，造成知识/账号混入；清理冲突数据，并用 5 个确定性租户作为演示目录。
- 原 `make test` 只跑 pytest，虽有覆盖率报告但不会阻止低于 70% 的提交；现已将 `--cov-fail-under=70` 固化到公开验收命令。

### 明确不采信或重写的 Agent 方案

- 不采用“LLM 输出 JSON 就直接调平台”；改用确定性 allowlist + Pydantic Schema + RBAC + ownership + confirmation。
- 不采用“RAG 无证据也让模型自由回答”；服务端 evidence gate 负责拒答与引用。
- 不采用“每个 worker 各自 20 并发”；改为 Redis 全局共享的 600 在途 + 20 starts/s。
- 不把 Locust smoke 成绩写成生产容量承诺；未跑的 500 WS / 200 msg/s / 1000 msg/s 保留在已知边界。
