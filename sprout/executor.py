"""Sprout executor for recursive traversal."""

import asyncio
import contextlib
import math
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast, get_args

from .clock import DynamicClock, GrandClock, StaticClock
from .concurrency import ConcurrencyGate
from .config import ExecutorConfig
from .exceptions import (
    FetcherError,
    InvalidParameterError,
    NodeTimeoutError,
    TraversalAbortedError,
)
from .models import (
    ChildRef,
    FetchResult,
    NodeError,
    TraversalReport,
    TreeNode,
)
from .retry import RetryConfig
from .typedefs import Fetcher, OnDuplicate, OnNodeError, ProgressHook


def _truncate_traceback(exc: Exception, *, max_lines: int = 20) -> str:
    """Extract and truncate exception traceback, keeping each tail.

    A chained exception is formatted as one section per exception, root
    cause first. When the whole is too long, every section keeps its tail:
    the innermost frames and the exception line. A wrapped error therefore
    still shows where the original one arose, such as the line a fetch was
    hanging on when its node timeout fired.

    Args:
        exc: Exception to extract traceback from.
        max_lines: Approximate number of lines to keep.

    Returns:
        Truncated traceback string.

    """
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    if len(tb_lines) <= max_lines:
        return "".join(tb_lines)

    # The line that links two chained exceptions starts with a newline.
    sections: list[list[str]] = [[]]
    for line in tb_lines:
        if line.startswith("\n"):
            sections.append([])
        sections[-1].append(line)

    keep = max(max_lines // len(sections), 2)
    parts: list[str] = []
    for section in sections:
        if len(section) <= keep:
            parts.extend(section)
            continue
        link = section[0] if section[0].startswith("\n") else ""
        parts.extend([link, "... (truncated)\n", *section[-keep:]])
    return "".join(parts)


def blocking_fetcher[T](
    fetch: Callable[[ChildRef, int], FetchResult[T]],
) -> Fetcher[T]:
    """Adapt a blocking fetch function to the async ``Fetcher`` protocol.

    Each call runs in a worker thread, so a fetcher built on a blocking
    client can be used unchanged.

    A blocking call cannot be interrupted: on a timeout the node is recorded
    and the report is final, but the thread runs until the call returns.
    ``node_timeout`` therefore frees a concurrency slot while its thread is
    still busy, so more calls than ``max_at_once`` can run at once and a
    retry can overlap the abandoned call. Give the underlying client its own
    timeout and leave ``node_timeout`` unset.

    Args:
        fetch: Blocking function taking a node reference and its depth.

    Returns:
        Async fetcher that runs ``fetch`` in a worker thread.

    """

    async def fetcher(ref: ChildRef, depth: int, /) -> FetchResult[T]:
        # ponytail: the loop's default thread pool caps real concurrency at
        # its own size; give the loop a larger pool if max_at_once exceeds it.
        return await asyncio.to_thread(fetch, ref, depth)

    return fetcher


@dataclass
class _SpawnContext[T]:
    """Context bundle passed to child-spawning logic.

    Attributes:
        report: Mutable traversal report accumulating stats.
        live_tasks: Set of currently running asyncio tasks.
        node_map: Mapping from task to tree node.
        max_depth: Maximum traversal depth (None = unbounded).
        clock: Grand timeout clock.
        seen_refs: IDs of every ref spawned so far in this traversal.

    """

    report: TraversalReport
    live_tasks: set[asyncio.Task[FetchResult[T]]]
    node_map: dict[asyncio.Task[FetchResult[T]], "TreeNode[T]"]
    max_depth: int | None
    clock: GrandClock
    seen_refs: set[str]


class Executor[T]:
    """Executor for async recursive tree traversal.

    Orchestrates recursive fetching with retry, concurrency control,
    grand timeout, configurable error handling, deduplication, and
    full ancestor path tracking.

    One Executor can run any number of traversals, one after another or
    concurrently on one event loop. Each traversal has its own dedup state;
    they share the concurrency and rate limits. An Executor is not
    thread-safe: do not run traversals on it from two threads at once.
    """

    def __init__(  # noqa: PLR0913
        self,
        fetcher: Fetcher[T],
        config: ExecutorConfig | None = None,
        *,
        on_node_error: OnNodeError = "skip_mark",
        on_duplicate: OnDuplicate = "skip_mark",
        retry_config: RetryConfig | None = None,
        progress_hook: ProgressHook | None = None,
    ) -> None:
        """Initialize Executor.

        Args:
            fetcher: Async callable that fetches children for a node.
            config: Executor tuning (defaults when omitted).
            on_node_error: Error handling policy.
            on_duplicate: What to do with a child ref whose ID was already
                seen. ``skip_mark`` fetches each ID once per traversal and
                records later sightings as ``duplicate_skipped``. ``allow``
                fetches the same ID under every parent that returns it; only
                a ref that is its own ancestor (a cycle) is skipped.
            retry_config: Override retry configuration (uses config values
                if None).
            progress_hook: Optional hook called on child discovery, leaf
                completion and, if it defines ``on_node_error``, node errors.

        """
        cfg = config or ExecutorConfig()
        for param, value, allowed in (
            ("on_node_error", on_node_error, get_args(OnNodeError)),
            ("on_duplicate", on_duplicate, get_args(OnDuplicate)),
        ):
            if value not in allowed:
                raise InvalidParameterError(
                    param=param,
                    requirement=f"one of {allowed}",
                )
        if cfg.node_timeout is not None and (
            math.isnan(cfg.node_timeout) or cfg.node_timeout <= 0
        ):
            raise InvalidParameterError(
                param="node_timeout",
                requirement="> 0 (or None for no per-node timeout)",
            )
        self._fetcher = fetcher
        self._retry_config = retry_config or RetryConfig(
            max_attempts=cfg.retry_max_attempts,
        )
        self._gate = ConcurrencyGate(cfg.max_at_once, cfg.max_per_second)
        self._level_timeout = cfg.level_timeout
        self._node_timeout = cfg.node_timeout
        self._on_node_error = on_node_error
        self._on_duplicate = on_duplicate
        self._progress_hook = progress_hook

    async def expand_bounded(
        self,
        root_ref: ChildRef,
        depth: int,
    ) -> tuple[TreeNode[T], TraversalReport]:
        """Expand tree with bounded depth using a static timeout.

        Args:
            root_ref: Reference to the root node.
            depth: Maximum depth to traverse (must be >= 1).

        Returns:
            Tuple of (root TreeNode, TraversalReport).

        Raises:
            InvalidParameterError: If depth < 1.
            TraversalAbortedError: If on_node_error="abort" and a node fails.

        """
        if depth < 1:
            raise InvalidParameterError(param="depth", requirement=">= 1")

        clock = StaticClock(depth * self._level_timeout)
        return await self._execute(root_ref, depth, clock)

    async def expand_unbounded(
        self,
        root_ref: ChildRef,
    ) -> tuple[TreeNode[T], TraversalReport]:
        """Expand tree with unbounded depth using a dynamic timeout.

        Args:
            root_ref: Reference to the root node.

        Returns:
            Tuple of (root TreeNode, TraversalReport).

        Raises:
            TraversalAbortedError: If on_node_error="abort" and a node fails.

        """
        clock = DynamicClock(
            base_seconds=self._level_timeout,
            per_level_seconds=self._level_timeout,
        )
        return await self._execute(root_ref, None, clock)

    def run_sync(
        self,
        root_ref: ChildRef,
        depth: int | None = None,
    ) -> tuple[TreeNode[T], TraversalReport]:
        """Run a traversal to completion from synchronous code.

        Starts its own event loop, so it cannot be called from inside a
        running one; await ``expand_bounded``/``expand_unbounded`` there.

        With a ``blocking_fetcher``, this waits for the worker threads
        before returning, also after a timeout: a blocking call that is
        still running cannot be interrupted. asyncio caps that wait at five
        minutes.

        Args:
            root_ref: Reference to the root node.
            depth: Maximum depth to traverse (None = unbounded).

        Returns:
            Tuple of (root TreeNode, TraversalReport).

        Raises:
            InvalidParameterError: If depth < 1.
            TraversalAbortedError: If on_node_error="abort" and a node fails.

        """
        traversal = (
            self.expand_unbounded(root_ref)
            if depth is None
            else self.expand_bounded(root_ref, depth)
        )
        try:
            return asyncio.run(traversal)
        finally:
            # Never started if asyncio.run() refused a running loop.
            traversal.close()

    async def _execute(
        self,
        root_ref: ChildRef,
        max_depth: int | None,
        clock: GrandClock,
    ) -> tuple[TreeNode[T], TraversalReport]:
        """Core traversal loop.

        Args:
            root_ref: Root node reference.
            max_depth: Maximum depth (None = unbounded).
            clock: Grand timeout clock.

        Returns:
            Tuple of (root TreeNode, TraversalReport).

        """
        clock.start()

        root_node: TreeNode[T] = TreeNode(ref=root_ref, depth=0)
        report = TraversalReport()
        live_tasks: set[asyncio.Task[FetchResult[T]]] = set()
        node_map: dict[asyncio.Task[FetchResult[T]], TreeNode[T]] = {}

        timeout_task = asyncio.create_task(clock.wait_for_timeout())

        ctx: _SpawnContext[T] = _SpawnContext(
            report=report,
            live_tasks=live_tasks,
            node_map=node_map,
            max_depth=max_depth,
            clock=clock,
            seen_refs={root_ref.id},
        )

        root_task = self._create_fetch_task(root_node, clock)
        live_tasks.add(root_task)
        node_map[root_task] = root_node

        try:
            while live_tasks:
                wait_set: set[asyncio.Task[FetchResult[T]] | asyncio.Task[None]] = {
                    timeout_task,
                    *live_tasks,
                }
                done, pending = await asyncio.wait(
                    wait_set,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                live_tasks = cast(
                    "set[asyncio.Task[FetchResult[T]]]",
                    pending - {timeout_task},
                )
                ctx.live_tasks = live_tasks

                # Fetches that finished in the same wakeup as the grand timeout
                # are still accounted for before the timeout is handled.
                for fetch_task in cast(
                    "set[asyncio.Task[FetchResult[T]]]",
                    done - {timeout_task},
                ):
                    node = node_map.pop(fetch_task)

                    if fetch_task.cancelled():
                        report.timed_out = report.timed_out or clock.is_cancelled
                        self._hook_node_error(node.mark_cancelled(report))
                        continue

                    try:
                        fetch_result = fetch_task.result()
                    except FetcherError as fetcher_err:
                        self._handle_fetch_error(
                            node,
                            fetcher_err.original_error,
                            report,
                        )
                    else:
                        self._handle_success_and_spawn_children(
                            node,
                            fetch_result,
                            ctx,
                        )

                if timeout_task in done:
                    await self._handle_timeout(report, live_tasks, node_map)
        finally:
            # Anything still mapped was not handled: the traversal aborted or
            # its caller cancelled it. Do not leave those fetches running.
            # Cancel all of them before waiting, so a second cancellation
            # arriving during the wait leaves nothing running.
            timeout_task.cancel()
            for task in node_map:
                task.cancel()
            await asyncio.gather(timeout_task, *node_map, return_exceptions=True)

        report.elapsed_ms = clock.elapsed_ms
        return root_node, report

    async def _handle_timeout(
        self,
        report: TraversalReport,
        live_tasks: set[asyncio.Task[FetchResult[T]]],
        node_map: dict[asyncio.Task[FetchResult[T]], "TreeNode[T]"],
    ) -> None:
        """Cancel all live tasks on grand timeout and mark nodes."""
        if not live_tasks:
            return
        report.timed_out = True
        for task in live_tasks:
            task.cancel()
        await asyncio.gather(*live_tasks, return_exceptions=True)
        for task in list(live_tasks):
            node = node_map.pop(task)
            self._hook_node_error(node.mark_cancelled(report))
        live_tasks.clear()

    def _handle_fetch_error(
        self,
        node: "TreeNode[T]",
        exc: Exception,
        report: TraversalReport,
    ) -> None:
        """Handle fetch error — skip_mark or abort depending on policy.

        On abort the caller's cleanup cancels the fetches still in flight.

        Raises:
            TraversalAbortedError: If on_node_error="abort".

        """
        node_error = node.mark_failed(
            exc,
            report,
            traceback=_truncate_traceback(exc),
        )
        self._hook_node_error(node_error)

        if self._on_node_error == "abort":
            raise TraversalAbortedError(
                message=(f"Traversal aborted due to error at node {node_error.node_id}"),
                node_id=node_error.node_id,
                original_error=exc,
            ) from exc

    def _handle_success_and_spawn_children(
        self,
        node: "TreeNode[T]",
        fetch_result: "FetchResult[T]",
        ctx: "_SpawnContext[T]",
    ) -> None:
        """Attach fetch result to node and spawn child tasks."""
        node.mark_fetched(fetch_result, ctx.report)

        if isinstance(ctx.clock, DynamicClock):
            task = asyncio.create_task(ctx.clock.notify_depth(node.depth))
            task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None,
            )

        parent_id = node.node_id

        if not fetch_result.child_refs:
            self._hook_node_complete(parent_id)
            return

        child_depth = node.depth + 1
        if ctx.max_depth is not None and child_depth >= ctx.max_depth:
            self._hook_node_complete(parent_id)
            return

        # "allow" blocks only a ref that is its own ancestor (a cycle);
        # "skip_mark" blocks every ID spawned so far in this traversal.
        # ponytail: "allow" costs O(depth) per parent; keep the ancestor set
        # on the node instead if deep trees make this slow.
        allow = self._on_duplicate == "allow"
        path: list[str] | None = node.path if allow else None
        ancestors = set(path or ())

        spawned_ids: list[str] = []
        for child_ref in fetch_result.child_refs:
            if child_ref.id in (ancestors if allow else ctx.seen_refs):
                # The path is built once per parent, not per duplicate.
                path = path or node.path
                self._hook_node_error(
                    node.record_duplicate_child(child_ref, ctx.report, path=path),
                )
                continue
            if not allow:
                ctx.seen_refs.add(child_ref.id)

            child_node = node.spawn_child(child_ref)

            child_task = self._create_fetch_task(child_node, ctx.clock)
            ctx.live_tasks.add(child_task)
            ctx.node_map[child_task] = child_node
            spawned_ids.append(child_ref.id)

        if spawned_ids:
            self._hook_children_discovered(parent_id, spawned_ids)
        else:
            self._hook_node_complete(parent_id)

    def _hook_children_discovered(
        self,
        parent_id: str,
        child_ids: list[str],
    ) -> None:
        """Call progress hook on_children_discovered.

        Suppresses any exception raised by the hook.
        """
        if self._progress_hook is None:
            return
        with contextlib.suppress(Exception):
            self._progress_hook.on_children_discovered(parent_id, child_ids)

    def _hook_node_complete(self, node_id: str) -> None:
        """Call progress hook on_node_complete, suppressing exceptions."""
        if self._progress_hook is None:
            return
        with contextlib.suppress(Exception):
            self._progress_hook.on_node_complete(node_id)

    def _hook_node_error(self, error: NodeError) -> None:
        """Call progress hook on_node_error, if the hook defines it.

        The method is optional so hooks written before it existed keep
        working. Suppresses any exception raised by the hook.
        """
        on_node_error = getattr(self._progress_hook, "on_node_error", None)
        if on_node_error is None:
            return
        with contextlib.suppress(Exception):
            on_node_error(error.node_id, error)

    def _create_fetch_task(
        self,
        node: "TreeNode[T]",
        clock: GrandClock,
    ) -> "asyncio.Task[FetchResult[T]]":
        """Create a fetch task for a node."""
        return asyncio.create_task(self._fetch_with_retry(node, clock))

    async def _fetch_with_retry(
        self,
        node: "TreeNode[T]",
        clock: GrandClock,
    ) -> "FetchResult[T]":
        """Fetch a node with retry and concurrency control.

        Records the attempts made and the time spent on the node, also when
        the fetch fails or is cancelled.

        Raises:
            FetcherError: If fetch fails after retries.
            asyncio.CancelledError: If traversal is cancelled.

        """
        try:
            async with self._gate.acquire():
                clock.check_cancelled()
                retrying = self._retry_config.as_retrying()
                started = time.monotonic()
                try:
                    return await retrying(self._fetch_once, node, clock)
                finally:
                    node.elapsed_ms = int((time.monotonic() - started) * 1000)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise FetcherError(node.node_id, exc) from exc

    async def _fetch_once(
        self,
        node: "TreeNode[T]",
        clock: GrandClock,
    ) -> "FetchResult[T]":
        """Execute a single fetch attempt, checking cancellation first.

        Args:
            node: Tree node to fetch children for.
            clock: Grand clock used to detect external cancellation.

        Returns:
            Fetch result from the user-provided fetcher.

        Raises:
            NodeTimeoutError: If the attempt exceeds the per-node timeout.

        """
        clock.check_cancelled()
        node.attempts += 1
        deadline = asyncio.timeout(self._node_timeout)
        try:
            async with deadline:
                return await self._fetcher(
                    node.ref or ChildRef(id="root"),
                    node.depth,
                )
        except TimeoutError as exc:
            # A TimeoutError the fetcher raised itself is its own failure.
            if self._node_timeout is None or not deadline.expired():
                raise
            raise NodeTimeoutError(node.node_id, self._node_timeout) from exc
