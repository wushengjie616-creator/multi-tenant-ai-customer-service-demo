import asyncio

from app.api import health


async def test_nats_ready_probe_has_a_hard_timeout(monkeypatch):
    async def never_connect(*args, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(health.nats, "connect", never_connect)

    result = await asyncio.wait_for(
        health.check_nats("nats://missing:4222", timeout=0.01),
        timeout=0.1,
    )

    assert result.startswith("error:")
    assert "timeout" in result.lower()
