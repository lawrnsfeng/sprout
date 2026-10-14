"""Data models for Sprout traversal."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .exceptions import NodeTimeoutError

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class ChildRef:
    """Reference to a child node to be recursively fetched.

    Attributes:
        id: Unique identifier for the child node.
        payload: Caller-defined data attached to this reference.

    """

    id: str
    payload: Any = None


@dataclass(frozen=True)
class FetchResult[T]:
    """Result from fetching a node's children.

    Attributes:
        items: List of leaf items (files, messages, etc.) at this node.
        child_refs: List of child references to recurse into.
        extra: Optional caller-defined metadata.

    """

    items: list[T] = field(default_factory=list)
    child_refs: list[ChildRef] = field(default_factory=list)
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class NodeError:
    """Error information for a failed or skipped node.

    Attributes:
        kind: Type of error that occurred. ``timeout`` is a fetch that
            exceeded the per-node timeout. ``cancelled`` is a fetch that
            ended cancelled: by the grand timeout when
            ``TraversalReport.timed_out`` is set, otherwise from inside the
            fetcher, for example by a task it was awaiting.
        attempts: Number of fetch attempts made.
        last_exception_type: Type name of the last exception.
        last_exception_repr: String representation of the last exception.
        node_id: ID of the node that failed.
        traceback: Truncated traceback string (optional).
        path: Full ancestor path from root to this node.
        elapsed_ms: Time spent fetching this node, retries included, in
            milliseconds (0 if the fetch never started).

    """

    kind: Literal[
        "fetch_failed",
        "timeout",
        "cancelled",
        "aborted",
        "duplicate_skipped",
    ]
    attempts: int
    last_exception_type: str
    last_exception_repr: str
    node_id: str
    traceback: str | None = None
    path: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def as_dict(self) -> dict[str, object]:
        """Return a dict representation of this error, suitable for JSON."""
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "attempts": self.attempts,
            "last_exception_type": self.last_exception_type,
            "last_exception_repr": self.last_exception_repr,
            "traceback": self.traceback,
            "path": self.path,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class TraversalReport:
    """Summary of a tree traversal operation.

    Attributes:
        total_nodes: Total number of nodes successfully visited.
        failed_nodes: Number of nodes that failed to fetch.
        cancelled_nodes: Number of nodes whose fetch ended cancelled, by the
            grand timeout or from inside the fetcher.
        skipped_nodes: Number of duplicate nodes skipped.
        max_depth_seen: Maximum depth reached during traversal.
        elapsed_ms: Total elapsed time in milliseconds.
        timed_out: True if the grand timeout cut unfinished work short.
        errors: List of all errors encountered: failed, timed-out,
            cancelled and skipped nodes.

    """

    total_nodes: int = 0
    failed_nodes: int = 0
    cancelled_nodes: int = 0
    skipped_nodes: int = 0
    max_depth_seen: int = 0
    elapsed_ms: int = 0
    timed_out: bool = False
    errors: list[NodeError] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        """Return True if any node failed, timed out, was cancelled or skipped.

        It does not cover depth: ``expand_bounded`` neither fetches nor
        reports the refs below its depth limit.
        """
        return bool(self.errors)

    @property
    def as_dict(self) -> dict[str, object]:
        """Return a dict representation of this report, suitable for JSON."""
        return {
            "total_nodes": self.total_nodes,
            "failed_nodes": self.failed_nodes,
            "cancelled_nodes": self.cancelled_nodes,
            "skipped_nodes": self.skipped_nodes,
            "max_depth_seen": self.max_depth_seen,
            "elapsed_ms": self.elapsed_ms,
            "timed_out": self.timed_out,
        }


@dataclass
class TreeNode[T]:
    """A node in the fetched tree.

    Attributes:
        ref: Reference to this node (None for synthetic root).
        result: Fetch result for this node (None if failed/cancelled).
        children: List of child TreeNodes.
        error: Error info if this node failed (None on success).
        partial: True if this subtree was cancelled mid-traversal.
        depth: Depth of this node in the tree (0 for root).
        attempts: Number of fetch attempts made for this node.
        elapsed_ms: Time spent fetching this node, retries included, in
            milliseconds (0 if the fetch never started).

    """

    ref: ChildRef | None = None
    result: "FetchResult[T] | None" = None
    children: "list[TreeNode[T]]" = field(default_factory=list)
    error: NodeError | None = None
    partial: bool = False
    depth: int = 0
    attempts: int = 0
    elapsed_ms: int = 0

    def __post_init__(self) -> None:
        """Start without a parent and adopt parentless children passed in.

        A child that already has a parent keeps it, so a shallow copy made
        with ``dataclasses.replace`` does not take the children away from
        the tree they belong to.

        The back-reference is not a dataclass field, so field-walking
        helpers such as ``dataclasses.asdict`` do not follow it in a loop.
        """
        self._parent: TreeNode[T] | None = None
        for child in self.children:
            if child.parent is None:
                child._parent = self  # noqa: SLF001 - another instance of this class

    @property
    def parent(self) -> "TreeNode[T] | None":
        """Return the node this one was spawned from (None for root)."""
        return self._parent

    @property
    def node_id(self) -> str:
        """Return this node's ID (``"root"`` for a node without a ref)."""
        return self.ref.id if self.ref else "root"

    @property
    def path(self) -> list[str]:
        """Return node IDs from the root down to this node."""
        path: list[str] = []
        node: TreeNode[T] | None = self
        while node is not None:
            path.append(node.node_id)
            node = node.parent
        path.reverse()
        return path

    @property
    def subtree(self) -> "Iterator[TreeNode[T]]":
        """Yield this node, then every descendant, depth-first pre-order."""
        stack: list[TreeNode[T]] = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    @property
    def outcomes(self) -> "dict[str, FetchResult[T] | NodeError]":
        """Map each node ID in this subtree to its fetch result or its error.

        ``outcomes.get(ref_id)`` is the result of a ref that was fetched, the
        error of one that failed, timed out or was cancelled, and None for an
        ID that is not in the subtree.

        A node skipped as a duplicate is left out, so its ID maps to the
        outcome of the fetch that did happen. Where one ID was fetched more
        than once (``on_duplicate="allow"``), the first in pre-order wins.
        """
        outcomes: dict[str, FetchResult[T] | NodeError] = {}
        for node in self.subtree:
            if node.error is not None and node.error.kind == "duplicate_skipped":
                continue
            outcome = node.error if node.error is not None else node.result
            if outcome is not None:
                outcomes.setdefault(node.node_id, outcome)
        return outcomes

    def mark_fetched(
        self,
        fetch_result: "FetchResult[T]",
        report: TraversalReport,
    ) -> None:
        """Attach a fetch result to this node and update report stats."""
        self.result = fetch_result
        report.total_nodes += 1
        report.max_depth_seen = max(report.max_depth_seen, self.depth)

    def spawn_child(self, ref: ChildRef) -> "TreeNode[T]":
        """Create a child node at this node's depth + 1 and attach it."""
        child: TreeNode[T] = TreeNode(ref=ref, depth=self.depth + 1)
        child._parent = self
        self.children.append(child)
        return child

    def mark_cancelled(self, report: TraversalReport) -> NodeError:
        """Mark this node as partially cancelled and record it on report."""
        self.partial = True
        self.error = NodeError(
            kind="cancelled",
            attempts=self.attempts,
            last_exception_type="CancelledError",
            last_exception_repr="Task cancelled",
            node_id=self.node_id,
            path=self.path,
            elapsed_ms=self.elapsed_ms,
        )
        report.cancelled_nodes += 1
        report.errors.append(self.error)
        return self.error

    def mark_failed(
        self,
        exc: Exception,
        report: TraversalReport,
        *,
        traceback: str | None,
    ) -> NodeError:
        """Attach failure info to this node and record it on the report."""
        node_error = NodeError(
            kind="timeout" if isinstance(exc, NodeTimeoutError) else "fetch_failed",
            attempts=self.attempts,
            last_exception_type=type(exc).__name__,
            last_exception_repr=repr(exc),
            traceback=traceback,
            node_id=self.node_id,
            path=self.path,
            elapsed_ms=self.elapsed_ms,
        )
        self.error = node_error
        report.failed_nodes += 1
        report.errors.append(node_error)
        return node_error

    def record_duplicate_child(
        self,
        child_ref: ChildRef,
        report: TraversalReport,
        *,
        path: list[str],
    ) -> NodeError:
        """Record a skipped duplicate child ref as an errored child node.

        The child keeps the skipped ref, payload included, and is never
        fetched, so it has an error and no result.
        """
        node_error = NodeError(
            kind="duplicate_skipped",
            attempts=0,
            last_exception_type="DuplicateRef",
            last_exception_repr=(f"Ref '{child_ref.id}' already seen, skipped"),
            node_id=child_ref.id,
            path=[*path, child_ref.id],
        )
        self.spawn_child(child_ref).error = node_error
        report.skipped_nodes += 1
        report.errors.append(node_error)
        return node_error

    @property
    def as_dict(self) -> dict[str, object]:
        """Recursively return a dict representation of this subtree."""
        status = "success"
        if self.error is not None:
            status = self.error.kind
        elif self.partial:
            status = "partial"
        elif self.result is None:
            status = "pending"

        result: dict[str, object] = {
            "node_id": self.node_id,
            "depth": self.depth,
            "status": status,
            "attempts": self.attempts,
            "elapsed_ms": self.elapsed_ms,
            "items_count": len(self.result.items) if self.result else 0,
            "children": [child.as_dict for child in self.children],
        }

        if self.error is not None:
            result["error"] = self.error.as_dict

        return result

    def dump_execution_graph(
        self,
        report: TraversalReport,
        path: Path,
    ) -> None:
        """Dump this subtree's execution graph as JSON for debugging.

        Produces a JSON file with:
        - ``tree``: nested tree structure mirroring runtime hierarchy
        - ``report``: summary statistics
        - ``errors``: flat array of all errors for quick scanning

        Args:
            report: TraversalReport from the traversal.
            path: File path to write the JSON output to.

        """
        output: dict[str, object] = {
            "tree": self.as_dict,
            "report": report.as_dict,
            "errors": [e.as_dict for e in report.errors],
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(output, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
