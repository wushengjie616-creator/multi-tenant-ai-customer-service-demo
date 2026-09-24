"""Configuration shared by Locust profiles and their contract tests."""

import os
from enum import Enum


class LoadScenario(str, Enum):
    MIXED = "mixed"
    STABLE = "stable"
    BURST = "burst"
    FINANCE = "finance"
    LLM_TIMEOUT = "llm-timeout"


def load_scenario() -> LoadScenario:
    value = os.getenv("LOADTEST_SCENARIO", LoadScenario.MIXED.value).strip().lower()
    try:
        return LoadScenario(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in LoadScenario)
        raise ValueError(f"LOADTEST_SCENARIO must be one of: {allowed}") from exc
