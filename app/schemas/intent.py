"""意图分类与策略层之间的固定契约。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class IntentResult(BaseModel):
    """两级意图：一级意图为粗粒度白名单，二级意图为 LLM 概括的具体意图（仅 LLM 兜底时生产）。"""

    intent: Literal[
        "platform_command",
        "finance",
        "schedule",
        "human_handoff",
        "knowledge",
        "chitchat",
        "unknown",
        "course_consultation",
    ]
    confidence: float = Field(ge=0, le=1)
    source: Literal["rule", "llm", "abstain"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk_level: Literal["low", "medium", "high"] = "low"
    abstain_reason: str | None = None
    model_version: str = "rules-v2"
    secondary_intent: str | None = None
