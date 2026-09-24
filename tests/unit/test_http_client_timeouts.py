import asyncio

import httpx
import pytest

from app.clients.finance_client import FinanceClient
from app.clients.platform_client import PlatformClient


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


async def test_platform_client_retries_transient_500_with_same_idempotency_key():
    calls = []

    async def flaky(request):
        calls.append(request.headers["Idempotency-Key"])
        if len(calls) < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"status": "succeeded"})

    client = PlatformClient(base_url="http://platform.test", timeout=1, max_attempts=3, retry_base_delay=0)
    await client.client.aclose()
    client.client = httpx.AsyncClient(base_url="http://platform.test", transport=httpx.MockTransport(flaky))
    try:
        result = await client.execute("submit_leave", {}, "same-key")
    finally:
        await client.close()
    assert result["status"] == "succeeded"
    assert calls == ["same-key", "same-key", "same-key"]


async def test_platform_client_does_not_retry_4xx():
    calls = 0

    async def rejected(request):
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "invalid"})

    client = PlatformClient(base_url="http://platform.test", timeout=1, max_attempts=3, retry_base_delay=0)
    await client.client.aclose()
    client.client = httpx.AsyncClient(base_url="http://platform.test", transport=httpx.MockTransport(rejected))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.execute("submit_leave", {}, "same-key")
    finally:
        await client.close()
    assert calls == 1
