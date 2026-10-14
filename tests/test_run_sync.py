"""Tests for the synchronous entry point and blocking fetchers."""

import asyncio
import gc
import threading
import time
import warnings

import pytest

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
_REUSE_RATE = 5.0
_REUSE_MIN_ELAPSED = 0.36


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


def test_executor_reused_across_event_loops() -> None:
    """Test one Executor serves traversals on two different event loops."""
    executor = Executor[str](
        fetcher=blocking_fetcher(_fetch),
        config=ExecutorConfig(max_at_once=1),
    )

    for _ in range(2):
        _tree, report = asyncio.run(
            executor.expand_bounded(ChildRef(id="root"), depth=2),
        )
        assert report.total_nodes == _TOTAL_NODES
        assert report.errors == []


@pytest.mark.asyncio
async def test_run_sync_inside_a_running_loop_leaves_no_trace() -> None:
    """Test a refused run_sync neither swaps the gate nor leaks a coroutine."""
    executor = Executor[str](fetcher=blocking_fetcher(_fetch))
    await executor.expand_bounded(ChildRef(id="root"), depth=1)
    gate = executor._gate

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(RuntimeError, match="running event loop"):
            executor.run_sync(ChildRef(id="root"), depth=1)
        gc.collect()

    assert executor._gate is gate
    assert [str(w.message) for w in caught] == []


def test_node_timeout_with_blocking_fetcher_can_exceed_max_at_once() -> None:
    """Pin the documented hazard: a timed-out thread still occupies no slot."""
    lock = threading.Lock()
    running = 0
    peak = 0

    def slow(_ref: ChildRef, depth: int) -> FetchResult[str]:
        """Root answers at once; children block past the node timeout."""
        nonlocal running, peak
        if depth == 0:
            return FetchResult(
                child_refs=[ChildRef(id=f"child-{i}") for i in range(3)],
            )
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(_BLOCK_SECONDS)
        with lock:
            running -= 1
        return FetchResult()

    executor = Executor[str](
        fetcher=blocking_fetcher(slow),
        config=ExecutorConfig(
            max_at_once=1,
            node_timeout=_GRAND_TIMEOUT,
            retry_max_attempts=1,
        ),
    )

    _tree, report = executor.run_sync(ChildRef(id="root"), depth=2)

    assert {e.kind for e in report.errors} == {"timeout"}
    assert peak > 1


def test_rate_limit_carries_across_run_sync_calls() -> None:
    """Test pacing survives the new event loop each run_sync starts."""

    def leaf(_ref: ChildRef, _depth: int) -> FetchResult[str]:
        """Leaf fetch."""
        return FetchResult()

    executor = Executor[str](
        fetcher=blocking_fetcher(leaf),
        config=ExecutorConfig(max_per_second=_REUSE_RATE),
    )

    start = time.monotonic()
    for _ in range(3):
        executor.run_sync(ChildRef(id="root"), depth=1)
    elapsed = time.monotonic() - start

    assert elapsed >= _REUSE_MIN_ELAPSED
