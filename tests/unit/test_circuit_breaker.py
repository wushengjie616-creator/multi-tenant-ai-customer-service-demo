import pytest

from app.core.circuit_breaker import AsyncCircuitBreaker, CircuitOpenError


async def test_circuit_opens_after_threshold_and_recovers_after_timeout():
    now = [100.0]
    breaker = AsyncCircuitBreaker(failure_threshold=2, recovery_timeout=10, clock=lambda: now[0])
    calls = 0

    async def fail():
        nonlocal calls
        calls += 1
        raise TimeoutError("downstream timeout")

    with pytest.raises(TimeoutError):
        await breaker.call(fail)
    with pytest.raises(TimeoutError):
        await breaker.call(fail)
    with pytest.raises(CircuitOpenError):
        await breaker.call(fail)
    assert calls == 2

    now[0] += 11

    async def succeed():
        return "ok"

    assert await breaker.call(succeed) == "ok"
    assert breaker.state == "closed"


async def test_cancelled_calls_do_not_trip_circuit():
    import asyncio

    breaker = AsyncCircuitBreaker(failure_threshold=1)

    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await breaker.call(cancelled)
    assert breaker.state == "closed"
