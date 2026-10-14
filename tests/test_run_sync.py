"""Tests for the synchronous entry point and blocking fetchers."""

import threading
import time

from sprout import (
    ChildRef,
    Executor,
    ExecutorConfig,
    FetchResult,
    blocking_fetcher,
)

_TOTAL_NODES = 4
_MAX_DEPTH = 3
_GRAND_TIMEOUT = 0.1
_BLOCK_SECONDS = 0.5


def _fetch(ref: ChildRef, depth: int) -> FetchResult[str]:
    """Blocking fetch: root has three children, each a leaf."""
    time.sleep(0.01)
    thread = threading.current_thread().name
    if depth == 0:
        return FetchResult(
            items=[thread],
            child_refs=[ChildRef(id=f"child-{i}") for i in range(3)],
        )
    return FetchResult(items=[thread], extra={"ref": ref.id})


def test_run_sync_bounded_with_blocking_fetcher() -> None:
    """Test run_sync drives a blocking fetcher off the calling thread."""
    executor = Executor[str](fetcher=blocking_fetcher(_fetch))

    tree, report = executor.run_sync(ChildRef(id="root"), depth=2)

    assert report.total_nodes == _TOTAL_NODES
    assert report.errors == []
    threads = {item for n in tree.subtree if n.result for item in n.result.items}
    assert threading.current_thread().name not in threads


def test_run_sync_unbounded() -> None:
    """Test run_sync without a depth expands until the tree runs out."""

    def chain(_ref: ChildRef, depth: int) -> FetchResult[str]:
        """Return one child per level down to the maximum depth."""
        if depth < _MAX_DEPTH:
            return FetchResult(child_refs=[ChildRef(id=f"node-{depth + 1}")])
        return FetchResult()

    executor = Executor[str](fetcher=blocking_fetcher(chain))

    _tree, report = executor.run_sync(ChildRef(id="node-0"))

    assert report.max_depth_seen == _MAX_DEPTH


def test_run_sync_twice_on_one_executor() -> None:
    """Test a second run_sync works although each call has its own loop."""
    executor = Executor[str](
        fetcher=blocking_fetcher(_fetch),
        config=ExecutorConfig(max_at_once=1),
    )

    for _ in range(2):
        _tree, report = executor.run_sync(ChildRef(id="root"), depth=2)
        assert report.total_nodes == _TOTAL_NODES
        assert report.errors == []


def test_run_sync_waits_for_blocking_fetch_after_timeout() -> None:
    """Test the report is final at the timeout but run_sync joins the thread."""

    def slow(_ref: ChildRef, _depth: int) -> FetchResult[str]:
        """Block well past the grand timeout."""
        time.sleep(_BLOCK_SECONDS)
        return FetchResult()

    executor = Executor[str](
        fetcher=blocking_fetcher(slow),
        config=ExecutorConfig(level_timeout=_GRAND_TIMEOUT),
    )

    start = time.monotonic()
    _tree, report = executor.run_sync(ChildRef(id="root"), depth=1)
    elapsed = time.monotonic() - start

    assert report.timed_out is True
    assert report.elapsed_ms < _BLOCK_SECONDS * 1000 / 2
    assert elapsed >= _BLOCK_SECONDS * 0.9
