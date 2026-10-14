"""Retry configuration and utilities for Sprout traversal.

Uses tenacity for retry orchestration but remains HTTP-client-agnostic.
Callers provide their own RetryPredicate and optional RetryAfterHook.
"""

from dataclasses import dataclass, field

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

from .typedefs import RetryAfterHook, RetryPredicate


def _always_retry(_exc: BaseException) -> bool:
    """Retry all exceptions."""
    return True


class _HookedWait(wait_base):
    """Wait strategy that honours a retry-after hook from exception metadata.

    Falls back to exponential jitter when the hook returns no override.
    """

    def __init__(self, base: wait_base, hook: RetryAfterHook | None) -> None:
        """Initialise with a base wait strategy and an optional hook.

        Args:
            base: Fallback wait strategy (e.g. wait_exponential_jitter).
            hook: Caller-provided hook extracting wait seconds from exceptions.
                When None, the base strategy is always used.

        """
        self._base = base
        self._hook = hook

    def __call__(self, retry_state: RetryCallState) -> float:
        """Return wait duration in seconds for this retry attempt.

        Args:
            retry_state: Tenacity retry state for the current attempt.

        Returns:
            Seconds to wait before the next attempt.

        """
        if self._hook is not None and retry_state.outcome is not None:
            exc = retry_state.outcome.exception()
            if exc is not None:
                wait_secs = self._hook(exc)
                if wait_secs is not None and wait_secs > 0:
                    return min(wait_secs, 60.0)
        return self._base(retry_state)


@dataclass(frozen=True)
class RetryConfig:
    """Configuration for retry behavior.

    Attributes:
        max_attempts: Maximum number of retry attempts.
        min_wait: Minimum wait between retries in seconds.
        max_wait: Maximum wait between retries in seconds.
        jitter_pct: Maximum random jitter added to each wait, in seconds
            (not a percentage, despite the name).
        is_retryable: Predicate determining if an exception is retryable.
        retry_after_hook: Optional hook extracting wait duration
            from exceptions.

    """

    max_attempts: int = 4
    min_wait: float = 0.5
    max_wait: float = 10.0
    jitter_pct: float = 0.2
    is_retryable: RetryPredicate = field(
        default_factory=lambda: _always_retry,
    )
    retry_after_hook: RetryAfterHook | None = None

    def as_retrying(self) -> AsyncRetrying:
        """Build a tenacity AsyncRetrying instance from this config.

        Returns:
            Configured AsyncRetrying instance ready for iteration.

        """
        base_wait: wait_base = wait_exponential_jitter(
            multiplier=self.min_wait,
            max=self.max_wait,
            jitter=self.jitter_pct,
        )
        wait = _HookedWait(base_wait, self.retry_after_hook)
        return AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait,
            retry=retry_if_exception(self._should_retry),
            reraise=True,
        )

    def _should_retry(self, exc: BaseException) -> bool:
        """Retry only ordinary errors that ``is_retryable`` accepts.

        Cancellation, ``KeyboardInterrupt`` and ``SystemExit`` are not
        ``Exception`` subclasses and always propagate at once.
        """
        return isinstance(exc, Exception) and self.is_retryable(exc)
