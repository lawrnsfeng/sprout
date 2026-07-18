"""Data models for Sprout traversal."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


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
        kind: Type of error that occurred.
        attempts: Number of retry attempts made.
        last_exception_type: Type name of the last exception.
        last_exception_repr: String representation of the last exception.
        node_id: ID of the node that failed.
        traceback: Truncated traceback string (optional).
        path: Full ancestor path from root to this node.

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
        }


@dataclass
class TraversalReport:
    """Summary of a tree traversal operation.

    Attributes:
        total_nodes: Total number of nodes successfully visited.
        failed_nodes: Number of nodes that failed to fetch.
        cancelled_nodes: Number of nodes cancelled due to timeout.
        skipped_nodes: Number of duplicate nodes skipped.
        max_depth_seen: Maximum depth reached during traversal.
        elapsed_ms: Total elapsed time in milliseconds.
        timed_out: True if grand timeout was exceeded.
        errors: List of all errors encountered.

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

    """

    ref: ChildRef | None = None
    result: "FetchResult[T] | None" = None
    children: "list[TreeNode[T]]" = field(default_factory=list)
    error: NodeError | None = None
    partial: bool = False
    depth: int = 0

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
            "node_id": self.ref.id if self.ref else "root",
            "depth": self.depth,
            "status": status,
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
