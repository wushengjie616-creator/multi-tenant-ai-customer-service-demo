"""平台工具 HTTP 边界。"""

import asyncio
import httpx

from app.core.config import settings
from app.core.circuit_breaker import AsyncCircuitBreaker
from app.core.metrics import CIRCUIT_BREAKER_STATE


def _record_state(breaker: AsyncCircuitBreaker) -> None:
    CIRCUIT_BREAKER_STATE.labels("platform").set(
        {"closed": 0, "half_open": 1, "open": 2}[breaker.state]
    )


class PlatformClient:
    def __init__(self, base_url: str | None = None, timeout: float = 3.0,
                 max_attempts: int = 3, retry_base_delay: float = 0.05):
        self.client = httpx.AsyncClient(base_url=base_url or settings.mock_platform_url, timeout=timeout)
        self.timeout = timeout
        self.breaker = AsyncCircuitBreaker()
        self.max_attempts = max(1, max_attempts)
        self.retry_base_delay = max(0, retry_base_delay)

    async def _retry(self, operation):
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await operation()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 and exc.response.status_code != 429:
                    raise
                if attempt == self.max_attempts:
                    raise
            except (httpx.ConnectError, httpx.TimeoutException, TimeoutError):
                if attempt == self.max_attempts:
                    raise
            if self.retry_base_delay:
                await asyncio.sleep(self.retry_base_delay * (2 ** (attempt - 1)))

    async def execute(self, action: str, payload: dict, idempotency_key: str) -> dict:
        async def request():
            async with asyncio.timeout(self.timeout):
                response = await self.client.post(f"/tools/{action}", json=payload, headers={"Idempotency-Key": idempotency_key})
                response.raise_for_status()
                return response.json()
        try:
            return await self.breaker.call(lambda: self._retry(request))
        finally:
            _record_state(self.breaker)

    async def query(self, kind: str, tenant_id: str, user_id: str) -> dict:
        async def request():
            async with asyncio.timeout(self.timeout):
                response = await self.client.get(
                    f"/queries/{kind}",
                    params={"tenant_id": tenant_id, "user_id": user_id},
                )
                response.raise_for_status()
                return response.json()
        try:
            return await self.breaker.call(lambda: self._retry(request))
        finally:
            _record_state(self.breaker)

    async def close(self):
        await self.client.aclose()


platform_client = PlatformClient()
