"""意图分类与策略层之间的固定契约。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class IntentResult(BaseModel):
    intent: Literal[
        "platform_command",
        "knowledge_qa",
        "course_info",
        "enrollment",
        "schedule_info",
        "teacher_info",
        "pricing",
        "promotion",
        "attendance_policy",
        "location_contact",
        "account_support",
        "material_info",
        "schedule",
        "finance",
        "chitchat",
        "human_handoff",
        "high_risk",
        "unknown",
    ]
    confidence: float = Field(ge=0, le=1)
    source: Literal["rule", "llm", "abstain"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk_level: Literal["low", "medium", "high"] = "low"
    abstain_reason: str | None = None
    model_version: str = "rules-v1"
