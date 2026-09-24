"""OpenAI-compatible LLM client used by DeepSeek and the local mock."""

import asyncio
import inspect
import json
import time
import httpx

from app.core.config import settings
from app.core.redis import redis_client
from app.core.circuit_breaker import AsyncCircuitBreaker
from app.core.metrics import CIRCUIT_BREAKER_STATE, LLM_LATENCY, LLM_REQUESTS
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
        call_started = time.perf_counter()

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
                LLM_REQUESTS.labels("openai-compatible", self.model, "success").inc()
                LLM_LATENCY.labels("openai-compatible", self.model, "success").observe(
                    time.perf_counter() - call_started
                )
                return reply
        try:
            if self.gate is None:
                result = await self.breaker.call(request)
            else:
                async with self.gate.slot():
                    result = await self.breaker.call(request)
        except Exception:
            LLM_REQUESTS.labels("openai-compatible", self.model, "failed").inc()
            LLM_LATENCY.labels("openai-compatible", self.model, "failed").observe(
                time.perf_counter() - call_started
            )
            CIRCUIT_BREAKER_STATE.labels("llm").set(
                {"closed": 0, "half_open": 1, "open": 2}[self.breaker.state]
            )
            raise
        CIRCUIT_BREAKER_STATE.labels("llm").set(
            {"closed": 0, "half_open": 1, "open": 2}[self.breaker.state]
        )
        return result

    async def generate_stream(
        self,
        messages: list[dict],
        on_delta,
        *,
        thinking: bool | None = None,
    ) -> str:
        """Consume an OpenAI-compatible SSE stream and forward deltas immediately.

        The global gate and circuit breaker cover the whole provider stream, not
        only connection establishment, so a slow response still occupies one of
        the configured in-flight slots.
        """
        safe_messages = [
            {**message, "content": mask_pii(message.get("content", ""))}
            for message in messages
        ]
        call_started = time.perf_counter()

        async def request() -> str:
            chunks: list[str] = []
            usage: dict = {}
            payload = {
                "model": self.model,
                "messages": safe_messages,
                "stream": True,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            if thinking is not None:
                payload["thinking"] = {
                    "type": "enabled" if thinking else "disabled"
                }
            async with asyncio.timeout(self.timeout):
                async with self._client.stream(
                    "POST", self.api_url, headers=self._headers, json=payload
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        data = json.loads(raw)
                        usage = data.get("usage") or usage
                        choices = data.get("choices") or []
                        delta = (choices[0].get("delta") or {}).get("content") if choices else None
                        if not delta:
                            continue
                        chunks.append(delta)
                        callback_result = on_delta(delta)
                        if inspect.isawaitable(callback_result):
                            await callback_result
            reply = "".join(chunks)
            if not reply:
                raise ValueError("LLM stream completed with no content delta")
            prompt_tokens = int(usage.get("prompt_tokens") or max(
                1, sum(len(item.get("content", "")) for item in safe_messages) // 4
            ))
            completion_tokens = int(
                usage.get("completion_tokens") or max(1, len(reply) // 4)
            )
            llm_usage_context.add({
                "model": self.model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            })
            LLM_REQUESTS.labels("openai-compatible", self.model, "success").inc()
            LLM_LATENCY.labels("openai-compatible", self.model, "success").observe(
                time.perf_counter() - call_started
            )
            return reply

        try:
            if self.gate is None:
                result = await self.breaker.call(request)
            else:
                async with self.gate.slot():
                    result = await self.breaker.call(request)
        except Exception:
            LLM_REQUESTS.labels("openai-compatible", self.model, "failed").inc()
            LLM_LATENCY.labels("openai-compatible", self.model, "failed").observe(
                time.perf_counter() - call_started
            )
            CIRCUIT_BREAKER_STATE.labels("llm").set(
                {"closed": 0, "half_open": 1, "open": 2}[self.breaker.state]
            )
            raise
        CIRCUIT_BREAKER_STATE.labels("llm").set(
            {"closed": 0, "half_open": 1, "open": 2}[self.breaker.state]
        )
        return result

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
