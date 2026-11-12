from __future__ import annotations

import threading
from pathlib import Path

import pytest

from apb.engine import (
    BlockState,
    EventKind,
    Executor,
    PipelineModel,
    PipelineValidationError,
    RunEvent,
)
from tests.conftest import Scale, Source, TextFile


def chain() -> PipelineModel:
    p = PipelineModel()
    p.add_block("test.source", block_id="src", values=[1, 2, 3])
    p.add_block("test.scale", block_id="scale", factor=10)
    p.add_block("test.total", block_id="total")
    p.connect("src.table", "scale.table")
    p.connect("scale.table", "total.table")
    return p


def test_runs_in_topological_order(registry, tmp_path):
    events: list[RunEvent] = []
    result = Executor(registry, sink=events.append).run(chain(), tmp_path)
    assert result.ok
    assert result.output("total", "value") == 60.0
    started = [e.block_id for e in events if e.kind is EventKind.BLOCK_STARTED]
    assert started == ["src", "scale", "total"]
    assert result.results["src"].log == ["3 values"]


def test_block_cannot_modify_upstream_output(registry, tmp_path):
    result = Executor(registry).run(chain(), tmp_path)
    assert result.output("src", "table")["A"].tolist() == [1, 2, 3]


def test_cache_reuses_unchanged_blocks(registry, tmp_path):
    pipeline = chain()
    executor = Executor(registry)
    executor.run(pipeline, tmp_path)
    second = executor.run(pipeline, tmp_path)
    assert Source.runs == 1 and Scale.runs == 1
    assert all(r.cached for r in second.results.values())

    pipeline.block("scale").params["factor"] = 100
    assert executor.states(pipeline, tmp_path) == {
        "src": BlockState.DONE,
        "scale": BlockState.STALE,
        "total": BlockState.STALE,
    }
    third = executor.run(pipeline, tmp_path)
    assert Source.runs == 1 and Scale.runs == 2
    assert third.output("total", "value") == 600.0


def test_changed_input_file_invalidates_cache(registry, tmp_path):
    (tmp_path / "note.txt").write_text("v1", encoding="utf-8")
    p = PipelineModel()
    p.add_block("test.text_file", block_id="t", file="note.txt")
    executor = Executor(registry)
    assert executor.run(p, tmp_path).output("t", "text") == "v1"
    executor.run(p, tmp_path)
    assert TextFile.runs == 1
    (tmp_path / "note.txt").write_text("v2", encoding="utf-8")
    assert executor.states(p, tmp_path)["t"] is BlockState.STALE
    assert executor.run(p, tmp_path).output("t", "text") == "v2"
    assert TextFile.runs == 2


def test_error_skips_downstream_but_independent_branch_runs(registry, tmp_path):
    p = PipelineModel()
    p.add_block("test.fail", block_id="fail")
    p.add_block("test.total", block_id="after_fail")
    p.add_block("test.source", block_id="src")
    p.add_block("test.total", block_id="ok")
    p.connect("fail.table", "after_fail.table")
    p.connect("src.table", "ok.table")
    result = Executor(registry).run(p, tmp_path)
    assert not result.ok
    assert result.results["fail"].state is BlockState.ERROR
    assert "ValueError: boom" in result.results["fail"].error
    assert "Traceback" in result.results["fail"].traceback
    assert result.results["after_fail"].state is BlockState.SKIPPED
    assert result.results["ok"].state is BlockState.DONE


def test_wrong_output_type_is_an_error(registry, tmp_path):
    p = PipelineModel()
    p.add_block("test.bad_output", block_id="bad")
    result = Executor(registry).run(p, tmp_path)
    assert result.results["bad"].state is BlockState.ERROR
    assert "must be Scalar" in result.results["bad"].error


def test_run_until_runs_only_upstream(registry, tmp_path):
    p = chain()
    p.add_block("test.fail", block_id="unrelated")
    result = Executor(registry).run(p, tmp_path, until="scale")
    assert set(result.results) == {"src", "scale"}
    assert result.ok


def test_validation_errors_stop_the_run(registry, tmp_path):
    p = chain()
    p.block("scale").params["factor"] = "not a number"
    with pytest.raises(PipelineValidationError) as info:
        Executor(registry).run(p, tmp_path)
    assert any("factor" in str(i) for i in info.value.issues)


def test_invalid_block_outside_run_until_is_ignored(registry, tmp_path):
    p = chain()
    p.add_block("test.scale", block_id="broken", factor="x")  # also unconnected
    assert Executor(registry).run(p, tmp_path, until="total").ok


def test_variables_and_overrides(registry, tmp_path):
    p = chain()
    p.variables["k"] = 2.0
    p.block("scale").params["factor"] = "${k}"
    executor = Executor(registry)
    assert executor.run(p, tmp_path).output("total", "value") == 12.0
    assert executor.run(p, tmp_path, overrides={"k": 3}).output("total", "value") == 18.0


def test_multiple_and_optional_inputs(registry, tmp_path):
    p = PipelineModel()
    p.add_block("test.source", block_id="a", values=[1])
    p.add_block("test.source", block_id="b", values=[2])
    p.add_block("test.stack", block_id="s")
    p.connect("a.table", "s.tables")
    p.connect("b.table", "s.tables")
    result = Executor(registry).run(p, tmp_path)
    assert result.output("s", "table")["A"].tolist() == [1, 2]


def test_stop_skips_remaining_blocks(registry, tmp_path):
    executor = Executor(registry)

    def sink(event: RunEvent) -> None:
        if event.kind is EventKind.BLOCK_FINISHED and event.block_id == "src":
            executor.stop()

    executor._sink = sink
    result = executor.run(chain(), tmp_path)
    assert result.cancelled
    assert result.results["scale"].state is BlockState.SKIPPED


def test_sink_exceptions_do_not_break_run(registry, tmp_path):
    def bad_sink(event: RunEvent) -> None:
        raise RuntimeError("gui gone")

    assert Executor(registry, sink=bad_sink).run(chain(), tmp_path).ok


def test_run_in_worker_thread(registry, tmp_path: Path):
    results = []
    thread = threading.Thread(
        target=lambda: results.append(Executor(registry).run(chain(), tmp_path))
    )
    thread.start()
    thread.join(timeout=10)
    assert results and results[0].ok
