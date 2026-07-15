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
from sprout import AsyncTreeExecutor, AsyncTreeExecutorConfig
```

## Development

```bash
uv run pytest
uv run pytest --cov=sprout --cov-report=term-missing
uv run ruff check
uv run ruff format --check
uv run ty check
pre-commit run --all-files
```

Coding standards live in `.claude/rules/`.
