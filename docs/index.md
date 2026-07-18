# sprout

Async recursive tree traversal framework: a generic, platform-agnostic
framework for recursively fetching hierarchical tree structures with
configurable retry, concurrency gating, timeout policies, partial-result
recovery on cancellation, and rich error reporting.

Standalone — zero dependencies on any other first-party package.

## Install

```bash
uv add sprout
```

or, inside this repo:

```bash
uv sync --all-extras --all-groups -U
```

## Where to go next

- **[Usage guide](usage.md)** — a worked example for every feature: bounded
  and unbounded crawls, concurrency/rate limiting, retry tuning, error
  policies, progress hooks, and result inspection.
- **[API reference](api.md)** — exact signatures for every public class and
  function, generated from the source docstrings.
