"""Deterministic 50-case routing/safety eval plus grounded RAG quality probes."""

import asyncio
import json
from pathlib import Path

from app.clients.knowledge_client import vector_store
from app.core.config import settings
from app.services.intent_service import classify_intent
from app.services.rag_service import answer_question

BANNED_STYLE = ("作为 AI", "我很乐意", "总之", "希望对你有帮助", "亲亲")
TOOL_INTENTS = {"finance", "platform_command", "schedule"}


def ratio(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 1.0


async def evaluate_rag() -> dict:
    path = Path("sample-data/tenants/xinghe-future/expected-queries.jsonl")
    samples = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = []
    for sample in samples:
        result = await answer_question(vector_store, settings.demo_customer_tenant_id, sample["query"])
        answer = result["answer"]
        rows.append({
            "query": sample["query"],
            "factual": all(keyword in answer for keyword in sample["expected_contains"]),
            "cited": result["evidence_level"] == "SUPPORTED" and bool(result["citations"]),
            "style": not any(phrase in answer for phrase in BANNED_STYLE),
            "citations": result["citations"],
        })
    return {"samples": len(rows),
            "factual_accuracy": ratio([row["factual"] for row in rows]),
            "citation_hit_rate": ratio([row["cited"] for row in rows]),
            "style_compliance_rate": ratio([row["style"] for row in rows]),
            "failed": [row for row in rows if not (row["factual"] and row["cited"] and row["style"])]}


async def run() -> dict:
    dataset = json.loads(Path("evaluation/dataset.json").read_text(encoding="utf-8"))
    rows = []
    for sample in dataset:
        result = classify_intent(sample["input"])
        expected = {"reminder": "schedule"}.get(sample["expected_intent"], sample["expected_intent"])
        predicted_tool = result.intent in TOOL_INTENTS
        predicted_handoff = result.intent == "human_handoff"
        rows.append({"id": sample["id"], "expected": expected, "actual": result.intent,
                     "intent_passed": result.intent == expected,
                     "tool_passed": predicted_tool == sample["should_call_tool"],
                     "handoff_passed": predicted_handoff == sample["should_handoff"],
                     "unauthorized_refusal_passed": expected != "unknown" or (result.intent == "unknown" and not predicted_tool)})
    report = {
        "samples": len(rows),
        "intent_accuracy": ratio([row["intent_passed"] for row in rows]),
        "tool_routing_accuracy": ratio([row["tool_passed"] for row in rows]),
        "handoff_accuracy": ratio([row["handoff_passed"] for row in rows]),
        "unauthorized_refusal_rate": ratio([row["unauthorized_refusal_passed"] for row in rows if row["expected"] == "unknown"]),
        "failed": [row for row in rows if not all((row["intent_passed"], row["tool_passed"], row["handoff_passed"], row["unauthorized_refusal_passed"]))],
        "rag": await evaluate_rag(),
    }
    return report


def main() -> None:
    report = asyncio.run(run())
    Path("evaluation/report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not report["failed"] and not report["rag"]["failed"] else 1)


if __name__ == "__main__":
    main()
