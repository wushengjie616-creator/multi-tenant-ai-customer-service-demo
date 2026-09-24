"""OpenAI-compatible LLM client used by DeepSeek and the local mock."""

import asyncio
import httpx

from app.core.config import settings
from app.core.redis import redis_client
from app.core.circuit_breaker import AsyncCircuitBreaker
from app.services.llm_gate import LLMRequestGate
from app.services import llm_usage_context
from app.utils.masking import mask_pii


def extract_reply(data: dict) -> str:
    """从 OpenAI 风格 chat 响应提取首个回复文本。"""
    return data["choices"][0]["message"]["content"]


class LLMClient:
    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        client: httpx.AsyncClient | None = None,
        gate: LLMRequestGate | None = None,
    ):
        self.api_url = api_url or settings.deepseek_api_url
        self.model = model or settings.deepseek_model
        self.timeout = timeout or settings.llm_timeout_seconds
        self.temperature = (
            settings.llm_temperature if temperature is None else temperature
        )
        self.max_tokens = max_tokens or settings.llm_max_tokens
        headers = {}
        resolved_key = settings.deepseek_api_key if api_key is None else api_key
        if resolved_key:
            headers["Authorization"] = f"Bearer {resolved_key}"
        self._headers = headers
        self._client = client or httpx.AsyncClient(timeout=self.timeout, headers=headers)
        self.breaker = AsyncCircuitBreaker()
        self.gate = gate

    async def generate(
        self,
        messages: list[dict],
        *,
        stream: bool = False,
        thinking: bool | None = None,
    ) -> str:
        safe_messages = [
            {
                **message,
                "content": mask_pii(message.get("content", "")),
            }
            for message in messages
        ]

        async def request():
            async with asyncio.timeout(self.timeout):
                payload = {
                    "model": self.model,
                    "messages": safe_messages,
                    "stream": stream,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                }
                if thinking is not None:
                    payload["thinking"] = {
                        "type": "enabled" if thinking else "disabled"
                    }
                resp = await self._client.post(
                    self.api_url,
                    headers=self._headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                reply = extract_reply(data)
                usage = data.get("usage") or {}
                prompt_tokens = int(usage.get("prompt_tokens") or max(1, sum(len(item.get("content", "")) for item in safe_messages) // 4))
                completion_tokens = int(usage.get("completion_tokens") or max(1, len(reply) // 4))
                llm_usage_context.add({"model": self.model, "prompt_tokens": prompt_tokens,
                                       "completion_tokens": completion_tokens})
                return reply
        if self.gate is None:
            return await self.breaker.call(request)
        async with self.gate.slot():
            return await self.breaker.call(request)

    async def close(self) -> None:
        await self._client.aclose()


global_llm_gate = LLMRequestGate(
    redis_client, max_inflight=settings.llm_global_max_inflight,
    starts_per_second=settings.llm_global_starts_per_second,
)
llm_client = LLMClient(gate=global_llm_gate)
mock_llm_client = LLMClient(
    api_url=f"{settings.mock_llm_url.rstrip('/')}/v1/chat/completions",
    api_key="",
    model="mock-education",
    gate=global_llm_gate,
)
