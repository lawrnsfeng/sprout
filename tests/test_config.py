"""Tests for Sprout config module."""

from sprout.config import ExecutorConfig, SproutSettings

_DEFAULT_RETRY_MAX = 4
_DEFAULT_MAX_AT_ONCE = 8
_DEFAULT_MAX_PER_SECOND = 10.0
_DEFAULT_LEVEL_TIMEOUT = 30.0
_CUSTOM_RETRY_MAX = 2
_CUSTOM_MAX_AT_ONCE = 3
_CUSTOM_MAX_PER_SECOND = 5.0
_CUSTOM_LEVEL_TIMEOUT = 10.0
_SETTINGS_MAX_AT_ONCE = 3
_SETTINGS_MAX_PER_SECOND = 5.0
_SETTINGS_LEVEL_TIMEOUT = 15.0
_SETTINGS_RETRY_MAX = 2
_SETTINGS_NODE_TIMEOUT = 4.0


def test_executor_config_defaults() -> None:
    """Test ExecutorConfig default values."""
    config = ExecutorConfig()
    assert config.retry_max_attempts == _DEFAULT_RETRY_MAX
    assert config.max_at_once == _DEFAULT_MAX_AT_ONCE
    assert config.max_per_second == _DEFAULT_MAX_PER_SECOND
    assert config.level_timeout == _DEFAULT_LEVEL_TIMEOUT
    assert config.node_timeout is None


def test_executor_config_custom() -> None:
    """Test ExecutorConfig with custom values."""
    config = ExecutorConfig(
        retry_max_attempts=_CUSTOM_RETRY_MAX,
        max_at_once=_CUSTOM_MAX_AT_ONCE,
        max_per_second=_CUSTOM_MAX_PER_SECOND,
        level_timeout=_CUSTOM_LEVEL_TIMEOUT,
    )
    assert config.retry_max_attempts == _CUSTOM_RETRY_MAX
    assert config.max_at_once == _CUSTOM_MAX_AT_ONCE
    assert config.max_per_second == _CUSTOM_MAX_PER_SECOND
    assert config.level_timeout == _CUSTOM_LEVEL_TIMEOUT


def test_executor_config_from_settings() -> None:
    """Test ExecutorConfig.from_settings factory."""
    settings = SproutSettings(
        max_at_once=_SETTINGS_MAX_AT_ONCE,
        max_per_second=_SETTINGS_MAX_PER_SECOND,
        level_timeout_seconds=_SETTINGS_LEVEL_TIMEOUT,
        retry_max_attempts=_SETTINGS_RETRY_MAX,
        node_timeout_seconds=_SETTINGS_NODE_TIMEOUT,
    )
    config = ExecutorConfig.from_settings(settings)
    assert config.max_at_once == _SETTINGS_MAX_AT_ONCE
    assert config.max_per_second == _SETTINGS_MAX_PER_SECOND
    assert config.level_timeout == _SETTINGS_LEVEL_TIMEOUT
    assert config.retry_max_attempts == _SETTINGS_RETRY_MAX
    assert config.node_timeout == _SETTINGS_NODE_TIMEOUT


def test_settings_defaults() -> None:
    """Test SproutSettings default values."""
    settings = SproutSettings()
    assert settings.max_at_once == _DEFAULT_MAX_AT_ONCE
    assert settings.max_per_second == _DEFAULT_MAX_PER_SECOND
    assert settings.level_timeout_seconds == _DEFAULT_LEVEL_TIMEOUT
    assert settings.retry_max_attempts == _DEFAULT_RETRY_MAX
    assert settings.node_timeout_seconds is None
