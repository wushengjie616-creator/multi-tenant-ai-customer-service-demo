# 实施进度（PROGRESS）

> 本文件是 v2 PLAN.md 要求的跨会话进度记录，为当前阶段进度的唯一权威。README 的“完成”勾选不复用。
> 生成：2026-09-23；执行者：接手的 coding agent。

## 当前阶段

- **阶段：P9/P10 验收与收尾进行中；功能、测试和面试规模故障矩阵已通过，仅保留正式生产容量压测与演示录屏**
- 冻结技术栈：**NATS JetStream + Qdrant + Locust**（PLAN §2）

## 环境基线（P0 §6.1.1）

| 项 | 值 |
|---|---|
| 操作系统 | Darwin 25.6.0（macOS arm64） |
| 系统 Python | 3.9.6（未使用） |
| 项目 Python | `.venv` 内 Python 3.12.14（uv 0.12.18 创建，无 pip，用 `uv pip install --python .venv/bin/python` 安装） |
| Docker | ✅ 已安装（CLI 在 `/opt/homebrew/bin/docker`，不在默认 PATH；daemon 经 Colima aarch64 运行） |
| Docker Compose | ✅ v2 5.5.1（`docker compose`） |
| Git | 2.50.1（当前目录**不是** Git 仓库，PLAN §3 已注明） |
| 并发写入者 | 已确认无（近 3 分钟无文件写入；残留 Codex 进程为闲置桌面应用） |

## 基线检查（接手时原始状态，P0 §6.1.3）

| 检查 | 结果 |
|---|---|
| 现有测试 | 3 passed（仅 `tests/unit/test_events.py`、`test_llm_client.py` 两个文件） |
| 静态 import（app + mocks） | 全部通过 |
| §6.2 一致性门槛 | **9 处旧选型残留 + 5 处 Qdrant 接线缺失**（见下，已全部清除） |

接手时旧选型残留：
- `README.md:9`（RabbitMQ）、`README.md:44`（rabbitmq 端口）
- `docker-compose.yml:5`（注释含 pgvector）、`:25`（`pgvector/pgvector:pg15` 镜像）
- `docs/architecture.md:3/12/14/72/74`（RabbitMQ + aio-pika）

接手时 Qdrant 接线缺失：`config.py` 无 `qdrant_url`、`.env.example` 无 `QDRANT_URL`、`health.py` 无 Qdrant 就绪探针、compose 无 qdrant 服务、`requirements.txt` 无 qdrant-client/locust。

## 文件分类与处理决定（P0 §4.1.4）

| 类别 | 文件 | 处理 |
|---|---|---|
| 保留 | `app/core/nats.py`、`app/workers/message_worker.py`、`app/services/outbox_relay.py`、`migrations/`、`app/models/`、`app/schemas/`、`mocks/`、其余 services/clients/middleware | 不动（NATS 消息闭环 + 数据/事件模型已存在，作为待审计资产） |
| 修正 | `app/core/config.py` | 补 `qdrant_url`（保留 `nats_url`） |
| 修正 | `.env.example` | 补 `QDRANT_URL` |
| 修正 | `app/api/health.py` | 就绪探针增加 Qdrant 检查（`AsyncQdrantClient`） |
| 修正 | `requirements.txt` | 加 `qdrant-client==1.12.0`、`locust==2.32.6`、`pytest-cov==5.0.0` |
| 修正 | `docker-compose.yml` | postgres 镜像 pgvector→`postgres:15`；新增 qdrant 服务与 `QDRANT_URL` |
| 重写 | `docs/architecture.md` | RabbitMQ→NATS JetStream 全量同步 |
| 重写 | `README.md` | RabbitMQ→NATS；第 1/2 步完成标记复位为 `[ ]` |
| 同步 | `../项目需求拆解.md` | pgvector→Qdrant、k6→Locust；容器划分加 qdrant 行（不改 FR/NFR/E2E 编号与含义） |
| 暂不使用 | `loadtests/locustfile.py`、`evaluation/evaluate.py` | 空 stub，P9 实现 |

## 验证命令与结果（P0 收敛后）

```bash
# §6.2 一致性门槛 → 退出码 1，零命中 ✓
rg -n -i 'rabbitmq|aio[-_]?pika|pgvector|k6' \
  app mocks tests docker-compose.yml requirements.txt .env.example \
  README.md docs/architecture.md Makefile loadtests

# 静态 import 全量（app + mocks + 入口）→ 全部通过 ✓（含 health.py 新引入 qdrant_client）

# Settings 校验 → qdrant_url / nats_url 均存在 ✓；AsyncQdrantClient(url=, timeout=)/get_collections/close 均存在 ✓

# 现有测试 → 3 passed ✓
./.venv/bin/python -m pytest -q
```

## P0 退出条件（§6.3）

- [x] 所有入口可 import，无缺包或 Settings 字段缺失（qdrant-client/locust/pytest-cov 已装）
- [x] `docker compose config`、fresh build、空库迁移成功（`alembic upgrade head` → `0002 (head)`，6 张表）
- [x] PostgreSQL、Redis、NATS、Qdrant 健康（compose ps 四者 `healthy`）
- [x] live=200；依赖全开 ready=200；任一关闭 ready=503（停 qdrant → `degraded` + `qdrant: error: [Errno -3] name resolution` + 503；恢复 → 200）
- [x] README、架构、环境变量、依赖和 Compose 只描述活动技术栈（§6.2 零命中）
- [x] 容器内 `docker compose run --rm api pytest -q` → 3 passed（不再依赖本机 .venv 等价验证）

## Docker 验收结果（P0 §4.3，2026-09-23 完成）

| 项 | 结果 |
|---|---|
| fresh build（Aliyun pip 镜像，无 `--no-cache`） | ✅ exit 0；8 个镜像（api/worker/scheduler + 5 mock）均含 nats-py 2.9.0 / qdrant-client 1.12.0 / locust 2.32.6 |
| 基础设施镜像 | ✅ postgres:15 / redis:7-alpine / nats:2-alpine / qdrant/qdrant:v1.9.2 拉取成功 |
| `up -d` + `ps` | ✅ 12 服务 up，4 基础设施 `healthy` |
| 空库迁移 | ✅ `0002 (head)`，6 表（tenants/users/conversations/messages/outbox_events/alembic_version） |
| 健康检查 | ✅ live=200；ready=200（四依赖 ok）；停 qdrant→503；恢复→200 |
| 容器内测试 | ✅ 3 passed |

已知告警（非阻塞）：
- postgres 迁移打印 `collation version mismatch (2.36 vs 2.41)`：pgdata 卷由旧 glibc 初始化，当前 postgres:15 镜像带新 glibc。空库无索引，仅告警不报错。已用 `ALTER DATABASE template1/postgres/eduai REFRESH COLLATION VERSION` 刷新，`CREATE DATABASE` 恢复正常。

## P1 完成（数据、安全与身份底座，2026-09-23）

### 实现内容

- **模型**：`User` 增 `email`（租户内唯一）/ `phone` / `full_name` / `password_hash`，`UniqueConstraint(tenant_id, email)`；新建 `AuditLog`（trace_id/tenant_id/actor_id/action/target/outcome/detail JSONB/created_at）。
- **迁移** `0003_audit_and_user_pii.py`：加列先 nullable → 存量行 backfill（email 用 `legacy-<id>@example.com`、password_hash 用不可登录占位 `!locked`）→ 收紧 NOT NULL → 建唯一约束 + audit_logs 表。已验证两条路径：**从空库**（→0001→0002→0003）与**从 0002 升级**（存量 1 行 backfill 成功）。
- **安全** `app/core/security.py`：JWT（`sub`/`tenant_id`/`role`/`exp`，身份与租户只来自已验证 token）、PBKDF2-SHA256 密码哈希。`app/api/dependencies.py`：`get_current_user` + `require_roles(*roles)`（RBAC）。
- **脱敏** `app/utils/masking.py`：email/phone/bank_card/id_card 逐字面 + `mask_pii` 自由文本 + `mask_value` 递归（敏感 key 整值 `[REDACTED]`）。`app/core/logging.py` 的 `JsonFormatter` 对消息/异常/extra_fields 统一脱敏。
- **端点**：`POST /auth/login`、`GET /users/me`、`GET /admin/users`、`GET /admin/audit-logs`（admin + 强制 tenant 过滤）；`/webhooks/im/messages` 与 `/conversations/{id}/messages` 改由 JWT 驱动，请求体伪造 tenant_id/user_id → 403。

### P1 退出条件（PLAN §7.2）对照

| # | 退出条件 | 证据 |
|---|---|---|
| 1 | 租户 A 读不到 B 的 users/conversations/messages/audit | `test_admin_tenant_isolation` / `test_cross_tenant_list_messages_403` / `test_tenant_filter_isolation_at_query_level` / `test_audit_logs_tenant_isolation` |
| 2 | 普通 user 不能做 agent/admin 操作 | `test_user_cannot_access_admin`（/admin/* → 403） |
| 3 | 请求体/LLM 伪造身份不能覆盖认证身份 | `test_webhook_rejects_forged_identity`（403）+ `test_webhook_uses_token_identity`（以 token 为准落库） |
| 4 | email/phone/bank_card/id_card 逐字面脱敏 | `tests/unit/test_masking.py`（14 字面断言）+ `test_admin_masks_pii` |
| 5 | 日志/审计不含完整 token/password/PII | `tests/unit/test_logging.py`（含敏感 key 整值 REDACTED）+ `test_admin_masks_pii`（审计 target 只含脱敏邮箱） |
| 6 | 删 tenant filter 使 ≥1 隔离测试失败 | `test_tenant_filter_isolation_at_query_level`（直接调 `list_messages` 服务，注释标明突变守卫） |
| 7 | 迁移从空库 + 从上一版本均可行 | 空库 →0001→0002→0003 ✓；0002→0003 backfill ✓（见上） |

### 验证结果

```bash
# 本机 .venv：unit + integration → 43 passed
DATABASE_URL=...@localhost:5432/eduai DATABASE_URL_SYNC=... .venv/bin/pytest tests/unit tests/integration -q

# 容器（canonical）：rebuild 后 → 43 passed；alembic current = 0003 (head)
docker compose run --rm api alembic current          # 0003 (head)
docker compose run --rm api pytest tests/unit tests/integration -q   # 43 passed
```

### 过程中发现并修复的问题

1. **迁移 0003 对存量行失败**：最初 `ADD COLUMN email NOT NULL` 直接报 `NotNullViolation`（0002 已有 1 行 seed）。改为先 nullable + backfill + 收紧。
2. **日志敏感 key 漏打码**：`JsonFormatter` 原先对 `extra_fields` 逐 value 调 `mask_value`，`authorization`/`password` 等 key 的字符串值不会按 key 打码。改为整 dict 交给 `mask_value`（按 key 判敏感）。
3. **集成测试并发/事件循环**：pytest-asyncio 每测试独立 loop，asyncpg 连接跨 loop 复用触发 `attached to a different loop`。改用 `NullPool` 引擎（每次新建连接、即用即关）。另 `str(URL)` 会把密码掩码成 `***`，改用 `render_as_string(hide_password=False)`。
4. **demo/seed 连带修复**：`seed_data.py` 建 user 缺 `email/password_hash`（P1 起 NOT NULL）→ 补 `demo@example.com` + `!locked`；`demo.py` 走 webhook 无 JWT、list 用已废弃的 `X-Tenant-Id` 头 → 改为签发 token 走 `Authorization` 头。

### 明确延后到后续阶段（非 P1 范围）

- WS `/ws` 鉴权：P2 与消息闭环一起做。
- `idempotency.py` / `rate_limit.py` / `tracing.py` 等空 stub：分别归 P5/P6/P8。
- 无 agent 专属端点，RBAC 的 agent 分支待 agent 端点落地时测试。

## 下一阶段（P2）风险与前置

1. P1 退出条件全部满足（本机 + 容器双验证）；P2（消息闭环 / WS / LLM 编排）需在 P1 身份底座上接 `get_current_user` 到 WS 握手。
2. 集成测试基座已就绪（`tests/integration/conftest.py`：独立 `<db>_test` 库 + NullPool + 造数助手），P2 新端点直接复用。
3. 审计/脱敏已挂到日志与审计服务，后续新业务落地前沿用 `record_audit` + `mask_value`，不要散落手写脱敏。

## Codex 接管复核与 P2 实施记录（2026-09-23）

### P1 复核发现并修复

原 43 项测试均通过，但缺少三类真实反例：

1. 不存在的 tenant 登录会向带外键的 `audit_logs` 写入并触发 500；现改为只有合法 tenant 才落租户审计，外部仍统一返回 401。
2. 合法签名但非 UUID 的 identity claim 会在 API 下游触发 500；现由共享 Bearer 校验入口拒绝为 401。
3. 只校验 tenant、未校验 conversation owner，导致同租户用户可读写他人会话；现普通用户必须同时匹配 tenant + user，会话写入的 service 边界也强制 owner。

RED：4 个新增集成测试均按预期失败（FK IntegrityError、ValueError 500、写入 202、读取 200）。  
GREEN：同一聚焦命令 `4 passed`；全量本机 unit+integration `49 passed`。

### P2 已完成的增量

- 新增迁移 `0004_message_leases_and_outbox_retry.py`，当前数据库 `0004 (head)`。
- message 增加 processing lease；worker 崩溃后消息可在租约过期后重新取得处理权。
- outbox 增加 tenant、next_attempt_at、claimed_at；发布失败按 30s/2m/10m/30m 退避，最多 5 次后 `failed`。
- worker 回复 ID 改为 tenant + inbound message 的确定性 hash；consumer 显式 ACK、ack_wait=150s、max_deliver=5，第 5 次失败发布最小化死信。
- WebSocket 改为 Authorization Bearer JWT、普通用户 conversation owner 校验、tenant+conversation 双键 fan-out；出站使用完整 envelope。
- P3 已开始：规则意图路由的 9 个单测已完成 RED→GREEN。

最新本机证据：unit `31 passed`（随后新增 P2/P3 单测）；integration `18 passed`；P2 reliability 聚焦 `2 passed`。fresh Docker 镜像构建正在执行，尚未把容器/E2E 结果记为通过。

## 2026-09-23 完整接管实施结果

### 已实现

- P2：processing lease、outbox 租约/退避/终态、JetStream 显式 ACK/NAK/max-deliver、最小死信、确定性 reply id；HTTP 与带 Bearer/owner 校验的 WebSocket 闭环。
- P3：固定 `IntentResult`、规则优先/未知 abstain、工具 Schema/allowlist/RBAC/owner/confirmation 管线；确认摘要绑定 tenant/user/conversation/action/resource/args。
- P4：Qdrant tenant/governance 强制过滤、确定性 embedding、幂等示例导入、证据门控、服务端引用校验。
- P5：订单/账单/发票/退费/余额 mock/client/API；跨用户 403 + 安全审计；递归脱敏；超时/失败固定降级。
- P6：关闭自动续费 5 分钟确认、原子状态转换、幂等 execution 和 mock-platform。
- P7：持久提醒 CRUD、UTC/IANA 时区、单次/每天/每周/工作日、`SKIP LOCKED` scheduler、occurrence 唯一；幂等人工转接与脱敏上下文投递。
- P8：tenant/user Redis 双维限流、JSON 脱敏日志、Prometheus HTTP 指标；P2 已含有限重试与死信。
- P9：50 条固定评测与评分器、Locust HTTP smoke、E2E-01 至 E2E-10 全部 live 验证，另含真实 WebSocket 测试。

### fresh 证据

```text
docker compose run --rm api pytest tests/unit tests/integration -q
84 passed（61 unit + 23 integration；仅 python-jose datetime deprecation warnings）

pytest ... --cov-fail-under=70
TOTAL 628 statements, 78.82%; 83 passed

docker compose run --rm api pytest tests/e2e/test_core_scenarios.py -q
10 passed（覆盖 E2E-01 至 E2E-10，并额外验证 WebSocket）

python -m evaluation.evaluate
50/50, intent_accuracy=1.0

Locust 5 users / 8s
100 requests, 0 failures, 9.16 req/s, P50 14ms, P95 4.6s, P99 6.7s
```

迁移已升级到 `0005 (head)`；示例 5 篇知识已成功导入真实 Qdrant；`make demo` 验证消息去重与异步回复；12 个 Compose 服务运行。

最终公开测试入口 fresh 结果：`make test` → **94 passed, 30 warnings, 52.77s**（warnings 均来自 python-jose 内部 `datetime.utcnow()` deprecation）。`/health/ready` 返回 PostgreSQL/Redis/NATS/Qdrant 全部 `ok`，`/metrics` 已观察到按路由聚合的 request/latency 指标。

### 2026-09-23 第二轮收口（当前证据）

- WebSocket 已实现并由 live E2E 验证 `reply.start → reply.chunk → reply.end`、delta 重组和 `after_message_id` 重连补取。
- 新增迁移 `0006_dead_letters.py`；死信持久化脱敏元数据，admin 按租户查看并可从原消息重放。
- Qdrant 导入改为稳定 Markdown 分块，写入 version/content_hash/effective_from/effective_until；示例数据重导入为 5 chunks。
- 平台补齐课程表、学习报告、提交请假、修改课程提醒；低风险写操作持久幂等，关闭续费继续二次确认。
- finance/LLM/platform HTTP 边界加入三次失败开启、超时后半开探测的异步熔断器。
- OpenTelemetry SDK + FastAPI/HTTPX instrumentation + OTLP Collector 已接入；EventEnvelope 携带 W3C `traceparent`，测试证明 NATS 事件边界 trace ID 不变，Collector 实际收到 spans。
- Compose 改为只构建一次 `education-ai-service-app:latest`，其余 Python 服务共享镜像；当前共 13 个服务。
- Locust 现覆盖持久 WebSocket；5 users/10s smoke 为 46 次请求/事件、0 失败，WS 往返中位数 130ms，但最大约 7.4s。

Fresh 验证：

```text
make test
107 passed, 37 warnings, 11.57s

pytest tests/unit tests/integration --cov=app --cov-fail-under=70
97 passed; TOTAL 1872 statements; 71.79%

docker compose run --rm api pytest tests/e2e -q
10 passed in 12.41s

alembic upgrade head
0006 (head)

/health/ready
postgres/redis/nats/qdrant = ok

OpenTelemetry Collector debug exporter
resource spans=1, spans=5（对 ready/root 请求的实测）
```

仍未满足的最终 Gate：

- 未在固定资源环境执行 500 WS、200 msg/s 持续 5 分钟、1000 msg/s 突发 30 秒；现有 smoke 不构成容量承诺。
- 当时边界：Redis stop/start 仅人工验证，NATS/积压矩阵未完成。该项已由文末“故障注入矩阵收口”取代。

## 2026-09-23 Redis 功能补齐与工程修复

### 已实现

- 新增固定窗口短期上下文：`context:{tenant_id}:{conversation_id}`，默认最近 20 条 message、TTL 3600 秒，每次写入刷新 TTL。
- Redis miss/expired 时从 PostgreSQL 最近消息按时间正序重建并回填；Redis error 时直接使用 PostgreSQL 历史，聊天链路不中断。
- Redis 读写失败会给 conversation 加进程内 dirty 标记，恢复后的下一次访问强制从 PostgreSQL 重建；Compose 明确关闭 RDB/AOF，Redis 重启不恢复陈旧缓存。
- 闲聊 LLM 输入改为“同租户同会话历史 + 当前 user message”，当前 message 不重复。
- worker 只在 assistant message 与出站 outbox 已提交 PostgreSQL 后写 Redis；Redis 仍是可重建热副本。
- tenant/user fixed-window 限流改为单个 Lua 脚本原子计数并设置 TTL，保持 Redis 故障 fail-open 与 HTTP 429 行为。
- 新增 `REDIS_MAX_CONNECTIONS=50`、`CONTEXT_MAX_MESSAGES=20`、`CONTEXT_TTL_SECONDS=3600`，Settings、Compose、`.env.example` 一致。
- Compose 不再向宿主机发布 Redis 6379，应用仅通过内部 `redis:6379` 访问。

### RED → GREEN 证据

```text
RED: docker compose run --rm -v "$PWD:/app" api pytest \
  tests/unit/test_context_service.py tests/unit/test_assistant_service.py \
  tests/unit/test_rate_limit.py -q
exit 1: 8 failed, 3 passed（目标行为缺失）

RED: docker compose run --rm -v "$PWD:/app" api pytest \
  tests/unit/test_redis_config.py -q
exit 1: 2 failed（Settings 字段与客户端工厂缺失）

GREEN: 同一聚焦 unit 集
exit 0: 13 passed

Redis 聚焦 integration + PostgreSQL 提交顺序 unit
exit 0: 8 passed, 3 warnings

make test
exit 0: 125 passed, 40 warnings, 11.69s

pytest tests/unit tests/integration --cov=app --cov-fail-under=70
exit 0: 115 passed, 40 warnings；TOTAL 1969 statements，73.84%

pytest tests/e2e -q
exit 0: 10 passed, 6.27s
```

warning 均为 python-jose 内部 `datetime.utcnow()` deprecation，不是本轮失败。

### Redis stop/start 故障验证

```text
Redis 停止：live=200；ready=503（checks.redis 明确 ConnectionError）
认证消息接入：202；历史查询：200
worker：记录 context read/append failed，但从 PostgreSQL 历史继续生成并持久化回复

Redis 恢复：ready=200
RDB/AOF 关闭，重启后旧 context 不存在；发送下一条消息后自动从 PostgreSQL 重建：
context_length=20；TTL=3597 秒；故障期间持久化的 message 已出现在 Redis context
```

边界：stop/start 为本轮人工可重复验证，尚未封装为自动控制 Docker 的 fault test；NATS/积压矩阵不在本轮 Redis 修复范围。

## 2026-09-23 本地双角色演示前端与 DeepSeek

- `/ui/tenant.html`：租户管理员登录、Markdown/TXT 或粘贴文本导入、当前租户客服问答。
- `/ui/customer.html`：通过受 `DEMO_MODE` 限制的短期 JWT，一键进入示例客户会话。
- `demo-bootstrap` 先运行 Alembic，再幂等创建管理员/客户/会话并导入 5 份 sample-data 知识。
- LLM 客户端支持 `.env` 配置完整 DeepSeek URL / key / model / timeout / temperature / token 限制；key 仅在服务端使用。
- RAG 先做租户过滤和证据门控，仅对 `SUPPORTED` 证据请求 LLM 组织话术；引用不交给 LLM，失败回退到证据原文。
- 新增文档大小限制并新增/更新 14 个聚焦测试，覆盖静态页、demo 开关/身份、上传限制、DeepSeek 请求合约、RAG 精排与 grounded synthesis。

最终 fresh 验证：

```text
docker compose config --quiet
exit 0

docker compose run --rm --no-deps -v "$PWD:/app" api pytest -q
134 passed, 42 warnings in 12.73s

真实浏览器链路
客户一键会话：成功创建 JWT 会话并返回带来源的知识答案
租户链路：管理员登录 -> 粘贴文本 -> 1 个分块导入 -> 问答命中新文档来源
```

warnings 仍全部来自 python-jose 内部 `datetime.utcnow()` deprecation。

## 2026-09-23 实体增强混合 RAG

- 入库 payload 新增 `entities/aliases/topics/keywords/search_text/enrichment_status`；API 优先 DeepSeek 结构化抽取，任何异常使用确定性规则降级并继续入库。
- 新增 10 类知识型客服意图；未命中显式意图的问题先用 DeepSeek 抽取查询实体（失败时规则降级），再进入当前租户 RAG，不再立即拒答或暗示转人工。
- 检索合并 Qdrant 实体 keyword 候选、最多 `KNOWLEDGE_LEXICAL_SCAN_LIMIT` 个租户分块的中文全文兜底和原向量候选，再按实体与二元词重叠重排。
- 真实 Qdrant 1.9 测试证明 multilingual `MatchText` 不能稳定命中连续中文短语，因此改用有上限的租户内本地匹配。
- 新鲜验证：`tests/unit + tests/integration` 共 138 项通过；API/worker 临时切换仓库 mock 后 `tests/e2e` 共 10 项通过，随后已恢复 `.env` 的 `deepseek-flash` 并完成真实连通性检查。
- 真实链路探针导入临时知识“星河屋（银河自习室）”，未知问法成功经实体增强检索返回带来源答案；探针文档验证后已删除。

## 2026-09-23 租户批量上传与文档清单

- 租户工作台文件选择器支持多选，逐篇导入并汇总成功、失败与实体抽取分块数。
- 新增管理员 `GET /knowledge/documents`，严格按 JWT tenant 从 Qdrant 聚合文档；返回标题、前三个非空行和全文。
- 新文档完整原文只保存在首分块，已有分块数据缺少该字段时兼容重建；工作台悬浮或键盘聚焦文件卡片可查看全文。
- TDD 证据：聚焦 RED 为 4 项目标行为缺失，GREEN 为 4/4；完整 unit + integration 为 140/140。重新构建后真实管理员登录探针列出示例租户 7 篇文档，API、worker、Qdrant 正常。

## 2026-09-23 多表示语义 RAG 与客户建议问题

- 入库新增 `summary/suggested_questions`，并纳入 Qdrant `search_text`；查询新增 DeepSeek 同义改写和低召回时的租户内语义证据选择。
- 语义选择严格限制为已有候选 `chunk_id`，最多读取 `RAG_SEMANTIC_CANDIDATE_LIMIT=40` 个公开、已审核、有效分块；模型失败保持原安全拒答。
- 新增 `GET /knowledge/suggestions`；客户页面显示“您可以这样提问”，点击后仅填充输入框。
- TDD 聚焦行为通过；完整 unit + integration 148/148，通过租户隔离、无关文档拒答、标题规范化、查询降级与引用边界测试。
- `deepseek-flash` 默认 thinking 曾耗尽结构化任务的短输出预算；已按官方参数仅对 JSON 索引/改写/选择设置 `thinking=disabled`，请求合约测试覆盖该行为。
- 真实 DeepSeek 迁移当前 2 篇文档、2/2 分块 AI 富化成功，客户接口返回 7 条有原文依据的建议问题；“小猫爱喝啥”命中《吴圣洁》，“你们这里有哪些班级”在当前个人资料库保持 `NONE`。临时课程语料中“可以学什么/有哪些班级”分别正确命中《英语常见问题》与《英语课程政策》，临时数据已删除。

## 2026-09-23 故障注入矩阵收口

- 新增 `make faulttest`，真实 stop/start Redis、NATS 和 worker，并以 `finally` 恢复服务。
- Redis 故障时实测 `live=200`、`ready=503`、入站 ACK=202，恢复后 `ready=200`。
- NATS 故障时实测 `ready=503`、入站 ACK=202，恢复后 `ready=200`。测试发现 ready 可被 NATS DNS/初始连接拖住，已增加 2 秒硬超时与无重连探针。
- worker 停止期间 12 条消息全部 ACK，`im-inbound-worker pending` 从 0 升到 12；worker 恢复后 pending/ack-pending 回到 0，无 redelivery。
- Qdrant 搜索异常由 HTTP 500 改为 fail-closed 的固定无依据回复，空引用，不调用 LLM 自由补事实。
- TDD 证据：NATS 探针硬超时与 Qdrant 故障拒答均先 RED 后 GREEN；最终 unit + integration `156 passed`，Mock LLM 下 E2E `10 passed`。
- 原始证据：`report/fault-matrix.json`；人类可读报告：`report/故障注入报告.md`。

## 2026-09-24 面试验收与演示收口

- 平台管理员入口收敛为 5 个策划租户（T01–T05）：星河未来成长中心、云杉学业服务中心、拾光艺术成长营、远航国际语言学院、启明星科学探索馆；每个租户有明确演示主题和独立账号，页面不再暴露冗长 UUID。
- 演示租户统一使用 Mock LLM，确保录屏结果稳定且不消耗外部额度；真实新建租户仍按 `.env` 使用 DeepSeek。
- 修复通用课程问法和 A3/S2/P1 等课程编号的实体增强规则；重新索引后，“有哪些班”“A3 什么时候上课”等问题可由租户知识库检索回答。
- 启用历史会话摘要：保留最近 20 条热消息，较早消息压缩写入 PostgreSQL 并在后续上下文中恢复。
- 测试入口增加 70% 覆盖率硬门禁，并通过独立 Compose 覆盖保证自动测试绝不调用付费 LLM；测试完成后自动恢复真实服务配置。
- `make acceptance` 新鲜通过：单元/集成 184 项，总代码覆盖率 76.41%；E2E 10 项；50 条路由安全样本与 5 条真实 Qdrant RAG 探针全部通过；PostgreSQL、Redis、NATS、Qdrant 就绪检查全部为 `ok`。
- 新增 `docs/acceptance-checklist.md` 和 `docs/demo-script.md`，分别用于逐项对照原题与执行 10–15 分钟面试演示；已知边界不包装为生产级结论。
