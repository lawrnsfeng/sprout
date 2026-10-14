"""Tests for Sprout duplicate child ref detection."""

import asyncio

import pytest

from sprout import (
    ChildRef,
    Executor,
    ExecutorConfig,
    FetchResult,
)

_TOTAL_WITH_DUP = 3
_CHILDREN_WITH_DUP = 2


class SampleItem:
    """Sample item for tree nodes."""

    def __init__(self, name: str) -> None:
        """Initialize SampleItem."""
        self.name = name


@pytest.mark.asyncio
async def test_duplicate_ref_is_skipped_with_warning() -> None:
    """Test fetcher returning same ID twice results in dedup + warning."""

    async def fetcher(ref: ChildRef, depth: int) -> FetchResult[SampleItem]:
        """Return duplicate child refs at root."""
        if depth == 0:
            return FetchResult(
                items=[SampleItem("root-file")],
                child_refs=[
                    ChildRef(id="dup-child"),
                    ChildRef(id="dup-child"),
                    ChildRef(id="unique-child"),
                ],
            )
        return FetchResult(items=[SampleItem(f"file-{ref.id}")])

    config = ExecutorConfig(max_at_once=5, level_timeout=2.0)
    executor = Executor[SampleItem](fetcher=fetcher, config=config)

    tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=2)

    assert report.total_nodes == _TOTAL_WITH_DUP
    assert report.skipped_nodes == 1
    assert len(tree.children) == _CHILDREN_WITH_DUP

    dup_errors = [e for e in report.errors if e.kind == "duplicate_skipped"]
    assert len(dup_errors) == 1
    assert dup_errors[0].node_id == "dup-child"
    assert "already seen" in dup_errors[0].last_exception_repr


@pytest.mark.asyncio
async def test_duplicate_across_branches_is_skipped() -> None:
    """Test duplicate ref across different branches is detected."""

    async def fetcher(ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Two branches both reference the same child."""
        if ref.id == "root":
            return FetchResult(
                child_refs=[ChildRef(id="branch-a"), ChildRef(id="branch-b")],
            )
        if ref.id in ("branch-a", "branch-b"):
            return FetchResult(child_refs=[ChildRef(id="shared-node")])
        return FetchResult(items=[SampleItem(f"item-{ref.id}")])

    config = ExecutorConfig(max_at_once=5, level_timeout=2.0)
    executor = Executor[SampleItem](fetcher=fetcher, config=config)

    _tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=3)

    assert report.skipped_nodes == 1
    dup_errors = [e for e in report.errors if e.kind == "duplicate_skipped"]
    assert len(dup_errors) == 1
    assert dup_errors[0].node_id == "shared-node"


@pytest.mark.asyncio
async def test_no_infinite_loop_with_circular_refs() -> None:
    """Test circular references don't cause infinite loop."""

    async def fetcher(ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Each node references the other (circular)."""
        if ref.id == "root":
            return FetchResult(child_refs=[ChildRef(id="node-a")])
        if ref.id == "node-a":
            return FetchResult(child_refs=[ChildRef(id="node-b")])
        if ref.id == "node-b":
            return FetchResult(child_refs=[ChildRef(id="root")])
        return FetchResult()

    config = ExecutorConfig(max_at_once=5, level_timeout=2.0)
    executor = Executor[SampleItem](fetcher=fetcher, config=config)

    _tree, report = await executor.expand_bounded(
        ChildRef(id="root"),
        depth=10,
    )

    assert report.skipped_nodes == 1
    assert report.total_nodes == _TOTAL_WITH_DUP


async def _diamond(ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
    """Two branches both reference the same child."""
    if ref.id == "root":
        return FetchResult(
            child_refs=[ChildRef(id="branch-a"), ChildRef(id="branch-b")],
        )
    if ref.id in ("branch-a", "branch-b"):
        return FetchResult(child_refs=[ChildRef(id="shared-node")])
    return FetchResult(items=[SampleItem(f"item-{ref.id}")])


@pytest.mark.asyncio
async def test_allow_fetches_shared_ref_under_each_parent() -> None:
    """Test on_duplicate='allow' fetches a shared ref once per parent."""
    executor = Executor[SampleItem](fetcher=_diamond, on_duplicate="allow")

    tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=3)

    assert report.skipped_nodes == 0
    assert report.errors == []
    shared = [n for n in tree.subtree if n.ref and n.ref.id == "shared-node"]
    assert sorted(n.path for n in shared) == [
        ["root", "branch-a", "shared-node"],
        ["root", "branch-b", "shared-node"],
    ]
    assert all(n.result is not None for n in shared)


@pytest.mark.asyncio
async def test_allow_fetches_sibling_duplicates() -> None:
    """Test on_duplicate='allow' fetches the same ref twice under one parent."""

    async def fetcher(_ref: ChildRef, depth: int) -> FetchResult[SampleItem]:
        """Return the same child ref twice at root."""
        if depth == 0:
            return FetchResult(
                child_refs=[ChildRef(id="dup-child"), ChildRef(id="dup-child")],
            )
        return FetchResult()

    executor = Executor[SampleItem](fetcher=fetcher, on_duplicate="allow")

    tree, report = await executor.expand_bounded(ChildRef(id="root"), depth=2)

    assert report.total_nodes == _TOTAL_WITH_DUP
    assert report.skipped_nodes == 0
    assert len(tree.children) == _CHILDREN_WITH_DUP
    assert [c.path for c in tree.children] == [["root", "dup-child"]] * 2


@pytest.mark.asyncio
async def test_allow_still_breaks_cycles() -> None:
    """Test on_duplicate='allow' skips a ref that is its own ancestor."""

    async def fetcher(ref: ChildRef, _depth: int) -> FetchResult[SampleItem]:
        """Node-b points back at the root and at itself."""
        if ref.id == "root":
            return FetchResult(child_refs=[ChildRef(id="node-a")])
        if ref.id == "node-a":
            return FetchResult(child_refs=[ChildRef(id="node-b")])
        return FetchResult(
            child_refs=[ChildRef(id="root"), ChildRef(id="node-b")],
        )

    config = ExecutorConfig(level_timeout=2.0)
    executor = Executor[SampleItem](
        fetcher=fetcher,
        config=config,
        on_duplicate="allow",
    )

    _tree, report = await executor.expand_unbounded(ChildRef(id="root"))

    assert report.timed_out is False
    assert report.total_nodes == _TOTAL_WITH_DUP
    assert [(e.kind, e.path) for e in report.errors] == [
        ("duplicate_skipped", ["root", "node-a", "node-b", "root"]),
        ("duplicate_skipped", ["root", "node-a", "node-b", "node-b"]),
    ]


@pytest.mark.asyncio
async def test_concurrent_traversals_on_one_executor_are_isolated() -> None:
    """Test two traversals at once keep their own dedup state."""

    async def fetcher(ref: ChildRef, depth: int) -> FetchResult[SampleItem]:
        """Each root returns the shared child and a ref back to itself."""
        await asyncio.sleep(0.01)
        if depth == 0:
            return FetchResult(
                child_refs=[ChildRef(id="shared"), ChildRef(id=ref.id)],
            )
        return FetchResult(items=[SampleItem(ref.id)])

    executor = Executor[SampleItem](fetcher=fetcher)

    results = await asyncio.gather(
        executor.expand_bounded(ChildRef(id="root-a"), depth=2),
        executor.expand_bounded(ChildRef(id="root-b"), depth=2),
    )

    for root_id, (tree, report) in zip(("root-a", "root-b"), results, strict=True):
        assert report.total_nodes == _CHILDREN_WITH_DUP
        assert [n.node_id for n in tree.subtree] == [root_id, "shared"]
        assert [(e.kind, e.node_id) for e in report.errors] == [
            ("duplicate_skipped", root_id),
        ]
