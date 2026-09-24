# whynotai 客服项目设计借鉴说明

> 文档性质：外部项目调研与实施参考，不替代本仓库的 `项目需求拆解.md`、`PLAN.md` 和 `docs/architecture.md`。发生冲突时，以前三者及题目原文为准。
>
> 调研时间：2026-09-23
>
> 参考项目：`/Users/wushengjie/project/whynotai`

## 1. 调研结论

`whynotai` 已经实现了较完整的客服链路，适合借鉴其中经过约束和测试验证的设计，但不适合整套复制。本项目只有约五天的交付窗口，应采用“复用设计原则、用 Python 重新实现最小版本”的方式。

建议优先借鉴六项能力：

1. Qdrant 租户过滤由基础设施适配器强制注入并做失败关闭，而不是依赖业务调用者自觉传参。
2. 检索结果先经过证据强度判断和回答策略，再允许 LLM 生成答案。
3. LLM 只返回结构化引用 ID；服务端验证引用确实来自本次检索结果，再生成用户可见来源。
4. 会话和人工转接使用显式状态机，人工接管期间禁止 AI 继续自动回复。
5. 消息发布采用数据库 Outbox、幂等键、租约抢占、有限重试和死信/终止状态。
6. 示例知识库以 Markdown 文件为源，导入前全量校验，按内容哈希幂等写入，并在写入后核验。

不建议现在复制的能力包括：20 阶段完整流水线、稠密/稀疏混合检索与 RRF、MMR、多模态理解、HyDE、语义答案缓存、反馈驱动排序、复杂行业包和 Java/Python 双服务拆分。这些能力会显著扩大实现与测试范围，而且不是当前题目的必要条件。

## 2. 参考代码基线与边界

本次调研以本地代码快照为准：

| 模块 | 分支 | HEAD | 工作区状态 |
|---|---|---|---|
| `whynotai/code/ai` | `feature-wushengjie` | `a6a605788071039073de12c852a9f87cbee333f3` | clean |
| `whynotai/code/backend` | `feature-wushengjie` | `017698238e5445f33b401b04306ffa9d5fef3408` | 存在未跟踪 `dump.rdb`，未读取、未使用 |

边界说明：

- 本文只借鉴架构、约束、接口形态和测试思路，不直接复制业务数据、密钥、环境配置或生产文件。
- 未读取或复制参考项目中的 `.env.production` 等敏感配置。
- 参考项目后续如有更新，本文不会自动同步；实现 Agent 应以本节记录的代码快照理解结论。
- 参考项目规模大于当前面试项目；本文已经主动裁剪功能。

## 3. 推荐的精简客服处理链路

参考项目生产链路有约 20 个阶段。当前项目建议压缩为 8 个阶段，并保持每个阶段输入、输出和失败策略明确：

```text
HTTP / WebSocket / NATS 入站
          │
          ▼
1. input_guard          输入长度、注入、高风险词、显式转人工
          │
          ▼
2. intent_route         规则优先，结构化分类，允许 abstain
          │
          ▼
3. context_load         Redis 近期上下文 + PostgreSQL 持久记录
          │
          ▼
4. retrieve_or_plan     知识检索，或生成候选工具调用
          │
          ▼
5. authorize_execute    Schema→allowlist→鉴权→归属→确认→执行
          │
          ▼
6. answer_policy        回答 / 澄清 / 拒答 / 转人工
          │
          ▼
7. output_guard         引用校验、敏感信息、越权与危险表达检查
          │
          ▼
8. trace_persist        消息、审计、trace、outbox 持久化
```

不要在 P1 就搭建通用 Pipeline 框架。P3/P4 出现多个稳定阶段后，再抽象统一的 `Stage` 协议。若抽象，应至少支持：

- 阶段名称、前置字段和产出字段；
- 单阶段超时；
- 明确的 fallback 或失败终止；
- 阶段耗时和错误记录；
- 启动时验证阶段依赖顺序；
- 有上限的重试，禁止任意回跳。

### 3.1 建议的会话上下文

可以用一个强类型对象在阶段间传递状态，字段按需要逐步增加：

```text
ConversationContext
├── identity: tenant_id, user_id, roles
├── request: trace_id, conversation_id, message_id, text
├── intent: name, confidence, source, abstain_reason
├── retrieval: chunk_ids, scores, evidence_level
├── tool: requested_tool, validated_args, execution_result
├── policy: action, reason, handoff_reason
├── answer: text, requested_citations, validated_citations
└── diagnostics: stage_latency_ms, errors, downstream_status
```

关键原则：分类结果和执行策略必须分开。`intent=refund` 不等于“立即调用退款工具”；策略层仍要检查权限、资源归属、风险确认和证据。

## 4. 分模块借鉴方案

### 4.1 意图识别：规则优先，模型失败可降级

参考设计：确定性规则始终先执行；只有未命中时才调用分类器。分类器超时、异常或低置信度时输出 `abstain`，不能阻断消息主链路。

本项目建议的结构化结果：

```json
{
  "intent": "knowledge_qa",
  "confidence": 0.91,
  "source": "rule|llm|abstain",
  "abstain_reason": null
}
```

实施要求：

- P3 先实现少量高确定性规则：显式转人工、财务、提醒、平台写操作、高风险表达。
- “人工”一词不能单独触发转接，例如“人工智能”“人工成本”应被负向规则排除。
- mock LLM 只补充规则未覆盖的分类，并用固定 Schema 校验。
- 非法 JSON、未知 intent 或超时统一降级为澄清或转人工，不猜测执行工具。

### 4.2 Qdrant：租户安全适配器必须失败关闭

参考项目的 `TenantSafeQdrantClient` 会在请求发往 Qdrant 前验证 `filter.must` 中存在正确的 `tenant_id` 精确匹配；只有 `should` 或 `must_not` 不算租户约束，错误租户也会立即拒绝。

当前项目建议实现 `TenantScopedVectorStore`，业务代码不得直接持有原始 Qdrant client。适配器统一提供：

- `upsert_chunks(tenant_id, ...)`：强制把可信上下文中的 tenant 写入 payload，忽略调用参数中的伪造值；
- `search(tenant_id, ...)`：强制添加 `tenant_id == ...` 的 `must` filter；
- `scroll(tenant_id, ...)` 和 `count(tenant_id, ...)`：相同约束；
- `delete_document(tenant_id, document_id)`：tenant 与 document 双条件删除；
- 批量查询逐项验证，任一子查询缺少租户条件则整批拒绝；
- 安全校验失败时，下游 Qdrant 调用次数必须为 0。

除了向量检索，PostgreSQL、Redis key 和审计查询也必须使用同一可信 `tenant_id`，它只能来自已验证 JWT 或可信事件 envelope。

### 4.3 知识数据治理：默认不可公开、默认未审批

参考项目把可见性、审批状态和有效期作为知识片段的一等属性。当前项目可采用裁剪后的 payload：

| 字段 | 用途 | 建议规则 |
|---|---|---|
| `tenant_id` | 租户隔离 | 必填、keyword index、来自可信上下文 |
| `document_id` | 文档稳定标识 | 必填，同一租户内稳定 |
| `chunk_id` | 引用与幂等 | 必填，确定性生成 |
| `title` | 来源展示 | 必填 |
| `source` | 原始来源 | 必填，不存敏感路径 |
| `version` | 版本追踪 | 必填 |
| `visibility` | 可见性 | 缺省为 `internal`，不允许缺省公开 |
| `review_status` | 审批状态 | 缺省为 `pending`，只检索 `approved` |
| `effective_from` | 生效时间 | 可选 |
| `effective_until` | 失效时间 | 可选 |
| `content_hash` | 幂等与变更检测 | 必填 |
| `chunk_index` | 顺序与定位 | 必填 |

客服检索条件至少是：

```text
tenant_id == authenticated_tenant
AND visibility == public
AND review_status == approved
AND effective_from <= now（若存在）
AND effective_until > now（若存在）
```

有效期检查可在应用层完成，但必须使用可注入的当前时间，保证测试确定性。

### 4.4 检索：先做稳定基线，不复制复杂融合

P4 建议只实现：

1. 确定性 mock embedding；
2. Qdrant tenant + governance filter；
3. 适度 over-fetch；
4. 分数阈值过滤；
5. 取 top-k；
6. 保留 `chunk_id/document_id/title/source/version/score`。

定义三个证据等级即可：

- `SUPPORTED`：存在达到阈值的有效片段，可以生成基于知识的回答；
- `WEAK`：有近似结果但证据不足，澄清或提示转人工；
- `NONE`：无结果，固定拒答，禁止 LLM 根据常识补全企业事实。

复杂的 dense+sparse、RRF、MMR、query expansion 最多重试一次等模式可以记录为后续优化，不应进入当前五天关键路径。

### 4.5 回答策略：确定性规则先于生成

策略层推荐按以下顺序决策：

1. 输入越权、Prompt Injection 或禁止请求：拒答并审计；
2. 明确转人工：直接转接；
3. 工具执行需要确认：进入确认状态；
4. 意图不清：澄清；
5. 知识问答且证据为 `NONE/WEAK`：固定拒答或转人工；
6. 有充分证据：允许生成；
7. 普通闲聊：简短回答。

这些分支应写成可单元测试的普通代码，而不是只写在 system prompt 中。

### 4.6 引用校验：来源由服务器生成

LLM 的知识回答建议固定为：

```json
{
  "answer": "……",
  "citations": ["chunk-001", "chunk-004"]
}
```

输出保护阶段必须：

1. 建立本次检索允许引用的 `chunk_id` 集合；
2. 删除不在集合中的引用；
3. 去重同一文档/位置的来源；
4. 用户可见来源只从已验证 chunk 的 payload 生成，绝不采用 LLM 自由生成的标题或 URL；
5. 知识型回答要求引用但有效引用为 0 时，替换为固定拒答或转人工；
6. 一部分引用合法、一部分伪造时，可以保留合法引用，同时记录 guard 告警。

MVP 不必再调用第二个 LLM 做 grounding judge；确定性引用检查、证据阈值和固定策略已经能覆盖核心风险。

### 4.7 工具注册表：借鉴形态，加强授权

可以借鉴“名称 + 描述 + 参数 Schema + async handler”的注册表，但当前项目必须保留 `PLAN.md` 已冻结的执行管线：

```text
Schema 校验
→ 工具 allowlist
→ JWT/RBAC 鉴权
→ tenant 与资源归属
→ 高风险确认
→ 幂等键
→ 超时控制
→ 执行
→ 审计
```

LLM 输出只是一份“候选调用计划”，不是执行授权。任何前置检查失败时，handler 调用次数都必须为 0。

### 4.8 人工转接：显式状态机

当前项目建议的最小状态机：

```text
bot_active
    │ 明确请求 / 连续不满 / 低置信度 / 策略风险 / 下游故障
    ▼
handoff_pending
    │ 坐席接单                         │ 用户取消且尚未接单
    ▼                                  └──────────► bot_active
human_active
    │ 坐席关闭 / 用户结束
    ▼
closed
```

约束：

- 转接原因使用小型枚举：`explicit_request`、`repeated_dissatisfaction`、`low_confidence`、`downstream_failure`、`policy_risk`、`out_of_scope`。
- 状态变更必须按 `tenant_id + conversation_id` 查询，并记录认证 actor。
- 重复接单、重复关闭应幂等；只在真实状态变化时新增审计记录。
- `human_active` 状态下 worker 必须抑制 AI 自动回复。
- 转接摘要包含意图、已尝试动作、失败原因和风险提示，但不复制无关 PII。
- 无坐席在线时进入留言/等待策略，不伪造“已接入人工”。

### 4.9 Outbox：可靠发布与有界重试

参考项目的可靠性模式可以直接映射到 P2：

```text
PENDING → IN_FLIGHT → SUCCESS
              │
              ├── 可恢复失败 → PENDING（延迟重试）
              ├── 达到上限   → FAILED / DLQ
              └── 会话关闭   → CANCELLED
```

建议实现：

- 业务消息和 outbox 在同一 PostgreSQL 事务写入；
- `idempotency_key` 唯一约束；
- relay 原子抢占一批 pending 记录并设置 lease；
- 进程崩溃后可回收过期 lease；
- 收到 NATS JetStream publish ACK 后才标记成功；
- 重试次数有上限，退避时间可在测试中注入；
- 错误文本截断并脱敏；
- 会话关闭后取消尚未发送的人工/AI回复，避免迟到消息。

### 4.10 可观测：记录决策，不记录敏感全文

推荐 trace 字段：

```text
trace_id, tenant_id, conversation_id, message_id,
intent, intent_confidence, intent_source,
evidence_level, retrieved_chunk_ids, validated_citation_ids,
policy_action, handoff_reason,
stage_latency_ms, downstream_status, errors
```

默认不要把完整知识片段、完整用户输入、token、财务值或 PII 写入日志。需要调试时也应使用受控采样和脱敏结果。

## 5. 示例客户知识库方案

可以现在并行准备示例客户知识库，它与 P1/P2 的主体开发冲突很小。建议目录：

```text
sample-data/tenants/demo-school/
├── tenant.json
└── knowledge/
    ├── 001-school-overview.md
    ├── 002-course-faq.md
    ├── 003-refund-policy.md
    ├── 004-class-schedule-rules.md
    └── 005-contact-and-handoff.md
```

每个 Markdown 文件使用 frontmatter：

```yaml
---
document_id: demo-school-course-faq
title: 课程常见问题
version: "1.0"
visibility: public
review_status: approved
effective_from: 2026-01-01T00:00:00+08:00
effective_until: null
source: demo-school/knowledge/002-course-faq.md
tags: [课程, FAQ]
---
```

导入器契约：

1. 默认只 dry-run，显式 `--apply` 才写入；
2. 写入前扫描并校验全部文件，任何文件非法则零写入；
3. 计算规范化内容的 SHA-256 `content_hash`；
4. 使用稳定 `document_id` 和确定性 `chunk_id`；
5. 相同版本和 hash 重跑不重复写入；
6. 内容变化时执行版本化 upsert，并清理该文档旧 chunk；
7. 写入后读取 Qdrant payload 核验 hash、chunk 数和 tenant；
8. 测试数据全部虚构，不使用真实学校、学生、手机号、订单或财务信息。

建议的切片基线：按 Markdown 标题分段，再按字符/token 上限切片，保留少量 overlap。当前阶段不要引入复杂语义切片。

## 6. 映射到当前 PLAN

| 当前阶段 | 借鉴内容 | 优先级 | 本阶段完成定义 |
|---|---|---:|---|
| P1 数据/安全 | 所有实体租户过滤、可信 tenant 来源、脱敏 trace 字段 | 必须 | 反向隔离测试可证明删除 tenant 约束会失败 |
| P2 IM/NATS | Outbox 状态、幂等键、lease、ACK 后成功、过期 lease 回收 | 必须 | 崩溃恢复和重复投递不产生重复回复 |
| P3 意图/工具 | 规则优先、abstain、分类与策略分离、候选工具非授权 | 必须 | 非法输出和越权时 handler 调用为 0 |
| P4 Qdrant | `TenantScopedVectorStore`、治理 payload、证据等级、引用校验 | 必须 | 跨租户、伪造引用、无证据拒答测试通过 |
| P5/P6 | 工具 registry 形态、资源归属、固定降级 | 必须 | 下游故障不编造结果，副作用幂等 |
| P7 人工 | 显式状态机、转接原因、坐席状态抑制 AI、离线策略 | 必须 | 在线/离线/重复操作均有测试 |
| P8 可观测 | 阶段耗时、policy action、引用 ID、下游状态 | 应做 | 能通过 trace 定位一次失败链路且无敏感全文 |
| P9 测试/评测 | 安全负向测试、引用准确率、转人工准确率 | 必须 | 纳入 E2E 和 50 条固定评测集 |
| P10 交付 | 示例知识库、dry-run 导入、演示脚本 | 应做 | 全新环境可重复导入和演示 |

以下项目延期，不应阻塞 P0–P10：

- dense+sparse 混合检索、RRF、MMR；
- HyDE、自动 query expansion；
- 多模态图片/音频理解；
- 语义答案缓存和基于反馈的排序提升；
- 复杂知识图谱/行业 taxonomy；
- 二次 LLM grounding judge；
- 拆分独立 Java 后端；
- 全量复制参考项目的 feature flag 和 shadow traffic 系统。

## 7. 开发 Agent 必须补的测试

### 7.1 Qdrant 与知识问答

- 缺少 tenant filter 时，在发送 HTTP 请求前失败；
- tenant 放在 `should` 或 `must_not` 中仍然失败；
- tenant 值错误时失败且底层 client 调用次数为 0；
- batch 中任一查询非法，整批失败；
- upsert 的 tenant 不能被请求 payload 覆盖；
- 租户 A 搜不到租户 B 的 chunk；
- `internal`、`pending`、未生效和已过期文档不可检索；
- LLM 伪造 chunk ID 时来源为空并触发拒答/转人工；
- 合法与伪造引用混合时只保留合法来源；
- 多个 chunk 指向同一文档/位置时来源去重；
- over-fetch 但未被实际引用的文档不能展示为来源。

### 7.2 意图、策略和工具

- 分类器超时/非法 JSON 时主链路按策略降级；
- “转人工”触发，但“人工智能”“人工成本”不触发；
- 低置信度不执行工具；
- 未知工具、缺参、越权资源和过期确认时 handler 调用为 0；
- Prompt Injection 不能覆盖身份、tenant、allowlist 或确认状态。

### 7.3 Outbox 与转人工

- 相同幂等键只产生一条业务效果；
- relay 抢占具有原子性，并发实例不重复拥有同一 lease；
- relay 崩溃后过期 lease 可回收；
- NATS 未 ACK 不得标记成功；
- 重试达到上限进入 FAILED/DLQ，不能无限重试；
- 重复接单/关闭幂等，跨租户操作失败；
- `human_active` 时 AI 回复被抑制；
- 会话关闭后 pending 出站消息取消或被明确拦截。

### 7.4 知识库导入

- dry-run 不产生任何写入；
- 任一 Markdown metadata 非法时整批零写入；
- 相同内容重复导入不新增 point；
- 内容变化会更新 hash 和对应 chunk；
- 导入后 tenant、文档数、chunk 数和 hash 校验一致。

## 8. 参考源码索引

以下文件是本文结论的主要依据，开发时可按主题定点阅读，不建议无目标浏览整个参考仓库。

| 主题 | 参考文件 |
|---|---|
| 通用阶段框架 | `/Users/wushengjie/project/whynotai/code/ai/app/core/pipeline.py` |
| 客服完整流水线 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/pipeline/pipeline.py` |
| 客服上下文 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/pipeline/context.py` |
| 意图路由 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/intent/router.py` |
| 意图模型 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/intent/models.py` |
| 输入保护 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/pipeline/stages.py` |
| 检索阶段 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/pipeline/stages_retrieve.py` |
| 证据判断 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/pipeline/stages_evidence.py` |
| 回答策略 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/pipeline/stages_policy.py` |
| 工具计划与执行 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/pipeline/stages_tool.py` |
| 工具注册表 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/pipeline/tool_registry.py` |
| 输出保护 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/pipeline/stages_output_guard.py` |
| Trace 持久化 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/customer_service/application/pipeline/stages_trace.py` |
| Qdrant 安全适配器 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/knowledge/infrastructure/qdrant_safe.py` |
| 知识治理属性 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/knowledge/domain/axes.py` |
| 知识导入流水线 | `/Users/wushengjie/project/whynotai/code/ai/app/modules/knowledge/application/pipeline/pipeline.py` |
| Qdrant 安全测试 | `/Users/wushengjie/project/whynotai/code/ai/tests/knowledge/test_qdrant_safe.py` |
| 引用与来源测试 | `/Users/wushengjie/project/whynotai/code/ai/tests/customer_service/test_answer_sources.py` |
| Outbox 服务 | `/Users/wushengjie/project/whynotai/code/backend/src/main/java/com/gscq/cs/OutboxService.java` |
| 人工转接服务 | `/Users/wushengjie/project/whynotai/code/backend/src/main/java/com/gscq/cs/CsHandoffService.java` |
| 客户身份服务 | `/Users/wushengjie/project/whynotai/code/backend/src/main/java/com/gscq/cs/CustomerService.java` |
| 示例知识库导入 | `/Users/wushengjie/project/whynotai/code/backend/scripts/seed_data/load_industry_kb.py` |
| 示例知识文档 | `/Users/wushengjie/project/whynotai/code/backend/scripts/seed_data/industry_kb/IND_BAS/` |

## 9. 给开发 Agent 的使用说明

1. 先读当前仓库 `项目需求拆解.md`、`PLAN.md`、`PROGRESS.md` 和 `docs/architecture.md`，再读本文。
2. 本文是设计参考，不授权更换已冻结的 Python + FastAPI + PostgreSQL + SQLAlchemy + Alembic + Redis + NATS JetStream + Qdrant + httpx + pytest + Locust + Docker Compose 技术栈。
3. 按 PLAN 当前阶段逐项吸收，不要提前搭建所有高级能力。
4. 借鉴参考项目时，用当前仓库命名和模型重新实现；不要复制环境文件、业务数据或无关框架。
5. 每一项安全约束都必须有负向测试证明；仅有正向测试不能算完成。
6. 如果本文与实际代码或 PLAN 出现偏差，记录到 `PROGRESS.md`，不要静默扩大范围。
