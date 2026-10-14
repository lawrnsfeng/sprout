"""Sprout executor for recursive traversal."""

import asyncio
import contextlib
import traceback
from dataclasses import dataclass
from typing import cast

from .clock import DynamicClock, GrandClock, StaticClock
from .concurrency import ConcurrencyGate
from .config import ExecutorConfig
from .exceptions import (
    FetcherError,
    InvalidParameterError,
    TraversalAbortedError,
)
from .models import (
    ChildRef,
    FetchResult,
    TraversalReport,
    TreeNode,
)
from .retry import RetryConfig
from .typedefs import Fetcher, OnNodeError, ProgressHook


def _truncate_traceback(exc: Exception, *, max_lines: int = 20) -> str:
    """Extract and truncate exception traceback.

    Args:
        exc: Exception to extract traceback from.
        max_lines: Maximum lines to keep.

    Returns:
        Truncated traceback string.

    """
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    if len(tb_lines) <= max_lines:
        return "".join(tb_lines)
    return "".join(tb_lines[:max_lines]) + "\n... (truncated)"


@dataclass
class _SpawnContext[T]:
    """Context bundle passed to child-spawning logic.

    Attributes:
        report: Mutable traversal report accumulating stats.
        live_tasks: Set of currently running asyncio tasks.
        node_map: Mapping from task to tree node.
        max_depth: Maximum traversal depth (None = unbounded).
        clock: Grand timeout clock.

    """

    report: TraversalReport
    live_tasks: set[asyncio.Task[FetchResult[T]]]
    node_map: dict[asyncio.Task[FetchResult[T]], "TreeNode[T]"]
    max_depth: int | None
    clock: GrandClock


class Executor[T]:
    """Executor for async recursive tree traversal.

    Orchestrates recursive fetching with retry, concurrency control,
    grand timeout, configurable error handling, deduplication, and
    full ancestor path tracking.
    """

    def __init__(
        self,
        fetcher: Fetcher[T],
        config: ExecutorConfig | None = None,
        *,
        on_node_error: OnNodeError = "skip_mark",
        retry_config: RetryConfig | None = None,
        progress_hook: ProgressHook | None = None,
    ) -> None:
        """Initialize Executor.

        Args:
            fetcher: Async callable that fetches children for a node.
            config: Executor tuning (defaults when omitted).
            on_node_error: Error handling policy.
            retry_config: Override retry configuration (uses config values
                if None).
            progress_hook: Optional hook called on child discovery and
                leaf completion.

        """
        cfg = config or ExecutorConfig()
        self._fetcher = fetcher
        self._retry_config = retry_config or RetryConfig(
            max_attempts=cfg.retry_max_attempts,
        )
        self._gate = ConcurrencyGate(cfg.max_at_once, cfg.max_per_second)
        self._level_timeout = cfg.level_timeout
        self._on_node_error = on_node_error
        self._progress_hook = progress_hook
        self._parent_map: dict[str, str | None] = {}
        self._seen_refs: set[str] = set()

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

        self._reset_state()
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
        self._reset_state()
        clock = DynamicClock(
            base_seconds=self._level_timeout,
            per_level_seconds=self._level_timeout,
        )
        return await self._execute(root_ref, None, clock)

    def _reset_state(self) -> None:
        """Reset per-run mutable state."""
        self._parent_map = {}
        self._seen_refs = set()

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

        self._parent_map[root_ref.id] = None
        self._seen_refs.add(root_ref.id)

        timeout_task = asyncio.create_task(clock.wait_for_timeout())

        ctx: _SpawnContext[T] = _SpawnContext(
            report=report,
            live_tasks=live_tasks,
            node_map=node_map,
            max_depth=max_depth,
            clock=clock,
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
                        node.mark_cancelled(report, path=self._build_path(node))
                        continue

                    try:
                        fetch_result = fetch_task.result()
                    except FetcherError as fetcher_err:
                        await self._handle_fetch_error(
                            node,
                            fetcher_err.original_error,
                            report,
                            live_tasks,
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
            timeout_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timeout_task

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
            node.mark_cancelled(report, path=self._build_path(node))
        live_tasks.clear()

    async def _handle_fetch_error(
        self,
        node: "TreeNode[T]",
        exc: Exception,
        report: TraversalReport,
        live_tasks: set[asyncio.Task[FetchResult[T]]],
    ) -> None:
        """Handle fetch error — skip_mark or abort depending on policy.

        Raises:
            TraversalAbortedError: If on_node_error="abort".

        """
        node_error = node.mark_failed(
            exc,
            report,
            attempts=self._retry_config.max_attempts,
            traceback=_truncate_traceback(exc),
            path=self._build_path(node),
        )

        if self._on_node_error == "abort":
            for task in live_tasks:
                task.cancel()
            await asyncio.gather(*live_tasks, return_exceptions=True)
            live_tasks.clear()
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

        parent_id = node.ref.id if node.ref else "root"

        if not fetch_result.child_refs:
            self._hook_node_complete(parent_id)
            return

        child_depth = node.depth + 1
        if ctx.max_depth is not None and child_depth >= ctx.max_depth:
            self._hook_node_complete(parent_id)
            return

        spawned_ids: list[str] = []
        for child_ref in fetch_result.child_refs:
            if child_ref.id in self._seen_refs:
                node.record_duplicate_child(
                    child_ref,
                    ctx.report,
                    path=self._build_path(node),
                )
                continue
            self._seen_refs.add(child_ref.id)
            self._parent_map[child_ref.id] = parent_id

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

        Raises:
            FetcherError: If fetch fails after retries.
            asyncio.CancelledError: If traversal is cancelled.

        """
        node_id = node.ref.id if node.ref else "root"

        try:
            async with self._gate.acquire():
                clock.check_cancelled()
                retrying = self._retry_config.as_retrying()
                return await retrying(self._fetch_once, node, clock)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise FetcherError(node_id, exc) from exc

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

        """
        clock.check_cancelled()
        return await self._fetcher(
            node.ref or ChildRef(id="root"),
            node.depth,
        )

    def _build_path(self, node: "TreeNode[T]") -> list[str]:
        """Build full ancestor path from root to this node.

        Args:
            node: Node to build path for.

        Returns:
            List of node IDs from root to this node.

        """
        path: list[str] = []
        node_id: str | None = node.ref.id if node.ref else "root"
        while node_id is not None:
            path.insert(0, node_id)
            node_id = self._parent_map.get(node_id)
        return path
