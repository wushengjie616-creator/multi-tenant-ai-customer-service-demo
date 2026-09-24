# P7 提醒与人工转接实施指南

## 1. 本阶段目标

交付两个独立但共享消息投递能力的闭环：持久化提醒能在多实例和重启下准时且不重复；人工转接有显式状态、上下文包和在线/离线行为。

前置：P2 outbox/NATS 可用，P3 工具安全与会话状态可用。

## 2. 目标文件

```text
app/models/{reminder,reminder_delivery,handoff}.py
app/schemas/reminder.py
app/services/{reminder_service,handoff_service}.py
app/api/reminders.py
app/workers/reminder_worker.py
mocks/mock_im/main.py
migrations/versions/<reminder_handoff_revision>.py
tests/unit/test_{schedule,reminder_state,handoff_state}.py
tests/integration/test_{reminder_delivery,handoff}.py
tests/e2e/test_05_reminder.py
tests/e2e/test_06_handoff.py
```

## 3. 提醒

### 3.1 数据模型

`reminders`：tenant/user/conversation、原始时区、规则、next_run_at UTC、status、version。

`reminder_deliveries`：reminder_id、occurrence_at、idempotency_key、status、attempts、lease_until、delivered_at、last_error；`(reminder_id,occurrence_at)` 唯一。

状态：

```text
active → due → dispatching → delivered
                    └→ retrying → delivered
                               └→ dead_letter
active → cancelled
```

### 3.2 时间与规则

- 输入保留 IANA timezone，默认 `Asia/Shanghai`；落库执行时间为 UTC aware datetime。
- 支持单次、每天、每周、工作日和提前 N 分钟。
- 自然语言解析只覆盖题目演示所需的明确集合；无法唯一解析时澄清，不猜时区/日期。
- clock 必须可注入，测试不得依赖真实“明天”或本机时区。
- 重复提醒在成功生成 occurrence 后计算下一次；取消后不再产生新 occurrence。

### 3.3 多实例调度

scheduler 周期扫描 `next_run_at <= now`：

1. 事务内 `FOR UPDATE SKIP LOCKED` 抢占；
2. 插入唯一 delivery；
3. 写 `reminder.due` outbox；
4. 推进 next_run_at/状态；
5. commit。

消费者通过 delivery idempotency key 写 `im.outbound`，NATS/进程重投不能重复推送。失败有限重试，最终死信和指标。

### 3.4 API

创建/查询/修改/取消全部使用 JWT tenant/user；修改使用 version 或条件更新防止并发覆盖。返回解析后的绝对时间、时区和重复规则，便于用户确认。

## 4. 人工转接

客户页面每 2 秒轮询当前会话的人工状态。客服发起结束后，确认提示必须作为跨分页全局提示显示，不能只放在“办事服务”面板内；正式租户的工作台 AI 测试区也需显示待处理、处理中、待确认结束和 AI 恢复状态。

`offline_message` 在客服尚未接入时不暂停 AI，也不作为客户的活动人工会话；一旦客服接入或发起结束，它必须重新进入客户可见的活动状态，允许客户确认结束或要求继续服务。

### 4.1 触发

- 明确“转人工”立即触发；
- 连续两次不满触发；成功解答、人工接管、会话关闭后重置计数；
- 低置信度、策略风险、无证据、关键下游失败可由策略层触发。

### 4.2 状态机

```text
bot_active → pending（待处理）→ in_progress（处理中）
→ awaiting_confirmation（等待客户确认结束）→ ended（已结束）

人工客服只能从独立 `/ui/agent.html` 工作台接入、回复和发起结束。客户可确认结束或要求继续；结束请求超过 10 分钟未回应时由 scheduler 自动关闭并释放 `active_key`。
                 └────────────→ bot_active（未接单前取消/恢复）
```

- 状态查询和更新使用 tenant + conversation；
- 重复触发只保留一个活动 handoff；
- 重复接单/关闭幂等，只在真实变化时审计；
- actor 来自认证上下文；
- `human_active` 时 worker 禁止自动 AI 回复；
- 没有坐席接单时不能进入 human_active。

### 4.3 转接包

包含会话摘要、当前意图、已尝试操作、失败原因、风险提示、trace_id；通过集中脱敏移除无关 PII。不得把完整聊天记录无筛选地发送给坐席。

mock IM 提供坐席在线状态和接单接口：在线返回排队/接入，离线返回等待或留言选项，不伪造已接入。

## 5. 测试清单

提醒：

- 时区、夏令时边界、单次/每日/每周/工作日、提前提醒；
- 两个 scheduler 并发只创建一个 delivery；
- scheduler 在 commit 前后崩溃均不丢不重；
- worker 重投不重复 push；
- 修改/取消后旧 occurrence 不错误投递；
- E2E-05 短时提醒在到点后 5 秒内到 mock IM。

转人工：

- “转人工”一次产生一个 handoff；“人工智能”不触发；
- 两次不满触发且重置规则正确；
- 跨租户接单/关闭失败；
- 在线和离线行为不同且真实；
- human_active 状态下 AI 回复调用/推送为 0；
- 转接包完整但无完整 PII；
- E2E-06 携带摘要和已尝试操作。

## 6. 验证与退出

```bash
./.venv/bin/python -m pytest tests/unit -k 'schedule or reminder or handoff' -q
./.venv/bin/python -m pytest tests/integration -k 'reminder or handoff' -q
docker compose run --rm api pytest tests/e2e -k 'reminder or handoff' -q
```

- [ ] 提醒迁移可从上一版本升级。
- [ ] 时间规则使用可注入 clock 并保存原时区。
- [ ] 多实例抢占与 occurrence 幂等有集成测试。
- [ ] 人工转接状态机、在线/离线和 AI 抑制有测试。
- [ ] API/事件契约同步到 `docs/api.md`。
