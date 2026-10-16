# Usage guide

Sprout traverses anything shaped like a tree by repeatedly calling a
**fetcher** you provide — an async function that, given a node reference and
its depth, returns that node's leaf items plus references to its children.
Sprout's `Executor` handles the recursion: concurrency, retry, timeouts,
deduplication, partial-result recovery, and error bookkeeping are all built
in, so the fetcher only needs to know how to fetch *one* node.

Every example below reuses the same running scenario — crawling a directory
tree (a stand-in for any hierarchical source: an S3 bucket, a paginated REST
API, an org chart, a comment thread) — and layers on one feature at a time.

## 1. Bounded crawl (known max depth)

Use `expand_bounded` when you know how deep you want to go — e.g. "list files
up to 3 folders deep". The timeout budget for the whole traversal is
`depth * level_timeout`, computed automatically.

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

## 2. Unbounded crawl (unknown depth)

Use `expand_unbounded` when depth isn't known upfront — an arbitrarily nested
folder tree, an org chart, or a comment thread that could go 2 or 20 levels
deep. Instead of a fixed budget, a `DynamicClock` starts with a base timeout
and extends it by `level_timeout` seconds each time traversal reaches a new
maximum depth, so the crawl doesn't time out just because it turned out to be
deeper than expected.

```python
executor = Executor[str](fetcher=fetch_dir)
tree, report = await executor.expand_unbounded(ChildRef(id="."))
print(f"max depth reached: {report.max_depth_seen}")
```

## 3. Concurrency & rate limiting

`ExecutorConfig` caps how many fetches run at once (`max_at_once`, a
semaphore) and how many run per second (`max_per_second`, a leaky-bucket rate
limiter) — useful when your fetcher hits an API with a rate limit, e.g.
crawling a GitHub org's repos and issues without tripping abuse detection.

```python
from sprout import ExecutorConfig

config = ExecutorConfig(
    max_at_once=5,       # at most 5 concurrent fetches
    max_per_second=10.0, # at most 10 fetches/sec across all of them
    level_timeout=15.0,  # seconds of budget per depth level
    node_timeout=5.0,    # seconds for one fetch attempt of one node
)
executor = Executor[str](fetcher=fetch_dir, config=config)
```

`level_timeout` budgets the whole traversal; `node_timeout` bounds a single
fetch attempt and must be greater than zero. Without it, one hung fetch runs
until the whole-traversal budget expires and takes every in-flight sibling
down with it. With it, the hung attempt raises `NodeTimeoutError` (a
`TimeoutError`), goes through the normal retry policy, and the node fails on
its own with `kind == "timeout"`. A `TimeoutError` your fetcher raises itself
is still an ordinary `fetch_failed`.

Bad arguments fail when the `Executor` is built, not halfway through a crawl:
a `node_timeout` of zero or less (or NaN), and an `on_node_error` or
`on_duplicate` value that is not one of the documented ones, raise
`InvalidParameterError`.

For app-wide settings objects, build the same config from a plain
`SproutSettings` dataclass:

```python
from sprout import ExecutorConfig, SproutSettings

settings = SproutSettings(max_at_once=5, max_per_second=10.0)
config = ExecutorConfig.from_settings(settings)
```

One `Executor` can run any number of traversals, one after another or
concurrently on one event loop. Each has its own dedup state, and they share
the concurrency and rate limits. It is not thread-safe.

## 4. Retry tuning

Retries are powered by [tenacity](https://github.com/jd/tenacity) under the
hood, but sprout stays HTTP-client-agnostic: you supply an `is_retryable`
predicate deciding which exceptions deserve a retry, and an optional
`retry_after_hook` that lets a rate-limited API's `Retry-After` value
override the default exponential backoff. Sprout needs tenacity 9.2 or newer.

Cancellation, `KeyboardInterrupt` and `SystemExit` are never retried,
whatever `is_retryable` returns: they are not failures of the fetch.

```python
from sprout import RetryConfig


class RateLimitError(Exception):
    """Raised by fetch_dir when the upstream API is rate-limited."""

    def __init__(self, retry_after: float) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


def is_retryable(exc: BaseException) -> bool:
    """Only retry rate limits and transient network errors."""
    return isinstance(exc, RateLimitError | TimeoutError)


def retry_after_hook(exc: BaseException) -> float | None:
    """Honour the server's requested backoff, if any."""
    if isinstance(exc, RateLimitError):
        return exc.retry_after
    return None


retry_config = RetryConfig(
    max_attempts=5,
    min_wait=0.5,
    max_wait=30.0,
    is_retryable=is_retryable,
    retry_after_hook=retry_after_hook,
)
executor = Executor[str](fetcher=fetch_dir, retry_config=retry_config)
```

`max_wait` is the ceiling on every retry wait, including waits returned by `retry_after_hook`: a server-requested delay longer than `max_wait` is cut to `max_wait`. Raise `max_wait` if you want long server-directed waits honoured.

## 5. Error handling policy

By default (`on_node_error="skip_mark"`) a failed node is marked with a
`NodeError` and traversal continues elsewhere in the tree — inspect
`report.errors` afterward to see everything that failed. Set
`on_node_error="abort"` for fail-fast semantics: the first failure cancels
every in-flight fetch and raises `TraversalAbortedError`.

```python
from sprout import TraversalAbortedError

# Default: keep going, collect errors.
executor = Executor[str](fetcher=fetch_dir)
tree, report = await executor.expand_bounded(ChildRef(id="."), depth=3)
for err in report.errors:
    print(f"{err.node_id} failed after {err.attempts} attempts: {err.last_exception_repr}")

# Fail-fast: stop the whole crawl on the first error.
strict_executor = Executor[str](fetcher=fetch_dir, on_node_error="abort")
try:
    tree, report = await strict_executor.expand_bounded(ChildRef(id="."), depth=3)
except TraversalAbortedError as exc:
    print(f"aborted at {exc.node_id}: {exc.original_error!r}")
```

A failed node's `NodeError.traceback` holds the formatted traceback. A long
one is truncated to the innermost frames of each exception in the chain, so
it still ends with the exception line, and for a node timeout it still shows
the line the fetch was hanging on.

Cancelling the traversal from outside, for example by wrapping it in
`asyncio.wait_for`, cancels every fetch still in flight before the
cancellation propagates to you.

## 6. Progress reporting

Implement `ProgressHook` to drive a live CLI progress indicator or structured
logging while the crawl runs. `on_children_discovered` fires when a node's
children are spawned; `on_node_complete` fires for leaves with nothing left
to spawn. Hooks run synchronously on the executor's event loop and any
exception they raise is swallowed, so keep them fast and non-blocking.

A hook may also define `on_node_error`. It is optional: the executor calls
it only if the hook has it. It fires once for every `NodeError` added to
`report.errors` (failed, timed-out and cancelled nodes, and skipped
duplicates), so a live error line needs no wrapper around the fetcher.

```python
from sprout import NodeError


class PrintProgress:
    """Minimal ProgressHook that prints as the crawl unfolds."""

    def on_children_discovered(self, parent_id: str, child_ids: list[str]) -> None:
        print(f"{parent_id}: queued {len(child_ids)} children")

    def on_node_complete(self, node_id: str) -> None:
        print(f"{node_id}: done")

    def on_node_error(self, node_id: str, error: NodeError) -> None:
        print(f"{node_id}: {error.kind} after {error.elapsed_ms} ms")


executor = Executor[str](fetcher=fetch_dir, progress_hook=PrintProgress())
```

## 7. Inspecting results

`expand_bounded`/`expand_unbounded` return `(TreeNode, TraversalReport)`.
Iterate the tree for the fetched data, or use the report for a summary:

```python
tree, report = await executor.expand_bounded(ChildRef(id="."), depth=3)

print(report.as_dict)
# {'total_nodes': ..., 'failed_nodes': ..., 'cancelled_nodes': ...,
#  'skipped_nodes': ..., 'max_depth_seen': ..., 'elapsed_ms': ..., 'timed_out': ...}

if report.has_failures:
    print("the crawl is incomplete")

for node in tree.subtree:  # this node, then every descendant, pre-order
    if node.partial:
        print(f"{node.ref.id} was cut short by the grand timeout")
    if node.error is not None:
        print(f"{'/'.join(node.path)} failed: {node.error.kind}")

total = sum(len(node.result.items) for node in tree.subtree if node.result)
print(f"total items: {total}")
```

`report.has_failures` is the one check for "did anything go wrong": it is
true when any node failed, timed out, was cancelled or was skipped, which is
exactly when `report.errors` is not empty. It says nothing about depth:
`expand_bounded` does not fetch the refs below its depth limit and does not
report them either. `report.timed_out` is narrower: the grand timeout cut
unfinished work short.

`tree.outcomes` joins results back to the inputs that produced them, without
any ID conventions. It maps each node ID to its `FetchResult`, or to its
`NodeError` if the node failed, timed out or was cancelled:

```python
from sprout import NodeError

outcomes = tree.outcomes

for ref in my_input_refs:
    outcome = outcomes.get(ref.id)
    if outcome is None:
        print(f"{ref.id}: never reached")
    elif isinstance(outcome, NodeError):
        print(f"{ref.id}: {outcome.kind}")
    else:
        print(f"{ref.id}: {len(outcome.items)} items")
```

For more than the outcome, walk `tree.subtree`: every node keeps its `ref`
(payload included), its `result` or `error`, its `path`, the `attempts` made
and the `elapsed_ms` spent.

After a grand timeout, `attempts` tells the cancelled nodes apart: 0 means
the fetch never started. For a node that had started, `elapsed_ms` says how
long it had been running.

For debugging a run — especially failures or timeouts — dump the whole
execution graph (tree structure, report, and flattened error list) to JSON:

```python
from pathlib import Path

tree.dump_execution_graph(report, Path("crawl-debug.json"))
```

## 8. Duplicate refs

By default (`on_duplicate="skip_mark"`) each `ChildRef.id` is fetched once
per traversal. A later sighting of the same ID, e.g. two parent directories
symlinking to the same target, is recorded as a `duplicate_skipped` error
rather than fetched again.

The skipped ref is still in the tree: it is a child of the parent that
returned it, with its `ref` and payload, the `duplicate_skipped` error and no
result. So an ID can appear more than once in `tree.subtree`, and the tree
can hold more nodes than `report.total_nodes`, which counts fetches.
`tree.outcomes` leaves skipped nodes out, so a skipped ID maps to the fetch
that did happen.

Set `on_duplicate="allow"` when the same ID under two parents is two pieces
of work, not one:

```python
executor = Executor[str](fetcher=fetch_dir, on_duplicate="allow")
```

The same ID is then fetched under every parent that returns it. A ref that
is its own ancestor is still skipped and recorded, so a cycle terminates.
IDs are no longer unique in the tree. Nodes under different parents differ
by `node.path`. If one parent returns the same ID twice, both are fetched and
they share a path. Progress hooks receive bare IDs, so the same ID can be
discovered and completed more than once: count IDs, do not keep them in a
set.
On a graph with many shared nodes, `allow` fetches each one once per path
that reaches it, so prefer `expand_bounded` there.

## 9. Synchronous callers

A script or CLI with a blocking client needs neither `async def` nor its own
event loop. `blocking_fetcher` runs a plain function in a worker thread, and
`run_sync` runs the traversal to completion:

```python
from pathlib import Path

from sprout import ChildRef, Executor, FetchResult, blocking_fetcher


def fetch_dir(ref: ChildRef, depth: int) -> FetchResult[str]:
    path = Path(ref.id)
    files = [str(p) for p in path.iterdir() if p.is_file()]
    dirs = [ChildRef(id=str(p)) for p in path.iterdir() if p.is_dir()]
    return FetchResult(items=files, child_refs=dirs)


executor = Executor[str](fetcher=blocking_fetcher(fetch_dir))
tree, report = executor.run_sync(ChildRef(id="."), depth=3)  # omit depth for unbounded
```

`run_sync` starts its own event loop, so call it from synchronous code only.

A blocking call cannot be interrupted. When a timeout fires, the node is
recorded as timed out or cancelled and the report is final, but the worker
thread keeps running until the call returns. Two things follow:

- `run_sync` waits for the worker threads before it returns. asyncio caps
  that wait at five minutes; a thread still running after that is abandoned
  with a warning and holds up interpreter exit instead.
- `node_timeout` frees the node's concurrency slot while its thread is still
  busy. More blocking calls than `max_at_once` can then run at once, and a
  retry can issue the same call alongside the abandoned one. Abandoned
  threads also fill the thread pool; once it is full, healthy nodes time out
  while waiting for a thread, without their fetch ever being called.

So for a blocking client, set the client's own timeout and leave
`node_timeout` unset.
