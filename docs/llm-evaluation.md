# LLM / RAG 离线评测

## 方法

`python -m evaluation.evaluate` 执行两组可重复证据：

1. 50 条固定输入：评估意图准确率、工具路由、转人工准确率和注入/未知输入的越权拒绝率。
2. T01 星河租户 5 条 RAG 金标问题：在已导入 Qdrant 的演示知识上评估关键事实命中、引用命中和少 AI 味禁用语合规。

评测不依赖付费 LLM；RAG 候选与 evidence gate 使用当前 Qdrant 演示索引。机器可读结果见 `evaluation/report.json`。

## 指标口径

| 指标 | 证明什么 | 不证明什么 |
| --- | --- | --- |
| intent accuracy | 输入能被分到正确安全路由 | 开放域语言理解的所有长尾 |
| tool routing accuracy | 需要/不需要工具的边界与金标一致 | 执行仍须经确定性策略 |
| unauthorized refusal rate | 未知/注入反例不进入业务工具 | 不代替红队或渗透测试 |
| handoff accuracy | 明确转人工语句的准确性 | 不代表生产坐席 SLA |
| factual accuracy | RAG 回答含有人工给定的关键事实 | 当前只是 5 条租户内金标 |
| citation hit rate | 有依据回答返回服务端验证的来源 | 不证明来源的外部权威性 |
| style compliance | 不出现 Word 禁用套话 | 不代替人工主观复核 |

## 验收

只有 50 条路由/安全样本全部通过，且 5 条 RAG 样本的事实、引用、风格全部通过时，命令才返回 0。这使 `make acceptance` 能阻止评测回归，而不是只生成报告。
