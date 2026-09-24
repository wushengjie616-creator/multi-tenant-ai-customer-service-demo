from decimal import Decimal

from sqlalchemy import func, select

from app.core.config import settings
from app.models import LLMUsage


async def record_calls(session, *, tenant_id, conversation_id, message_id: str, calls: list[dict]) -> None:
    for call in calls:
        prompt = int(call.get("prompt_tokens", 0)); completion = int(call.get("completion_tokens", 0))
        cost = (Decimal(prompt) * Decimal(str(settings.llm_input_cost_per_million_usd)) +
                Decimal(completion) * Decimal(str(settings.llm_output_cost_per_million_usd))) / Decimal(1_000_000)
        session.add(LLMUsage(tenant_id=tenant_id, conversation_id=conversation_id,
                             message_id=message_id, model=call.get("model", "unknown"),
                             prompt_tokens=prompt, completion_tokens=completion, cost_usd=cost))


async def conversation_totals(session, tenant_id):
    rows = await session.execute(select(
        LLMUsage.conversation_id, func.sum(LLMUsage.prompt_tokens),
        func.sum(LLMUsage.completion_tokens), func.sum(LLMUsage.cost_usd),
        func.count(LLMUsage.id),
    ).where(LLMUsage.tenant_id == tenant_id).group_by(LLMUsage.conversation_id))
    return [{"conversation_id": str(row[0]), "prompt_tokens": int(row[1] or 0),
             "completion_tokens": int(row[2] or 0), "total_tokens": int((row[1] or 0) + (row[2] or 0)),
             "cost_usd": float(row[3] or 0), "llm_calls": int(row[4])} for row in rows]
