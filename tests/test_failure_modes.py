"""Tests for Sprout executor failure modes."""

import asyncio
import time

import pytest

from sprout import (
    ChildRef,
    Executor,
    ExecutorConfig,
    FetchResult,
    TraversalAbortedError,
)
from sprout.clock import GrandClock

_SKIP_MARK_TOTAL = 3
_ABORT_MAX_ELAPSED = 2.0
_FAIL_DEPTH = 2
_RETRY_ATTEMPTS = 3


class _EventClock(GrandClock):
    """Grand clock that times out when its event is set."""

    def __init__(self) -> None:
        """Initialize with an unset event."""
        super().__init__()
        self.fire = asyncio.Event()

    def remaining_seconds(self) -> float:
        """Report no remaining budget."""
        return 0.0

    async def wait_for_timeout(self) -> None:
        """Time out as soon as the event is set."""
        await self.fire.wait()
        self.cancel()


class SampleItem:
    """Sample item for tree nodes."""

    def __init__(self, name: str) -> None:
        """Initialize SampleItem."""
        self.name = name


@pytest.mark.asyncio
async def test_skip_mark_continues_siblings() -> None:
    """Test on_node_error='skip_mark' continues with error marker."""

    async def fetcher(ref: ChildRef, depth: int) -> FetchResult[SampleItem]:
        """Fail on bad-child."""
        if ref.id == "bad-child":
            msg = "Simulated failure"
            raise RuntimeError(msg)
        if depth == 0:
            return FetchResult(
                child_refs=[
                    ChildRef(id="good-1"),
                    ChildRef(id="bad-child"),
                    ChildRef(id="good-2"),
                ],
            )
        return FetchResult(items=[SampleItem(f"item-{ref.id}")])

    config = ExecutorConfig(retry_max_attempts=1, max_at_once=5)
    executor = Executor[SampleItem](
        fetcher=fetcher,
        config=config,
        on_node_error="skip_mark",
    )

    tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=2)

    assert report.total_nodes == _SKIP_MARK_TOTAL
    assert report.failed_nodes == 1
    assert len(report.errors) == 1

    error = report.errors[0]
    assert error.kind == "fetch_failed"
    assert error.node_id == "bad-child"
    assert error.last_exception_type == "RuntimeError"
    assert error.attempts == 1
    assert "bad-child" in error.path

    bad = next(
        (c for c in tree.children if c.ref and c.ref.id == "bad-child"),
        None,
    )
    assert bad is not None
    assert bad.error is not None
    assert bad.result is None


@pytest.mark.asyncio
async def test_abort_cancels_all() -> None:
    """Test on_node_error='abort' cancels all tasks and raises."""

    async def fetcher(ref: ChildRef, depth: int) -> FetchResult[SampleItem]:
        """Fail on fail-child."""
        if ref.id == "fail-child":
            msg = "Abort trigger"
            raise ValueError(msg)
        await asyncio.sleep(0.1)
        if depth == 0:
            return FetchResult(
                child_refs=[
                    ChildRef(id="child-1"),
                    ChildRef(id="fail-child"),
                    ChildRef(id="child-2"),
                ],
            )
        return FetchResult(items=[SampleItem(f"item-{ref.id}")])

    config = ExecutorConfig(retry_max_attempts=1, max_at_once=5)
    executor = Executor[SampleItem](
        fetcher=fetcher,
        config=config,
        on_node_error="abort",
    )

    with pytest.raises(TraversalAbortedError) as exc_info:
        await executor.expand_bounded(ChildRef(id="root"), depth=2)

    assert exc_info.value.node_id == "fail-child"
    assert isinstance(exc_info.value.original_error, ValueError)


@pytest.mark.asyncio
async def test_concurrent_abort_cancels_within_bounded_time() -> None:
    """Test abort cancels all in-flight tasks promptly."""

    async def fetcher(ref: ChildRef, depth: int) -> FetchResult[SampleItem]:
        """Long-running children, one fails quickly."""
        if ref.id == "fail":
            msg = "abort!"
            raise RuntimeError(msg)
        if depth == 0:
            return FetchResult(
                child_refs=[
                    ChildRef(id="slow-1"),
                    ChildRef(id="slow-2"),
                    ChildRef(id="fail"),
                ],
            )
        await asyncio.sleep(10.0)
        return FetchResult()

    config = ExecutorConfig(
        retry_max_attempts=1,
        max_at_once=10,
        level_timeout=30.0,
    )
    executor = Executor[SampleItem](
        fetcher=fetcher,
        config=config,
        on_node_error="abort",
    )

    start = time.monotonic()
    with pytest.raises(TraversalAbortedError):
        await executor.expand_bounded(ChildRef(id="root"), depth=2)
    elapsed = time.monotonic() - start

    assert elapsed < _ABORT_MAX_ELAPSED


@pytest.mark.asyncio
async def test_error_has_traceback() -> None:
    """Test failed node error includes truncated traceback."""

    async def fetcher(ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Raise with nested calls for stack depth."""

        def inner() -> None:
            msg = "Deep error"
            raise TypeError(msg)

        def outer() -> None:
            inner()

        if ref.id == "root":
            outer()
        return FetchResult()

    config = ExecutorConfig(retry_max_attempts=1, max_at_once=5)
    executor = Executor[SampleItem](
        fetcher=fetcher,
        config=config,
        on_node_error="skip_mark",
    )

    _tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=1)

    assert report.failed_nodes == 1
    error = report.errors[0]
    assert error.traceback is not None
    assert len(error.traceback) > 0
    assert "Traceback" in error.traceback


@pytest.mark.asyncio
async def test_error_has_full_path() -> None:
    """Test node error includes full ancestor path."""

    async def fetcher(_ref: ChildRef, depth: int) -> FetchResult[SampleItem]:
        """Fail at depth 2."""
        if depth == _FAIL_DEPTH:
            msg = f"Fail at depth {_FAIL_DEPTH}"
            raise RuntimeError(msg)
        return FetchResult(child_refs=[ChildRef(id=f"node-{depth + 1}")])

    config = ExecutorConfig(retry_max_attempts=1, max_at_once=5)
    executor = Executor[SampleItem](
        fetcher=fetcher,
        config=config,
        on_node_error="skip_mark",
    )

    _tree, report = await executor.expand_bounded(
        ChildRef(id="node-0"),
        depth=3,
    )

    assert report.failed_nodes == 1
    error = report.errors[0]
    assert error.node_id == "node-2"
    assert error.path == ["node-0", "node-1", "node-2"]


@pytest.mark.asyncio
async def test_retry_exhausted_recorded() -> None:
    """Test retry exhaustion is recorded in error."""
    attempts = 0

    async def fetcher(_ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Raise on every call."""
        nonlocal attempts
        attempts += 1
        msg = f"Attempt {attempts}"
        raise RuntimeError(msg)

    config = ExecutorConfig(
        retry_max_attempts=_RETRY_ATTEMPTS,
        max_at_once=5,
    )
    executor = Executor[SampleItem](
        fetcher=fetcher,
        config=config,
        on_node_error="skip_mark",
    )

    _tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=1)

    assert report.failed_nodes == 1
    assert report.errors[0].attempts == _RETRY_ATTEMPTS


@pytest.mark.asyncio
async def test_fetch_finishing_with_grand_timeout_is_not_dropped() -> None:
    """Test a fetch completing in the same wakeup as the timeout is kept."""
    clock = _EventClock()

    async def fetcher(ref: ChildRef, depth: int) -> FetchResult[SampleItem]:
        """Fire the grand timeout while the root fetch completes."""
        clock.fire.set()
        if depth == 0:
            return FetchResult(
                items=[SampleItem(ref.id)],
                child_refs=[ChildRef(id="child-1"), ChildRef(id="child-2")],
            )
        return FetchResult()

    executor = Executor[SampleItem](fetcher=fetcher)

    tree, report = await executor._execute(ChildRef(id="root"), 2, clock)

    assert tree.result is not None
    assert report.total_nodes == 1
    assert report.timed_out is True
    assert report.cancelled_nodes == len(tree.children) == 2
    assert all(child.error is not None for child in tree.children)


@pytest.mark.asyncio
async def test_timeout_after_all_work_finished_is_not_a_timeout() -> None:
    """Test the timer firing with nothing left to fetch cuts nothing short."""
    clock = _EventClock()

    async def fetcher(_ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Fire the grand timeout while the only fetch completes."""
        clock.fire.set()
        return FetchResult()

    executor = Executor[SampleItem](fetcher=fetcher)

    _tree, report = await executor._execute(ChildRef(id="root"), 1, clock)

    assert report.total_nodes == 1
    assert report.timed_out is False
    assert report.errors == []


@pytest.mark.asyncio
async def test_abort_leaves_no_pending_tasks() -> None:
    """Test aborting a traversal does not leak the grand-timeout task."""

    async def fetcher(_ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Fail at once."""
        msg = "abort!"
        raise RuntimeError(msg)

    executor = Executor[SampleItem](
        fetcher=fetcher,
        config=ExecutorConfig(retry_max_attempts=1),
        on_node_error="abort",
    )

    with pytest.raises(TraversalAbortedError):
        await executor.expand_bounded(ChildRef(id="root"), depth=1)

    assert asyncio.all_tasks() == {asyncio.current_task()}


@pytest.mark.asyncio
async def test_fetcher_cancellation_marks_one_node_and_continues() -> None:
    """Test a fetch that ends cancelled is recorded without crashing the run."""
    calls: list[str] = []

    async def fetcher(ref: ChildRef, depth: int) -> FetchResult[SampleItem]:
        """Raise CancelledError for one child."""
        calls.append(ref.id)
        if ref.id == "cancelled-child":
            raise asyncio.CancelledError
        if depth == 0:
            return FetchResult(
                child_refs=[ChildRef(id="cancelled-child"), ChildRef(id="good")],
            )
        return FetchResult(items=[SampleItem(ref.id)])

    executor = Executor[SampleItem](fetcher=fetcher)

    _tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=2)

    assert calls.count("cancelled-child") == 1
    assert report.total_nodes == 2
    assert report.cancelled_nodes == 1
    assert report.timed_out is False
    assert [e.node_id for e in report.errors] == ["cancelled-child"]
