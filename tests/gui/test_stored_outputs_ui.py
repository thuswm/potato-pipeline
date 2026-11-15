"""Stored outputs in the GUI: labels and messages (spec 08 §3.9)."""

from __future__ import annotations

from apb.engine.events import BlockState
from apb.engine.executor import BlockResult, RunResult
from apb.gui.preview import result_origin, stored_time
from apb.gui.runner import summary

WHEN = "2026-11-15T17:08:12+01:00"


def test_stored_time():
    assert stored_time(WHEN) == "2026-11-15 17:08"
    assert stored_time("") == ""


def test_result_origin_says_where_a_result_comes_from():
    fresh = BlockResult(BlockState.DONE, duration=0.1949, finished=WHEN)
    assert result_origin(fresh) == "Done in 0.19 s"
    assert result_origin(BlockResult(BlockState.DONE, cached=True)) == "Done (cached)"
    restored = BlockResult(BlockState.DONE, cached=True, restored=True, finished=WHEN)
    assert result_origin(restored) == "Done · stored 2026-11-15 17:08"
    assert result_origin(restored, outdated=True) == (
        "Outdated · stored 2026-11-15 17:08 — parameters or inputs changed since"
    )
    assert result_origin(fresh, outdated=True) == (
        "Outdated · made 2026-11-15 17:08 — parameters or inputs changed since"
    )
    assert result_origin(BlockResult(BlockState.DONE), outdated=True).startswith(
        "Outdated · earlier"
    )
    assert result_origin(BlockResult(BlockState.ERROR)) == "Error"
    assert result_origin(BlockResult(BlockState.SKIPPED)) == "Skipped"


def test_summary_counts_blocks_taken_from_the_store():
    ran = BlockResult(BlockState.DONE)
    stored = BlockResult(BlockState.DONE, cached=True, restored=True)
    assert summary(RunResult({"a": ran, "b": ran})) == ("Run finished: 2 block(s) done", "positive")
    assert summary(RunResult({"a": ran, "b": stored, "c": stored})) == (
        "Run finished: 3 block(s) done (2 up to date)",
        "positive",
    )
