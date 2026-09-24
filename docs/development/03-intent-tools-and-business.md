# P3/P5/P6 意图、工具安全与业务集成实施指南

## 1. 为什么合并为一个板块

意图分类、工具注册、财务查询和平台指令共用一条安全执行管线。合并实现可确保身份、资源归属、确认、幂等、超时与审计只有一份规则；PLAN 仍按 P3→P5→P6 分阶段验收。

## 2. 目标结构

```text
app/schemas/{intent,tool,finance,command}.py
app/services/{intent_service,tool_service,finance_service,command_service}.py
app/clients/{finance_client,platform_client}.py
app/models/{intent_result,tool_execution,confirmation}.py
app/workers/message_worker.py
mocks/mock_{llm,finance,platform}/main.py
tests/unit/test_{intent,tool_policy,confirmation,retry}.py
tests/integration/test_{finance,platform_command}.py
tests/e2e/test_{02,03,04,07,08,09}_*.py
```

先完成 P3 的通用能力，再接 P5 财务，最后接 P6 平台写操作。

## 3. P3：意图与安全执行骨架

### 3.1 结构化分类

分类结果最少包含：

```text
intent, confidence, source(rule|llm|abstain),
parameters, risk_level, abstain_reason, model_version
```

规则优先覆盖显式转人工、财务、提醒、平台写操作和高风险表达；“人工智能/人工成本”等负例不得误触发转人工。正式租户仅在规则未命中时调用真实 LLM 做白名单分类，再进入租户隔离 RAG 或安全导航；LLM 不能直接执行工具。策展演示租户使用固定 Mock 回复，保证录屏结果稳定。

非法 JSON、未知 intent、超时或低置信度产生 `abstain`，策略选择澄清/转人工；不能默认执行最接近的工具。

### 3.2 分类与策略分离

`IntentResult` 只描述理解结果；`PolicyDecision` 决定 `answer/clarify/retrieve/request_confirmation/execute/handoff/refuse`。不要让分类器直接返回“已授权执行”。

保存意图、置信度、来源、版本和失败原因，以支持 P9 评测。

### 3.3 工具注册表

每个工具定义：

- 稳定名称和版本；
- Pydantic 参数 Schema；
- 允许角色；
- 风险级别与是否需要确认；
- 资源归属检查器；
- 超时、可重试错误、最大次数；
- 幂等键生成器；
- async handler。

执行顺序不可改变：

```text
解析 Schema → registry allowlist → RBAC → tenant/owner
→ confirmation → idempotency → timeout/retry → handler → audit
```

失败发生在哪一步，测试就断言后续步骤没有副作用。

### 3.4 高风险确认

状态：`proposed→pending_confirmation→confirmed→executing→succeeded/failed`，另有 `expired/cancelled`。

确认记录绑定 tenant、user、conversation、action、resource、args_hash 和 5 分钟 expires_at。只有同一认证用户对同一动作的明确肯定词才转换；模糊回复、改参后的旧确认、跨会话、跨用户和过期确认全部无效。

状态转换用条件更新/行锁，重复确认只能触发一次 execution。同一用户、会话、动作、资源和参数在有效期内重复提出时复用既有确认单；历史重复确认单执行时返回既有 execution，不能因幂等键冲突产生 HTTP 500。

## 4. P5：财务查询

### 4.1 Client 边界

`finance_client` 只处理 httpx 协议：请求/响应 Schema、connect/read timeout、状态码映射。它不决定谁有权查询。

`finance_service`：

1. 从 `AuthContext` 取 tenant/user；
2. 根据角色校验目标 user 和资源归属；
3. 越权 403，并写安全审计，下游调用 0 次；
4. 调 client；
5. 在任何 LLM/话术处理前递归脱敏；
6. 成功/超时/500/空响应写审计和确定性结果。

HTTP 财务中心与 AI 聊天均走审计边界；聊天成功结果先脱敏，再由确定性模板转为客服话术，不输出原始 JSON。

下游失败只能返回“暂时查不到及下一步”，不得出现模型生成的金额、订单状态或退费进度。

### 4.2 Mock finance

提供订单、账单、发票、退费、余额的固定 fixture；记录调用次数和 idempotency/request id；支持 delay、500、空响应。数据全部虚构且不同租户使用可区分 fixture。

## 5. P6：平台指令

先实现最小工具集：查询课程表、查询学习报告、提交请假、修改课程提醒、关闭自动续费。只有关闭自动续费属于首批高风险写操作。

### 5.1 幂等与重试

- 客户端幂等键先绑定 tenant、user 和 action；服务端另存规范化 resource + args 的 `request_hash`。同 key + 同 payload 返回原结果，同 key + 不同 payload 返回 `409 IDEMPOTENCY_CONFLICT`。
- 相同确认或消息重投返回既有 `tool_execution`，不再调用 mock-platform。
- 仅网络超时、连接错误和约定的 5xx 可重试；4xx、Schema、权限、归属失败不可重试。
- 重试使用有上限的指数退避加抖动；测试中注入 clock/sleeper，不能真的等待长时间。
- 平台写指令最多尝试 3 次，429/5xx/连接或超时才重试，每次复用同一幂等键。

### 5.2 Mock platform

按工具保存调用记录和最终资源状态，自动续费状态按 `(tenant_id,user_id)` 隔离，支持相同幂等键返回同一结果；支持 delay、可重试 500、不可重试 400。测试必须能查询调用次数来证明确认前为 0、确认后为 1。

## 6. 测试清单

### P3

- 规则命中不调用分类模型；模型失败产生 abstain。
- “转人工”与“人工智能”的正反例。
- 非法 JSON、未知工具、缺参、额外越权参数均不调用 handler。
- Prompt Injection 不能覆盖 tool allowlist、tenant、role 或确认。
- 低置信度澄清/转人工。
- 跨用户、跨会话、改参、模糊和过期确认不执行。

### P5

- E2E-02 发票结果正确且邮箱脱敏。
- E2E-03 跨用户财务 403、下游 0 调用、有安全审计。
- E2E-07 超时无虚构金额/状态，有明确降级。
- 审计与日志不含完整 PII。

### P6

- E2E-04 确认前 0 调用、确认后 1 调用、重复确认仍为 1。
- E2E-08 非法 LLM JSON 时工具 0 调用。
- 可恢复错误有限重试，不可恢复错误立即失败。
- 相同幂等键并发请求只产生一次业务效果。

## 7. 分阶段退出

P3 退出：安全管线与确认状态机通过，业务 handler 可用 fake 验证。

P5 退出：E2E-02/03/07 通过，脱敏发生在 LLM 前。

P6 退出：E2E-04/08/09 通过，副作用幂等且审计完整。

每个阶段同步 `docs/api.md` 中的请求、响应、错误码和确认交互示例；新增配置同步 `.env.example`。
