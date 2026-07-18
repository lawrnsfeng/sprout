# sprout

Async recursive tree traversal framework: a generic, platform-agnostic
framework for recursively fetching hierarchical tree structures with
configurable retry, concurrency gating, timeout policies, partial-result
recovery on cancellation, and rich error reporting.

Standalone — zero dependencies on any other first-party package.

📖 Full docs, use-case walkthroughs, and API reference:
**https://lawrnsfeng.github.io/sprout/**

## Install

```bash
uv sync --all-extras --all-groups -U
```

or

```bash
make sync
```

## Usage

Sprout traverses anything shaped like a tree — a filesystem, an S3 bucket, a
paginated REST API, an org chart, a comment thread — by repeatedly calling a
`Fetcher` you provide. You describe how to fetch *one* node's children; sprout
handles concurrency, retry, timeouts, deduplication, and error bookkeeping.

### Quick start

```python
import asyncio
from pathlib import Path

from sprout import ChildRef, Executor, FetchResult


async def fetch_dir(ref: ChildRef, depth: int) -> FetchResult[str]:
    """List a directory's files (items) and subdirectories (child_refs)."""
    path = Path(ref.id)
    files = [str(p) for p in path.iterdir() if p.is_file()]
    dirs = [ChildRef(id=str(p)) for p in path.iterdir() if p.is_dir()]
    return FetchResult(items=files, child_refs=dirs)


async def main() -> None:
    executor = Executor[str](fetcher=fetch_dir)
    tree, report = await executor.expand_bounded(ChildRef(id="."), depth=3)
    print(f"visited {report.total_nodes} dirs, {report.failed_nodes} failed")


asyncio.run(main())
```

`expand_bounded` caps the crawl at a known depth. For trees whose depth isn't
known upfront (arbitrarily nested folders, comment threads, org charts), use
`expand_unbounded` instead — its timeout budget grows automatically as deeper
levels are discovered.

Sprout also tunes concurrency and rate limits (`ExecutorConfig`), retry
behavior including `Retry-After`-aware backoff (`RetryConfig`), fail-fast vs.
skip-and-continue error policies (`on_node_error`), live progress hooks
(`ProgressHook`), and post-run inspection via `TraversalReport` and
`TreeNode.dump_execution_graph`. See the
**[full usage guide](https://lawrnsfeng.github.io/sprout/usage/)** for a
worked example of every feature, and the
**[API reference](https://lawrnsfeng.github.io/sprout/api/)** for exact
signatures.

## Development

```bash
make test
make cov
make lint
make tc
make pre
make docs   # serve the mkdocs site locally at http://127.0.0.1:8000
```

Coding standards live in `.claude/rules/`.
