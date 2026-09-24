"""Small async circuit breaker used at downstream HTTP boundaries."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    pass


class AsyncCircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, recovery_timeout: float = 30.0, clock=time.monotonic):
        if failure_threshold < 1 or recovery_timeout <= 0:
            raise ValueError("invalid circuit breaker configuration")
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.recovery_timeout:
            return "half_open"
        return "open"

    async def call(self, operation: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        async with self._lock:
            state = self.state
            if state == "open" or (state == "half_open" and self._half_open_in_flight):
                raise CircuitOpenError("downstream circuit is open")
            if state == "half_open":
                self._half_open_in_flight = True
        try:
            result = await operation(*args, **kwargs)
        except Exception:
            async with self._lock:
                self._half_open_in_flight = False
                self._failures += 1
                if self._failures >= self.failure_threshold:
                    self._opened_at = self._clock()
            raise
        async with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_in_flight = False
        return result
