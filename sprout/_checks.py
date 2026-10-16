"""Shared numeric validation helpers for Sprout config objects."""

from .exceptions import InvalidParameterError


def require_positive(param: str, value: float) -> None:
    """Require ``value > 0``; NaN is rejected.

    Args:
        param: Parameter name used in the error.
        value: Value to check.

    Raises:
        InvalidParameterError: If ``value`` is not greater than zero.

    """
    if not value > 0:
        raise InvalidParameterError(param=param, requirement="> 0")


def require_non_negative(param: str, value: float) -> None:
    """Require ``value >= 0``; NaN is rejected.

    Args:
        param: Parameter name used in the error.
        value: Value to check.

    Raises:
        InvalidParameterError: If ``value`` is negative or NaN.

    """
    if not value >= 0:
        raise InvalidParameterError(param=param, requirement=">= 0")
