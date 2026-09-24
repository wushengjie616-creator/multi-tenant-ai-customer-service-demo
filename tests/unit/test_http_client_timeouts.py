import asyncio

import httpx
import pytest

from app.clients.finance_client import FinanceClient


async def test_finance_client_enforces_hard_timeout_around_instrumented_transport():
    async def slow_response(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"status": "late"})

    client = FinanceClient(base_url="http://finance.test", timeout=0.01)
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="http://finance.test",
        transport=httpx.MockTransport(slow_response),
        timeout=None,
    )
    try:
        with pytest.raises(TimeoutError):
            await client.query("invoice", "tenant", "user")
    finally:
        await client.close()
