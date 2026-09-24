"""平台工具 HTTP 边界。"""

import asyncio
import httpx

from app.core.config import settings
from app.core.circuit_breaker import AsyncCircuitBreaker


class PlatformClient:
    def __init__(self, base_url: str | None = None, timeout: float = 3.0):
        self.client = httpx.AsyncClient(base_url=base_url or settings.mock_platform_url, timeout=timeout)
        self.timeout = timeout
        self.breaker = AsyncCircuitBreaker()

    async def execute(self, action: str, payload: dict, idempotency_key: str) -> dict:
        async def request():
            async with asyncio.timeout(self.timeout):
                response = await self.client.post(f"/tools/{action}", json=payload, headers={"Idempotency-Key": idempotency_key})
                response.raise_for_status()
                return response.json()
        return await self.breaker.call(request)

    async def query(self, kind: str, tenant_id: str, user_id: str) -> dict:
        async def request():
            async with asyncio.timeout(self.timeout):
                response = await self.client.get(
                    f"/queries/{kind}",
                    params={"tenant_id": tenant_id, "user_id": user_id},
                )
                response.raise_for_status()
                return response.json()
        return await self.breaker.call(request)

    async def close(self):
        await self.client.aclose()


platform_client = PlatformClient()
