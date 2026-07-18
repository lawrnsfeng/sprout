# sprout

Async recursive tree traversal framework: a generic, platform-agnostic
framework for recursively fetching hierarchical tree structures with
configurable retry, concurrency gating, timeout policies, partial-result
recovery on cancellation, and rich error reporting.

Standalone — zero dependencies on any other first-party package.

## Install

```bash
uv sync --all-extras --all-groups -U
```

or

```bash
make sync
```

## Usage

```python
from sprout import Executor, ExecutorConfig
```

## Development

```bash
make test
make cov
make lint
make tc
make pre
```

Coding standards live in `.claude/rules/`.
