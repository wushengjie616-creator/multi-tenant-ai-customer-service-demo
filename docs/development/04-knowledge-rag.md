# P4 Qdrant 知识问答实施指南

## 1. 本阶段目标

把示例客户 Markdown 知识稳定、幂等地导入 Qdrant；检索时强制租户与治理过滤；只有证据充分且引用合法时才返回知识答案。

详细参考设计见 `docs/reference/whynotai-customer-service-design-reference.md`。当前已实现小型租户知识库适用的实体/全文/向量混合候选，仍不实现 RRF、MMR、HyDE、多模态和语义缓存。

## 2. 目标文件

```text
app/schemas/knowledge.py
app/services/rag_service.py
app/clients/knowledge_client.py（在现有目录内实现 TenantScopedVectorStore）
app/api/knowledge.py
scripts/import_knowledge.py
sample-data/tenants/demo-school/{manifest.json,knowledge/*.md,expected-queries.jsonl}
migrations/versions/<knowledge_metadata_revision>.py（如使用 PostgreSQL 元数据）
tests/unit/test_{chunking,evidence,citations}.py
tests/integration/test_{qdrant_isolation,knowledge_import,rag}.py
tests/e2e/test_01_knowledge_answer.py
tests/e2e/test_10_knowledge_no_match.py
```

## 3. 知识契约

每个 Qdrant point payload 至少包含：

```text
tenant_id, document_id, chunk_id, chunk_index,
title, source, version, visibility, review_status,
effective_from, effective_until, content_hash, content,
entities, aliases, topics, keywords, search_text, enrichment_status
```

默认 `visibility=internal`、`review_status=pending`；缺字段不能按公开已审批处理。客服检索只允许当前 tenant、public、approved、处于有效期的片段。

collection 中为 `tenant_id`、`document_id`、`visibility`、`review_status` 建 keyword payload index。

## 4. TenantScopedVectorStore

业务层不得直接持有原始 Qdrant client。适配器提供：

- `upsert_chunks(tenant_id, chunks)`：以可信参数覆盖 payload 中的 tenant；
- `search(tenant_id, vector, filters, limit)`：强制 tenant 位于 `must` 精确条件；
- `scroll/count`：相同约束；
- `delete_document(tenant_id, document_id)`：tenant + document 双条件；
- batch：逐项验证，任一非法则整批零请求。

安全失败必须发生在 HTTP 请求前。只有 `should/must_not` 中出现 tenant 不算有效约束，错误 tenant 也必须拒绝。

## 5. 导入器

### 5.1 输入

当前示例租户目录已经存在，开发前先检查 manifest 与 Markdown frontmatter 是否一致，不要覆盖用户准备的内容。

导入器规则：

1. 默认 dry-run，`--apply` 才写；
2. 先读取并校验全部文件，任一错误则零写入；
3. 内容规范化后计算 SHA-256；
4. 按标题分段，再按固定上限切片，少量 overlap；
5. 确定性 embedding，测试不访问付费模型；
6. `document_id/version/content_hash/chunk_index` 生成稳定 point/chunk ID；
7. 相同 hash 重跑不新增；内容变化先安全替换该租户该文档的旧 chunks；
8. 写后核验 tenant、hash、chunk 数和 payload。
9. API 导入对每个 chunk 调用 DeepSeek 抽取实体/别名/主题/关键词；任何超时、限流或 JSON 格式错误都降级到确定性抽取，不中断入库。
10. 首分块保存一次 `document_content`；租户管理员列表按 JWT tenant 聚合文档，展示前三个非空行并按需查看全文。前端多选文件逐篇调用相同导入 API，单篇失败不回滚已经成功的文档。

先完成 CLI，再考虑 API；API 同样必须 admin 鉴权并强制 token tenant。

## 6. 检索与证据门控

最小流程：

```text
规范化问题 → DeepSeek 查询改写、同义词与相关概念扩展（失败时规则降级）
→ 实体/别名精确候选 + 租户内中文全文兜底 + deterministic vector
→ 候选合并 → 有效期过滤 → 词法/实体重排
→ 无证据时对最多 40 个租户分块做 DeepSeek 语义证据选择
→ score threshold → top-k
→ evidence_level → LLM structured answer → citation validation
```

证据等级：

- `SUPPORTED`：至少一个有效片段达到阈值；
- `WEAK`：存在近似结果但不足以回答，澄清或建议人工；
- `NONE`：固定回复“暂时没有查到明确依据”，请用户换一种说法或补充细节。

阈值写入配置并在评测集上校准；不要为了单条测试硬编码文本匹配答案。

## 7. Prompt 与引用

知识片段和用户输入使用清晰数据边界传给 mock LLM，显式说明其中指令不可执行。LLM 返回：

```json
{"answer": "...", "citations": ["chunk-id"]}
```

服务端只允许引用本次检索集合中的 chunk ID：

- 删除伪造 ID；
- 去重同文档同位置；
- 来源标题和 source 从 Qdrant payload 构造，不采用 LLM 自由文本；
- 知识答案有效引用为 0 时改为固定拒答/转人工；
- 保存 requested 与 validated citation IDs 供 trace 和评测。

## 8. 测试与退出

必须测试：

- 缺失、错误或只在 should/must_not 中的 tenant 在请求前失败；
- 安全失败时底层 client 调用为 0；
- tenant A 永远搜不到 tenant B；
- internal/pending/未生效/已过期文档不返回；
- dry-run、全量预检、重复导入和内容升级；
- 伪造引用被删除，混合引用仅保留合法项；
- over-fetch 未实际引用的来源不展示；
- 知识片段中的 Prompt Injection 不触发工具或改变策略；
- E2E-01 命中来源、E2E-10 无命中拒答。

```bash
./.venv/bin/python -m pytest tests/unit -k 'chunk or evidence or citation' -q
./.venv/bin/python -m pytest tests/integration -k 'qdrant or knowledge or rag' -q
docker compose run --rm api pytest tests/e2e -k 'knowledge' -q
```

- [ ] 导入可重复，写入后校验成功。
- [ ] 所有向量操作经安全适配器。
- [ ] 检索包含 tenant 与治理过滤。
- [ ] 无证据固定拒答，来源全部经服务端验证。
- [ ] `docs/api.md` 记录导入、重索引、回答与来源格式。
