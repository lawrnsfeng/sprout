# sprout

Async recursive tree traversal framework: a generic, platform-agnostic
framework for recursively fetching hierarchical tree structures with
configurable retry, concurrency gating, timeout policies, partial-result
recovery on cancellation, and rich error reporting.

Standalone — zero dependencies on any other first-party package.

## Install

Sprout is not published on PyPI. The `sprout` package there is an unrelated
project, so install from a release tag of this repository:

```bash
uv add git+https://github.com/lawrnsfeng/sprout --tag v0.2.0
```

Replace `v0.2.0` with the
[release](https://github.com/lawrnsfeng/sprout/releases) you want. Or, inside
a checkout of this repo:

```bash
uv sync --all-extras --all-groups -U
```

## Where to go next

- **[Usage guide](usage.md)** — a worked example for every feature: bounded
  and unbounded crawls, concurrency/rate limiting and per-node timeouts,
  retry tuning, error policies, progress and error hooks, result inspection,
  duplicate-ref handling, and a synchronous entry point for blocking clients.
- **[API reference](api.md)** — exact signatures for every public class and
  function, generated from the source docstrings.
