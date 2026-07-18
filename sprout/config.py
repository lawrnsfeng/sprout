"""Configuration for Sprout executor."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SproutSettings:
    """Plain-dataclass settings for Sprout traversal.

    Attributes:
        max_at_once: Maximum concurrent fetches.
        max_per_second: Maximum fetches per second (0 = unlimited).
        level_timeout_seconds: Timeout budget per depth level.
        retry_max_attempts: Maximum retry attempts per node.

    """

    max_at_once: int = 8
    max_per_second: float = 10.0
    level_timeout_seconds: float = 30.0
    retry_max_attempts: int = 4


@dataclass(frozen=True)
class ExecutorConfig:
    """Runtime configuration for Executor.

    Attributes:
        retry_max_attempts: Maximum retry attempts per node.
        max_at_once: Maximum concurrent fetches (bounded semaphore).
        max_per_second: Maximum fetches per second (0 = unlimited).
        level_timeout: Timeout budget per depth level in seconds.

    """

    retry_max_attempts: int = 4
    max_at_once: int = 8
    max_per_second: float = 10.0
    level_timeout: float = 30.0

    @classmethod
    def from_settings(
        cls,
        settings: SproutSettings,
    ) -> "ExecutorConfig":
        """Build config from Sprout settings.

        Args:
            settings: Object providing SproutSettings fields.

        Returns:
            Executor config with values from settings.

        """
        return cls(
            retry_max_attempts=settings.retry_max_attempts,
            max_at_once=settings.max_at_once,
            max_per_second=settings.max_per_second,
            level_timeout=settings.level_timeout_seconds,
        )
