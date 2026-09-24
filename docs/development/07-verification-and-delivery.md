# P9/P10 全量验证与交付实施指南

## 1. 本阶段目标

用可重复证据证明系统满足题目，而不是在最后一天补“看起来像测试”的脚本。P9 负责测试、故障、压测和 LLM 评测；P10 负责全新环境复现、文档、演示和最终审计。

## 2. P9 测试结构

```text
tests/unit/          纯规则、状态机、Schema、错误分类
tests/integration/   PostgreSQL/Redis/NATS/Qdrant/mocks 的真实组合
tests/e2e/           题目 E2E-01..10，每个场景可独立运行
loadtests/           Locust 用户、场景和参数
evaluation/          50 条固定数据与离线评分器
```

测试不得访问真实付费 LLM、真实 IM、真实财务或公网服务。

## 3. 十个 E2E 的可观察断言

| ID | 场景 | 必须观察的结果和副作用 |
|---|---|---|
| E2E-01 | 课程政策 | 回复事实正确；至少一个 validated source 属于当前 tenant |
| E2E-02 | 发票 | 订单/金额/状态来自 mock；邮箱等 PII 已脱敏 |
| E2E-03 | 跨用户财务 | HTTP/业务结果 403；finance 调用 0；存在安全审计 |
| E2E-04 | 关闭续费 | 确认前 platform 调用 0；确认后 1；重复确认仍 1 |
| E2E-05 | 短时提醒 | 到期后 5 秒内 mock IM 收到一次；delivery 状态 delivered |
| E2E-06 | 转人工 | 单个 handoff；摘要/已尝试操作齐全；无多余 PII |
| E2E-07 | 财务超时 | 无金额/状态编造；明确暂不可查；审计失败结果 |
| E2E-08 | LLM 非法 JSON | 安全兜底；所有业务工具调用 0 |
| E2E-09 | 重复 message_id | 一个入站业务记录、一个业务效果、一个最终回复 |
| E2E-10 | 知识无命中 | 固定无依据回复并建议人工；来源为空 |

每个测试使用独立 tenant/ID，显式等待最终状态并设置总 timeout；不要用固定长 `sleep` 掩盖竞态。

## 4. 覆盖率

核心模块范围建议明确为 `app/services app/core app/utils`，目标 line coverage ≥70%，并单独检查 branch/error path。覆盖率不是退出条件的唯一证据：tenant、确认、幂等、引用、PII 等必须有明确反向测试。

```bash
./.venv/bin/python -m pytest --cov=app/services --cov=app/core --cov=app/utils \
  --cov-report=term-missing --cov-fail-under=70 -q
```

## 5. Locust

实现可配置场景：

- 500 WebSocket 连接；
- 200 msg/s 持续 5 分钟；
- 1000 msg/s 突发 30 秒并观察积压恢复；
- 财务查询 100 QPS；
- 20% LLM 超时的降级场景。

先运行 50 连接 smoke，验证 Locust/gevent/WebSocket client 兼容，再逐级加压。报告 `docs/loadtest-report.md` 必须记录：

- 日期、机器 CPU/内存/OS、Docker/Compose 版本；
- 当前代码快照说明（若执行时仍无 Git，则记录文件时间和不可追溯限制）；
- 容器资源限制、数据规模、用户数、spawn rate、持续时间；
- QPS、P50/P95/P99、错误率；
- outbox/NATS backlog、CPU、内存、恢复时间；
- 瓶颈、失败场景和未达标项。

不得通过删除错误请求、缩短未注明的时长或隐藏资源条件美化结果。

## 6. LLM 离线评测

`evaluation/dataset.json` 固定 50 条，覆盖 knowledge、finance auth、confirmation、no-evidence、dissatisfaction/handoff、style。每条至少含：id、tenant、input、fixture state、expected intent/action、allowed facts/sources、forbidden claims、是否应调用工具/转人工。

`evaluation/evaluate.py` 输出机器可读 JSON 和 `docs/llm-evaluation.md`，指标：

- 事实准确率；
- 引用命中率；
- 越权拒绝率（目标 100%）；
- 禁用语/少 AI 味规则评分；
- 转人工 precision/recall 或准确率；
- 工具安全通过率。

评分预期不能由被测回答生成；使用人工核准 fixture 和确定性规则。报告列出失败样本，不只给总分。

## 7. P10 文档与演示

### 7.1 `docs/api.md`

从实际 FastAPI OpenAPI 和事件 Schema 核对后编写：认证、HTTP、WebSocket、事件 envelope、错误结构、示例、幂等/确认语义。不要提前把未实现 API 写成已完成；未完成项明确标注。

### 7.2 README

必须包含：架构概览、前置环境、配置、`make up/migrate/test/loadtest/demo`、健康检查、服务端口、种子账号、常见故障、清理方式和已知限制。

### 7.3 演示

`make demo` 应可重复准备租户、用户、会话和知识数据，并按固定 ID 或安全 upsert 幂等运行。10–15 分钟演示按需求 §14 顺序，开始前从空环境完整走一次。

### 7.4 Agent 记录

`docs/agent-log.md` 记录关键 prompt/任务、影响模块、人工复核点、被拒绝或重写的生成内容，不伪造开发过程。

## 8. 全新环境最终 Gate

在明确的可丢弃测试 volume 上执行：

```bash
docker compose config
docker compose build --no-cache
make up
make migrate
curl -fsS http://localhost:8000/health/live
curl -fsS http://localhost:8000/health/ready
make seed
make test
make demo
make loadtest
```

然后执行仓库卫生检查：

```bash
rg -n -i 'api[_-]?key|password\s*=|bearer [A-Za-z0-9._-]+' \
  --glob '!*.example' --glob '!docs/**' .
find . -name '.env' -o -name '__pycache__' -o -name '*.pyc'
rg -n -i 'rabbitmq|aio[-_]?pika|pgvector|k6' \
  app mocks tests docker-compose.yml requirements.txt .env.example \
  README.md docs/architecture.md Makefile loadtests
```

命中需要人工分类，不能盲删测试 fixture 或历史说明。

## 9. 最终退出清单

- [ ] `make test` 全绿，核心覆盖率 ≥70%。
- [ ] E2E-01..10 可独立及整套运行。
- [x] 故障注入矩阵有可重复证据。
- [ ] Locust 规定场景完成，报告诚实记录环境和未达标项。
- [ ] 50 条评测可复现并列出失败样本。
- [ ] fresh Compose、空库迁移、live/ready、seed、demo 全通过。
- [ ] README、API、架构、报告、agent log、known issues 同步实际实现。
- [ ] 仓库无密钥、真实 PII、`.env`、缓存和虚拟环境交付污染。
- [ ] `PROGRESS.md` 保存最终命令、退出码和剩余风险。
