"""Tests for Sprout retry module."""

import asyncio
import time
import warnings
from typing import Any

import pytest

from sprout.exceptions import InvalidParameterError
from sprout.retry import RetryConfig, _always_retry

_DEFAULT_MAX_ATTEMPTS = 4
_DEFAULT_MIN_WAIT = 0.5
_DEFAULT_MAX_WAIT = 10.0
_DEFAULT_JITTER = 0.2
_CUSTOM_MAX_ATTEMPTS = 2
_RETRY_ATTEMPTS_3 = 3
_JITTER_GAP_MIN = 0.05
_HOOK_WAIT_THRESHOLD = 3
_HOOK_ELAPSED_LIMIT = 2.0


def test_always_retry_predicate() -> None:
    """Test default retry predicate retries everything."""
    assert _always_retry(ValueError("x")) is True
    assert _always_retry(RuntimeError("y")) is True


def test_retry_config_defaults() -> None:
    """Test RetryConfig default values."""
    config = RetryConfig()
    assert config.max_attempts == _DEFAULT_MAX_ATTEMPTS
    assert config.min_wait == _DEFAULT_MIN_WAIT
    assert config.max_wait == _DEFAULT_MAX_WAIT
    assert config.jitter_pct == _DEFAULT_JITTER
    assert config.is_retryable is _always_retry
    assert config.retry_after_hook is None


def test_retry_config_custom_values() -> None:
    """Test RetryConfig with custom values."""

    def custom_pred(exc: BaseException) -> bool:
        return isinstance(exc, ValueError)

    config = RetryConfig(
        max_attempts=_CUSTOM_MAX_ATTEMPTS,
        min_wait=1.0,
        max_wait=5.0,
        jitter_pct=0.1,
        is_retryable=custom_pred,
    )
    assert config.max_attempts == _CUSTOM_MAX_ATTEMPTS
    assert config.is_retryable is custom_pred


@pytest.mark.parametrize(
    ("kwargs", "field_name"),
    [
        ({"max_attempts": 0}, "max_attempts"),
        ({"min_wait": -1.0}, "min_wait"),
        ({"jitter_pct": -0.1}, "jitter_pct"),
        ({"min_wait": 5.0, "max_wait": 1.0}, "max_wait"),
    ],
)
def test_retry_config_rejects_invalid(
    kwargs: dict[str, Any],
    field_name: str,
) -> None:
    """Test RetryConfig rejects out-of-range values, naming the field."""
    with pytest.raises(InvalidParameterError, match=field_name):
        RetryConfig(**kwargs)


def test_retry_config_boundary_values_are_valid() -> None:
    """Test zero min_wait and min_wait == max_wait are accepted."""
    assert RetryConfig(min_wait=0.0)
    assert RetryConfig(min_wait=2.0, max_wait=2.0)


def test_as_retrying_creates_instance() -> None:
    """Test as_retrying returns an AsyncRetrying object."""
    config = RetryConfig(max_attempts=_CUSTOM_MAX_ATTEMPTS)
    retrying = config.as_retrying()
    assert retrying is not None


def test_as_retrying_emits_no_warnings() -> None:
    """Test building the retry strategy uses no deprecated tenacity API."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        RetryConfig().as_retrying()


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt() -> None:
    """Test retry succeeds after first failure."""
    config = RetryConfig(
        max_attempts=_RETRY_ATTEMPTS_3,
        min_wait=0.01,
        max_wait=0.02,
    )
    retrying = config.as_retrying()
    call_count = 0

    async def flaky() -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            msg = "transient"
            raise RuntimeError(msg)
        return "ok"

    result = None
    async for attempt in retrying:
        with attempt:
            result = await flaky()

    assert result == "ok"
    assert call_count == _CUSTOM_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_retry_exhausted_raises() -> None:
    """Test retry raises after max attempts exhausted."""
    config = RetryConfig(
        max_attempts=_CUSTOM_MAX_ATTEMPTS,
        min_wait=0.01,
        max_wait=0.02,
    )
    retrying = config.as_retrying()

    async def always_fails() -> str:
        msg = "always"
        raise RuntimeError(msg)

    async def run() -> None:
        async for attempt in retrying:
            with attempt:
                await always_fails()

    with pytest.raises(RuntimeError, match="always"):
        await run()


@pytest.mark.asyncio
async def test_retry_non_retryable_fails_immediately() -> None:
    """Test non-retryable exception fails on first attempt."""

    def only_runtime(exc: BaseException) -> bool:
        return isinstance(exc, RuntimeError)

    config = RetryConfig(
        max_attempts=_RETRY_ATTEMPTS_3,
        min_wait=0.01,
        is_retryable=only_runtime,
    )
    retrying = config.as_retrying()
    call_count = 0

    async def raises_value_error() -> str:
        nonlocal call_count
        call_count += 1
        msg = "not retryable"
        raise ValueError(msg)

    async def run() -> None:
        async for attempt in retrying:
            with attempt:
                await raises_value_error()

    with pytest.raises(ValueError, match="not retryable"):
        await run()

    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_jitter_bounds() -> None:
    """Test retry wait times respect jitter bounds."""
    config = RetryConfig(
        max_attempts=_DEFAULT_MAX_ATTEMPTS,
        min_wait=0.1,
        max_wait=0.5,
        jitter_pct=_DEFAULT_JITTER,
    )
    retrying = config.as_retrying()
    timestamps: list[float] = []

    async def always_fails() -> None:
        timestamps.append(time.monotonic())
        msg = "fail"
        raise RuntimeError(msg)

    async def run() -> None:
        async for attempt in retrying:
            with attempt:
                await always_fails()

    with pytest.raises(RuntimeError):
        await run()

    for i in range(1, len(timestamps)):
        gap = timestamps[i] - timestamps[i - 1]
        assert gap >= _JITTER_GAP_MIN
        assert gap <= 1.0  # Upper bound (max_wait + jitter)


@pytest.mark.asyncio
async def test_retry_with_retry_after_hook() -> None:
    """Test retry_after_hook is accepted in config."""

    def hook(exc: BaseException) -> float | None:
        if "rate" in str(exc):
            return 0.1
        return None

    config = RetryConfig(
        max_attempts=_RETRY_ATTEMPTS_3,
        min_wait=0.01,
        retry_after_hook=hook,
    )
    retrying = config.as_retrying()
    call_count = 0

    async def flaky() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < _RETRY_ATTEMPTS_3:
            msg = "rate limited"
            raise RuntimeError(msg)
        return "done"

    result = None
    async for attempt in retrying:
        with attempt:
            result = await flaky()

    assert result == "done"
    assert call_count == _RETRY_ATTEMPTS_3


@pytest.mark.asyncio
async def test_retry_after_hook_respects_max_wait() -> None:
    """Test hook-supplied waits are capped at RetryConfig.max_wait."""
    config = RetryConfig(
        max_attempts=_RETRY_ATTEMPTS_3,
        min_wait=0.01,
        max_wait=0.05,
        retry_after_hook=lambda _exc: 30.0,
    )
    retrying = config.as_retrying()

    async def always_fails() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    async def run() -> None:
        async for attempt in retrying:
            with attempt:
                await always_fails()

    start = time.monotonic()
    with pytest.raises(RuntimeError):
        await run()
    elapsed = time.monotonic() - start

    assert elapsed < _HOOK_ELAPSED_LIMIT


@pytest.mark.asyncio
async def test_cancelled_error_is_never_retried() -> None:
    """Test cancellation propagates at once, even with the retry-all default."""
    calls = 0

    async def cancelled() -> None:
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    retrying = RetryConfig(max_attempts=_RETRY_ATTEMPTS_3).as_retrying()

    with pytest.raises(asyncio.CancelledError):
        await retrying(cancelled)

    assert calls == 1


class _StopEverything(BaseException):
    """Stand-in for KeyboardInterrupt and SystemExit."""


@pytest.mark.asyncio
async def test_non_exception_errors_are_never_retried() -> None:
    """Test a BaseException outside Exception propagates at once."""
    calls = 0

    async def interrupted() -> None:
        nonlocal calls
        calls += 1
        raise _StopEverything

    retrying = RetryConfig(max_attempts=_RETRY_ATTEMPTS_3).as_retrying()

    with pytest.raises(_StopEverything):
        await retrying(interrupted)

    assert calls == 1
