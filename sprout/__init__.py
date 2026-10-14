"""Async recursive tree traversal framework.

A generic, platform-agnostic framework for recursively fetching hierarchical
tree structures with configurable retry, concurrency gating, timeout policies,
partial-result recovery on cancellation, and rich error reporting.

This package is standalone — it has zero dependencies on any other
first-party package.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sprout")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

from .config import ExecutorConfig, SproutSettings
from .exceptions import (
    FetcherError,
    GrandTimeoutExceededError,
    InvalidParameterError,
    SproutError,
    TraversalAbortedError,
)
from .executor import Executor, blocking_fetcher
from .models import (
    ChildRef,
    FetchResult,
    NodeError,
    TraversalReport,
    TreeNode,
)
from .retry import RetryConfig
from .typedefs import (
    Fetcher,
    OnDuplicate,
    OnNodeError,
    ProgressHook,
    RetryAfterHook,
    RetryPredicate,
)

__all__ = [
    "ChildRef",
    "Executor",
    "ExecutorConfig",
    "FetchResult",
    "Fetcher",
    "FetcherError",
    "GrandTimeoutExceededError",
    "InvalidParameterError",
    "NodeError",
    "OnDuplicate",
    "OnNodeError",
    "ProgressHook",
    "RetryAfterHook",
    "RetryConfig",
    "RetryPredicate",
    "SproutError",
    "SproutSettings",
    "TraversalAbortedError",
    "TraversalReport",
    "TreeNode",
    "__version__",
    "blocking_fetcher",
]
