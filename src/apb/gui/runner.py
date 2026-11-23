"""Running pipelines from the GUI (GUI-08, GUI-09, spec 06 §3.3).

The engine runs in a worker thread so the page stays responsive. Engine events are put
into a thread-safe queue and handed to the page by :meth:`RunController.drain`, which the
page calls from a timer in its own (event loop) context.
"""

from __future__ import annotations

import queue
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any, Literal

from nicegui import run

from apb.engine.events import BlockState, EventKind, RunEvent
from apb.engine.executor import BlockResult, Executor, PipelineValidationError, RunResult
from apb.engine.graph import ValidationIssue, prepare
from apb.engine.store import OutputStore, StoredEntry
from apb.gui.editor import EditorState

#: Notification types of NiceGUI used for run summaries.
type NotifyType = Literal["positive", "negative", "warning"]

#: Run event -> state shown on the canvas.
EVENT_STATES: dict[EventKind, BlockState] = {
    EventKind.BLOCK_STARTED: BlockState.RUNNING,
    EventKind.BLOCK_FINISHED: BlockState.DONE,
    EventKind.BLOCK_CACHED: BlockState.DONE,
    EventKind.BLOCK_FAILED: BlockState.ERROR,
    EventKind.BLOCK_SKIPPED: BlockState.SKIPPED,
}


class RunController:
    """Runs the editor's pipeline and keeps the latest result of every block.

    Args:
        state: The editor state.
        on_event: Called (in the page context) for every engine event.
    """

    def __init__(self, state: EditorState, on_event: Callable[[RunEvent], None]) -> None:
        self.state = state
        self.on_event = on_event
        self._queue: queue.Queue[RunEvent] = queue.Queue()
        self.executor = Executor(state.registry, sink=self._queue.put)
        self.results: dict[str, BlockResult] = {}
        self.running = False
        self.edited: set[str] = set()

    def drain(self) -> None:
        """Deliver queued engine events to the page (call from a timer)."""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                return
            self.on_event(event)

    async def run(self, until: str | None = None) -> RunResult | list[ValidationIssue]:
        """Run the pipeline (or up to block ``until``) in a worker thread.

        Returns:
            The run result, or the validation issues that prevented the run.
        """
        if self.running:
            return []
        self.running = True
        snapshot = self.state.pipeline.model_copy(deep=True)
        try:
            result: RunResult = await run.io_bound(
                self.executor.run,
                snapshot,
                self.state.base_dir,
                until=until,
                pipeline_file=self.state.path,
            )
        except PipelineValidationError as exc:
            return exc.issues
        finally:
            self.running = False
            self.drain()
        self.results.update(result.results)
        self.edited.clear()
        return result

    def stop(self) -> None:
        """Stop after the current block."""
        self.executor.stop()

    # ---- stored outputs (spec 08) ------------------------------------------------------

    def attach_store(self, pipeline_file: Path | None) -> None:
        """Use the store of a pipeline file (``None`` = unsaved pipeline: memory only)."""
        self.executor.attach_store(
            OutputStore.for_pipeline(pipeline_file) if pipeline_file is not None else None
        )

    def restore(self) -> int:
        """Show the stored results of the pipeline's blocks; returns how many there are."""
        restored = self.executor.restore(self.state.pipeline)
        self.results.update(restored)
        return len(restored)

    def persist(self) -> int:
        """Write the up-to-date outputs to the store (after Save); returns how many."""
        return self.executor.persist(self.state.pipeline, self.state.base_dir)

    def downstream(self, block_id: str) -> set[str]:
        """Return the blocks that depend on a block."""
        return prepare(self.state.pipeline, self.state.registry).downstream(block_id)

    def clear(self, block_ids: Collection[str] | None = None) -> list[str]:
        """Clear stored and in-memory outputs: everything, or blocks and what is downstream.

        Returns:
            The IDs of the blocks that had outputs.

        Raises:
            StoreError: If stored data cannot be removed.
        """
        cleared = self.executor.clear_outputs(self.state.pipeline, block_ids)
        for block_id in cleared:
            self.results.pop(block_id, None)
            self.edited.discard(block_id)
        return cleared

    def warnings(self, block_id: str) -> list[str]:
        """Return the warnings of a block's latest result (``WARNING:`` log lines, GUI-35).

        Args:
            block_id: The block.

        Returns:
            The warning texts without the ``WARNING:`` prefix (empty if none or not run).
        """
        result = self.results.get(block_id)
        if result is None:
            return []
        return [
            line.removeprefix("WARNING:").strip()
            for line in result.log
            if line.startswith("WARNING:")
        ]

    def warning_blocks(self, states: dict[str, str]) -> list[str]:
        """Return the blocks whose latest result has a warning in its log (``WARNING:`` line).

        Args:
            states: The displayed states; blocks that are idle have no result to warn about.
        """
        return [
            block_id
            for block_id, result in self.results.items()
            if states.get(block_id) in ("done", "stale")
            and any(line.startswith("WARNING:") for line in result.log)
        ]

    def stored_entry(self, block_id: str) -> StoredEntry | None:
        """Return the stored entry of a block, if any."""
        return self.executor.stored_entry(block_id)

    def mark_edited(self, block_id: str) -> None:
        """Remember that a block changed since the last run."""
        self.edited.add(block_id)

    def display_states(self) -> dict[str, str]:
        """States to show on the canvas when no run is active (GUI-09).

        Cached, up-to-date blocks are ``done``; blocks whose inputs or settings changed are
        ``stale``; errors and skips of the last run stay visible until the block or its
        inputs are edited.
        """
        try:
            computed = self.executor.states(self.state.pipeline, self.state.base_dir)
        except Exception:  # an invalid pipeline must not break the editor
            computed = {}
        shown: dict[str, str] = {}
        for block in self.state.pipeline.blocks:
            state = computed.get(block.id, BlockState.IDLE)
            last = self.results.get(block.id)
            if (
                state is BlockState.IDLE
                and last is not None
                and last.state in (BlockState.ERROR, BlockState.SKIPPED)
                and block.id not in self.edited
            ):
                state = last.state
            shown[block.id] = str(state)
        return shown

    def result(self, block_id: str) -> BlockResult | None:
        """Latest result of a block, if it has run in this session."""
        return self.results.get(block_id)


def summary(result: RunResult) -> tuple[str, NotifyType]:
    """Return a one-line summary and a notification type for a run."""
    counts: dict[BlockState, int] = {}
    for r in result.results.values():
        counts[r.state] = counts.get(r.state, 0) + 1
    done, errors = counts.get(BlockState.DONE, 0), counts.get(BlockState.ERROR, 0)
    up_to_date = sum(r.cached for r in result.results.values())
    skipped = counts.get(BlockState.SKIPPED, 0)
    if result.cancelled:
        return f"Run stopped: {done} done, {skipped} not run", "warning"
    if errors:
        return f"Run finished with {errors} error(s): {done} done, {skipped} skipped", "negative"
    extra = f" ({up_to_date} up to date)" if up_to_date else ""
    return f"Run finished: {done} block(s) done{extra}", "positive"


def describe_value(value: Any) -> str:
    """Short description of an output value for lists."""
    import pandas as pd

    if isinstance(value, pd.DataFrame):
        return f"table, {len(value)} rows x {len(value.columns)} columns"
    if hasattr(value, "savefig"):
        return "figure"
    return type(value).__name__
