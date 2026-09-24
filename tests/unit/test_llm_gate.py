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


async def test_llm_gate_queues_until_global_slot_is_admitted():
    redis = FakeRedis()
    gate = LLMRequestGate(redis, max_inflight=600, starts_per_second=20, poll_seconds=0)
    async with gate.slot():
        pass
    assert len(redis.released) == 1
    assert redis.released[0][0] == "llm:gate:inflight"
