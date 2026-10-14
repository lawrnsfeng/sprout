"""Concurrency control for Sprout traversal.

Implements concurrency limiting using:
1. asyncio.Semaphore for capping concurrent in-flight operations
2. Leaky token bucket for rate limiting (operations per second)

Pattern inspired by aiometer/_impl/run_on_each.py
(https://github.com/florimondmanca/aiometer, MIT License).
"""

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from .exceptions import InvalidParameterError


class ConcurrencyGate:
    """Controls concurrency via semaphore and rate limiting.

    Provides an async context manager that enforces both:
    - Maximum concurrent operations (via asyncio.Semaphore)
    - Maximum operations per second (via leaky token bucket)

    """

    def __init__(
        self,
        max_at_once: int,
        max_per_second: float = 0.0,
    ) -> None:
        """Initialize ConcurrencyGate.

        Args:
            max_at_once: Maximum concurrent operations (must be > 0).
            max_per_second: Maximum operations per second (0 = unlimited).

        Raises:
            InvalidParameterError: If max_at_once <= 0 or max_per_second < 0.

        """
        if max_at_once <= 0:
            raise InvalidParameterError(param="max_at_once", requirement="> 0")
        if max_per_second < 0:
            raise InvalidParameterError(
                param="max_per_second",
                requirement=">= 0",
            )

        self._max_at_once = max_at_once
        self._max_per_second = max_per_second
        self._last_operation_time: float | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore
        self._rate_limit_lock: asyncio.Lock

    def _bind_to_running_loop(self) -> None:
        """Give the gate fresh asyncio primitives when the event loop changed.

        A Semaphore or Lock stays bound to the loop it first waited on. The
        pacing timestamp is plain data and is kept, so the rate limit also
        holds from one event loop to the next.
        """
        loop = asyncio.get_running_loop()
        if loop is self._loop:
            return
        self._loop = loop
        self._semaphore = asyncio.Semaphore(self._max_at_once)
        self._rate_limit_lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[None]:
        """Acquire a slot respecting both semaphore and rate limit.

        Yields:
            None — caller performs work inside the context.

        """
        self._bind_to_running_loop()
        async with self._semaphore:
            await self._wait_for_rate_limit()
            yield

    async def _wait_for_rate_limit(self) -> None:
        """Sleep if necessary to honour the per-second rate limit."""
        if self._max_per_second <= 0:
            return

        async with self._rate_limit_lock:
            now = time.monotonic()

            if self._last_operation_time is not None:
                min_interval = 1.0 / self._max_per_second
                elapsed = now - self._last_operation_time
                if elapsed < min_interval:
                    sleep_duration = min_interval - elapsed
                    await asyncio.sleep(sleep_duration)
                    now = time.monotonic()

            self._last_operation_time = now
