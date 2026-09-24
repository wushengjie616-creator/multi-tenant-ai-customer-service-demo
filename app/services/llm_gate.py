"""Redis-backed global LLM admission gate shared by all API/worker processes."""

import asyncio
import uuid
from contextlib import asynccontextmanager

from app.core.logging import get_logger

log = get_logger(__name__)

_ACQUIRE = """
local t = redis.call('TIME')
local now = (tonumber(t[1]) * 1000) + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - tonumber(ARGV[4]))
redis.call('ZREMRANGEBYSCORE', KEYS[2], 0, now - 1000)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then return 0 end
redis.call('ZADD', KEYS[1], now, ARGV[1])
redis.call('ZADD', KEYS[2], now, ARGV[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[4]) * 2)
redis.call('PEXPIRE', KEYS[2], 2000)
return 1
"""


class LLMRequestGate:
    def __init__(self, redis, *, max_inflight: int = 600, starts_per_second: int = 20,
                 lease_ms: int = 120_000, poll_seconds: float = 0.05):
        self.redis = redis
        self.max_inflight = max_inflight
        self.starts_per_second = starts_per_second
        self.lease_ms = lease_ms
        self.poll_seconds = poll_seconds

    async def acquire(self) -> str | None:
        request_id = str(uuid.uuid4())
        while True:
            try:
                admitted = await self.redis.eval(
                    _ACQUIRE, 2, "llm:gate:inflight", "llm:gate:starts",
                    request_id, self.max_inflight, self.starts_per_second, self.lease_ms,
                )
            except Exception as exc:  # Redis 故障时不把 LLM 完全打死，记录后降级直连。
                log.warning("llm gate unavailable; failing open: %s", type(exc).__name__)
                return None
            if int(admitted) == 1:
                return request_id
            await asyncio.sleep(self.poll_seconds)

    async def release(self, request_id: str | None) -> None:
        if request_id is None:
            return
        try:
            await self.redis.zrem("llm:gate:inflight", request_id)
        except Exception as exc:
            log.warning("llm gate release failed: %s", type(exc).__name__)

    @asynccontextmanager
    async def slot(self):
        request_id = await self.acquire()
        try:
            yield
        finally:
            await self.release(request_id)
