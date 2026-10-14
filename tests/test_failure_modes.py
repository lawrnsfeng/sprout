"""Tests for Sprout executor failure modes."""

import asyncio
import math
import time
from typing import TYPE_CHECKING, cast

import pytest

from sprout import (
    ChildRef,
    Executor,
    ExecutorConfig,
    FetchResult,
    InvalidParameterError,
    NodeTimeoutError,
    RetryConfig,
    TraversalAbortedError,
)
from sprout.clock import GrandClock

if TYPE_CHECKING:
    from sprout.typedefs import OnDuplicate, OnNodeError

_SKIP_MARK_TOTAL = 3
_ABORT_MAX_ELAPSED = 2.0
_FAIL_DEPTH = 2
_RETRY_ATTEMPTS = 3
_NODE_TIMEOUT = 0.1
_NODE_TIMEOUT_MS = 100
_GRAND_TIMEOUT = 0.3


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


async def _one_hung_child(ref: ChildRef, depth: int) -> FetchResult[SampleItem]:
    """Root returns a child that hangs and a child that answers."""
    if depth == 0:
        return FetchResult(
            child_refs=[ChildRef(id="hung"), ChildRef(id="good")],
        )
    if ref.id == "hung":
        await asyncio.sleep(10.0)
    return FetchResult(items=[SampleItem(ref.id)])


@pytest.mark.asyncio
async def test_node_timeout_fails_only_the_hung_node() -> None:
    """Test node_timeout names the hung node and leaves its siblings alone."""
    config = ExecutorConfig(retry_max_attempts=1, node_timeout=_NODE_TIMEOUT)
    executor = Executor[SampleItem](fetcher=_one_hung_child, config=config)

    tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=2)

    assert report.timed_out is False
    assert report.total_nodes == 2
    assert report.failed_nodes == 1

    error = report.errors[0]
    assert error.kind == "timeout"
    assert error.node_id == "hung"
    assert error.last_exception_type == NodeTimeoutError.__name__
    assert "hung" in error.last_exception_repr
    assert error.attempts == 1
    assert error.elapsed_ms >= _NODE_TIMEOUT_MS
    assert error.as_dict["elapsed_ms"] == error.elapsed_ms
    assert tree.attempts == 1


@pytest.mark.asyncio
async def test_grand_timeout_reports_which_node_was_running() -> None:
    """Test cancelled nodes say whether they were running or still queued."""
    config = ExecutorConfig(max_at_once=1, level_timeout=_GRAND_TIMEOUT)
    executor = Executor[SampleItem](fetcher=_one_hung_child, config=config)

    _tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=2)

    assert report.timed_out is True
    mp_str_error = {e.node_id: e for e in report.errors}
    assert {e.kind for e in report.errors} == {"cancelled"}
    assert mp_str_error["hung"].attempts == 1
    assert mp_str_error["hung"].elapsed_ms > 0
    assert mp_str_error["good"].attempts == 0
    assert mp_str_error["good"].elapsed_ms == 0


@pytest.mark.asyncio
async def test_attempts_is_the_actual_count() -> None:
    """Test a non-retryable failure reports one attempt, not the maximum."""

    async def fetcher(_ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Fail at once."""
        msg = "not retryable"
        raise RuntimeError(msg)

    executor = Executor[SampleItem](
        fetcher=fetcher,
        retry_config=RetryConfig(
            max_attempts=_RETRY_ATTEMPTS,
            is_retryable=lambda _exc: False,
        ),
    )

    _tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=1)

    assert report.errors[0].kind == "fetch_failed"
    assert report.errors[0].attempts == 1


@pytest.mark.asyncio
async def test_fetch_starting_after_clock_expiry_is_marked_cancelled() -> None:
    """Test a fetch that starts once the clock has expired never runs."""
    clock = _EventClock()
    calls: list[str] = []

    async def fetcher(ref: ChildRef, depth: int) -> FetchResult[SampleItem]:
        """Expire the clock from the root fetch, before the child starts."""
        calls.append(ref.id)
        if depth == 0:
            clock.cancel()
            return FetchResult(child_refs=[ChildRef(id="late")])
        return FetchResult()

    executor = Executor[SampleItem](fetcher=fetcher)

    tree, report = await executor._execute(ChildRef(id="root"), 2, clock)

    assert calls == ["root"]
    assert report.timed_out is True
    assert report.cancelled_nodes == 1
    late = tree.children[0]
    assert late.error is not None
    assert (late.error.kind, late.error.attempts) == ("cancelled", 0)


@pytest.mark.asyncio
async def test_caller_cancellation_stops_in_flight_fetches() -> None:
    """Test cancelling the traversal cancels its fetches, leaving no tasks."""
    cancelled: list[str] = []

    async def fetcher(ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Hang until cancelled."""
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            cancelled.append(ref.id)
            raise
        return FetchResult()

    executor = Executor[SampleItem](fetcher=fetcher)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            executor.expand_bounded(ChildRef(id="root"), depth=1),
            _NODE_TIMEOUT,
        )

    assert cancelled == ["root"]
    assert asyncio.all_tasks() == {asyncio.current_task()}


@pytest.mark.asyncio
async def test_attempts_not_inflated_by_cancelled_backoff() -> None:
    """Test a node cancelled while waiting to retry counts real attempts."""
    calls = 0

    async def fetcher(_ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Fail at once, every time."""
        nonlocal calls
        calls += 1
        msg = "always"
        raise RuntimeError(msg)

    executor = Executor[SampleItem](
        fetcher=fetcher,
        config=ExecutorConfig(level_timeout=_GRAND_TIMEOUT),
        retry_config=RetryConfig(max_attempts=_RETRY_ATTEMPTS, min_wait=5.0),
    )

    _tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=1)

    assert calls == 1
    assert report.errors[0].kind == "cancelled"
    assert report.errors[0].attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("node_timeout", [None, 5.0])
async def test_fetchers_own_timeout_error_is_a_plain_failure(
    node_timeout: float | None,
) -> None:
    """Test a TimeoutError raised by the fetcher is not a node timeout."""

    async def fetcher(_ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Raise the client's own timeout."""
        msg = "socket connect timed out"
        raise TimeoutError(msg)

    config = ExecutorConfig(retry_max_attempts=1, node_timeout=node_timeout)
    executor = Executor[SampleItem](fetcher=fetcher, config=config)

    _tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=1)

    assert report.errors[0].kind == "fetch_failed"
    assert report.errors[0].last_exception_type == "TimeoutError"


@pytest.mark.asyncio
async def test_node_timeout_is_retried_as_a_timeout_error() -> None:
    """Test a retry predicate for TimeoutError also retries node timeouts."""
    calls = 0

    async def fetcher(_ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Hang on every attempt."""
        nonlocal calls
        calls += 1
        await asyncio.sleep(10.0)
        return FetchResult()

    executor = Executor[SampleItem](
        fetcher=fetcher,
        config=ExecutorConfig(node_timeout=0.05),
        retry_config=RetryConfig(
            max_attempts=2,
            min_wait=0.01,
            is_retryable=lambda exc: isinstance(exc, TimeoutError),
        ),
    )

    _tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=1)

    assert calls == report.errors[0].attempts == 2
    assert report.errors[0].kind == "timeout"


@pytest.mark.parametrize("node_timeout", [0, -1.0])
def test_non_positive_node_timeout_is_rejected(node_timeout: float) -> None:
    """Test node_timeout must be positive, as zero would fail every fetch."""

    async def fetcher(_ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Leaf fetcher."""
        return FetchResult()

    with pytest.raises(InvalidParameterError, match="node_timeout"):
        Executor[SampleItem](
            fetcher=fetcher,
            config=ExecutorConfig(node_timeout=node_timeout),
        )


@pytest.mark.asyncio
async def test_second_cancellation_during_cleanup_leaks_nothing() -> None:
    """Test cancelling again while fetches wind down still stops the clock."""

    async def fetcher(_ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Hang, and be slow to honour cancellation."""
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            await asyncio.sleep(_NODE_TIMEOUT)
            raise
        return FetchResult()

    executor = Executor[SampleItem](fetcher=fetcher)
    traversal = asyncio.create_task(
        executor.expand_bounded(ChildRef(id="root"), depth=1),
    )

    await asyncio.sleep(_NODE_TIMEOUT / 2)
    traversal.cancel()
    await asyncio.sleep(_NODE_TIMEOUT / 2)
    traversal.cancel()
    with pytest.raises(asyncio.CancelledError):
        await traversal
    await asyncio.sleep(_NODE_TIMEOUT * 2)

    assert asyncio.all_tasks() == {asyncio.current_task()}


def test_invalid_executor_arguments_are_rejected() -> None:
    """Test a misspelt policy or a NaN timeout fails at construction."""

    async def fetcher(_ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Leaf fetcher."""
        return FetchResult()

    with pytest.raises(InvalidParameterError, match="on_duplicate"):
        Executor[SampleItem](
            fetcher=fetcher,
            on_duplicate=cast("OnDuplicate", "Allow"),
        )
    with pytest.raises(InvalidParameterError, match="on_node_error"):
        Executor[SampleItem](
            fetcher=fetcher,
            on_node_error=cast("OnNodeError", "ignore"),
        )
    with pytest.raises(InvalidParameterError, match="node_timeout"):
        Executor[SampleItem](
            fetcher=fetcher,
            config=ExecutorConfig(node_timeout=math.nan),
        )


async def _hang_here() -> None:
    """Hang until cancelled."""
    await asyncio.sleep(10.0)


async def _dive(levels: int) -> None:
    """Recurse to build a deep stack, then hang at the bottom."""
    if levels:
        await _dive(levels - 1)
        return
    await _hang_here()


@pytest.mark.asyncio
async def test_timeout_traceback_shows_where_the_fetch_was_hanging() -> None:
    """Test a truncated node-timeout traceback keeps the fetcher's hang line."""

    async def fetcher(_ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Hang at the bottom of a deep call chain."""
        await _dive(8)
        return FetchResult()

    config = ExecutorConfig(retry_max_attempts=1, node_timeout=_NODE_TIMEOUT)
    executor = Executor[SampleItem](fetcher=fetcher, config=config)

    _tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=1)

    tb = report.errors[0].traceback
    assert tb is not None
    assert "truncated" in tb
    assert "in _hang_here" in tb
    assert tb.rstrip().splitlines()[-1].startswith("sprout.exceptions.NodeTimeoutError")
