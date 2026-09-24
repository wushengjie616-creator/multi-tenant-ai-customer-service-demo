# API 与事件契约

所有业务 HTTP 请求使用 `Authorization: Bearer <JWT>`。JWT 中的 `tenant_id/sub/role` 是唯一可信身份来源；请求体中的身份字段不能覆盖它。统一业务错误为：

```json
{"error":"FORBIDDEN","message":"无权访问","detail":{}}
```

## HTTP

| 方法与路径 | 角色 | 用途 |
|---|---|---|
| `POST /auth/login` | public | 租户、邮箱、密码登录 |
| `GET /users/me` | authenticated | 当前用户资料 |
| `GET /demo/config` | public, demo only | 前端预填的非敏感演示配置；`DEMO_MODE=false` 时 404 |
| `POST /demo/customer-session` | public, demo only | 为独立的固定示例租户客户签发 30 分钟 JWT；不使用可写工作台租户；数据未初始化时 503 |
| `GET /demo/platform/tenants` | public, demo only | 平台管理员演示：列出多个租户及其模拟客户、账号和会话规模 |
| `GET /handoffs/active` | user | 查询当前会话的人工客服状态 |
| `POST /admin/handoffs/{id}/accept` | admin | 人工客服接入会话 |
| `POST /admin/handoffs/{id}/reply` | admin | 人工客服回复客户 |
| `POST /admin/handoffs/{id}/request-close` | admin | 发起结束，等待客户确认 10 分钟 |
| `POST /handoffs/{id}/close-response` | user | 客户确认结束或要求继续 |
| `POST /demo/platform/tenants/{id}/session` | public, demo only | 选择租户和模拟客户，签发租户管理员及客户视角会话 |
| `POST /demo/workbench-session` | admin, demo only | 已有管理员登录后恢复对应租户工作台 |
| `POST /webhooks/im/messages` | user | 入站消息，成功快速返回 202 |
| `GET /conversations/{id}/messages?after_message_id=` | owner/agent/admin | 全量历史或游标后的消息补取 |
| `GET /knowledge/documents` | admin | 列出当前租户知识文档、标题、前三个非空行和全文 |
| `GET /knowledge/suggestions` | authenticated | 返回当前租户知识分块生成的去重建议问题，默认最多 8 条 |
| `POST /knowledge/documents` | admin | 按 Markdown 块导入/替换知识文档，支持版本与有效期元数据 |
| `POST /knowledge/reindex` | admin | 删除当前租户的旧文档分片后显式重建索引 |
| `POST /knowledge/query` | authenticated | 有证据回答并返回服务端校验引用 |
| `GET /finance/{invoice|order|bill|refund|balance}` | owner/authorized | 财务查询，返回前递归脱敏 |
| `POST /commands/close-auto-renew` | user | 创建 5 分钟确认请求，不执行副作用 |
| `POST /commands/confirm/{id}` | same user | 原子确认并幂等执行一次 |
| `GET /platform/{course-schedule|study-report}` | authenticated | 按 token 身份查询课程表/学习报告 |
| `POST /commands/{submit-leave|update-course-reminder}` | conversation owner | 低风险平台写操作，要求客户端幂等键；同 key 更换资源/参数返回 `409 IDEMPOTENCY_CONFLICT` |
| `POST/GET /reminders` | user | 创建/查询自己的提醒；`lead_time_minutes` 控制提前通知 |
| `POST /reminders/parse` | conversation owner | 把自然语言时间解析为 `needs_confirmation=true` 的候选；绝不直接创建 |
| `PATCH/DELETE /reminders/{id}` | owner | 乐观版本修改/取消 |
| `POST /handoffs` | conversation owner | 幂等创建人工转接并投递上下文包；可携带 `message` 保存不触发 AI 的离线留言 |
| `GET /admin/handoffs` | admin | 查看当前租户的人工会话队列 |
| `POST /admin/handoffs/{id}/reply` | admin | 向当前租户人工会话发送坐席回复；跨租户请求返回 403 |
| `POST /admin/users` | admin | 在当前 JWT 租户中新建管理员，返回一次性展示的租户 ID、邮箱和初始密码 |

Mock IM 入站 WebSocket 使用 `/ws?conversation_id=<id>&token=<customer-jwt>`；Mock 会将该 token 作为 Bearer 凭据转发到真实 webhook，缺 token 时以 `1008` 拒绝连接。该 query token 仅用于本地 Mock，生产 IM 集成应使用平台签名而非 URL token。
| `GET /admin/llm-usage` | admin | 按当前租户、会话聚合 LLM 调用次数、token 与配置单价成本 |
| `GET /admin/dead-letters` | admin | 查看当前租户脱敏死信原因 |
| `POST /admin/dead-letters/{id}/replay` | admin | 从原始消息重建事件并幂等重放 |
| `GET /health/live` / `GET /health/ready` | public | 存活/依赖就绪 |
| `GET /metrics` | public（本地） | Prometheus 文本指标 |

`POST /knowledge/documents` 中 `content` 的上限由 `DEMO_MAX_UPLOAD_CHARS` 配置，超限返回 HTTP 413。浏览器页面可一次选择多份 `.md/.markdown/.txt` 文件，在本地逐篇读取后调用该 JSON API，不额外引入对象存储。成功响应中 `enrichment` 统计本次由 DeepSeek 抽取的 `llm` 分块和规则降级的 `fallback` 分块数。

`GET /knowledge/documents` 的租户范围只取自管理员 JWT。新导入文档只在首分块额外保存一次完整原文，列表接口按 `document_id` 聚合分块；旧数据没有完整原文字段时按 `chunk_index` 兼容重建。响应中的 `preview_lines` 是全文前三个非空行，`content` 供本地工作台悬浮查看。

`GET /knowledge/suggestions` 的租户范围同样只取自 JWT。问题来自入库时生成的 `suggested_questions`；旧分块没有该字段时按文档标题生成保守兜底问法。客户页面点击建议只填充输入框，不会自动发送。

## WebSocket

连接 `WS /ws?conversation_id=<uuid>[&after_message_id=<id>]`，Bearer token 必须放在 `Authorization` header。服务端在握手前验证 token、角色和 conversation owner。带游标重连时首先返回 `connection.resumed` 及游标后的持久消息。

客户端发送：

```json
{"type":"message.send","payload":{"message_id":"client-001","content":"你好"}}
```

服务端先返回 `message.accepted`。对用户可见的 LLM 生成，worker 直接消费上游 SSE，通过不持久化的 NATS Core 主题立即转发 `reply.start` 和带 `sequence/delta` 的 `reply.chunk`；完整结果落 PostgreSQL/outbox 后再发 `reply.end`。因此首块不需等待整段生成，而断线重连仍以持久化完整消息恢复。非 LLM 回复保持 start/chunk/end 兼容格式。

RAG 对短追问中的“那个/它/多少钱/呢”等指代，先使用当前租户、当前会话的持久化摘要与最近消息改写为独立检索问句，再进入原有证据门和服务端引用校验；改写失败会保守降级，不会跨会话或跨租户取上下文。

AI 转人工和 `POST /handoffs` 都会额外执行一次受约束的摘要请求，生成 `summary / intent / attempted_actions / risk_notes / recent_turns`，并在坐席工作台展示。摘要异常时使用确定性摘要，且写入前统一脱敏。

## 事件封装

```json
{
  "event_id":"uuid",
  "trace_id":"uuid-or-caller-id",
  "tenant_id":"uuid",
  "traceparent":"00-<trace-id>-<span-id>-01",
  "occurred_at":"RFC3339",
  "schema_version":"1.0",
  "type":"im.inbound|im.outbound|reply.start|reply.chunk|reply.end",
  "payload":{}
}
```

消息以 `(tenant_id,message_id)` 去重。高风险确认摘要绑定 tenant、user、conversation、action、resource 和规范化参数；重复确认返回同一 execution，不重复调用平台。
