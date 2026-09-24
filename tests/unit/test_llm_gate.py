import asyncio

from app.services.llm_gate import LLMRequestGate


class FakeRedis:
    def __init__(self):
        self.results = [0, 1]
        self.released = []

    async def eval(self, *args):
        return self.results.pop(0)

    async def zrem(self, key, member):
        self.released.append((key, member))


class BrokenRedis:
    async def eval(self, *args):
        raise ConnectionError("redis unavailable")

    async def zrem(self, key, member):
        raise ConnectionError("redis unavailable")


async def test_llm_gate_queues_until_global_slot_is_admitted():
    redis = FakeRedis()
    gate = LLMRequestGate(redis, max_inflight=600, starts_per_second=20, poll_seconds=0)
    async with gate.slot():
        pass
    assert len(redis.released) == 1
    assert redis.released[0][0] == "llm:gate:inflight"


async def test_llm_gate_uses_bounded_local_fallback_when_redis_is_down():
    gate = LLMRequestGate(
        BrokenRedis(), max_inflight=1, starts_per_second=20, poll_seconds=0
    )
    first = await gate.acquire()
    assert first is not None and first.startswith("local:")

    blocked = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0)
    assert not blocked.done()

    await gate.release(first)
    second = await asyncio.wait_for(blocked, timeout=0.2)
    assert second is not None and second.startswith("local:")
    await gate.release(second)
