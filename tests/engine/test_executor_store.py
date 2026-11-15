"""The executor with stored outputs: a pipeline reopened in a new session (spec 08)."""

from __future__ import annotations

import os

import pandas as pd
import pytest

import apb.engine.store as store_module
from apb.engine import BlockState, EventKind, Executor, PipelineModel, RunEvent
from apb.engine.store import OutputStore, StoredOutputs
from tests.conftest import Scale, Source, TextFile


def chain(factor: float = 10, values=(1, 2, 3)) -> PipelineModel:
    p = PipelineModel()
    p.add_block("test.source", block_id="src", values=list(values))
    p.add_block("test.scale", block_id="scale", factor=factor)
    p.add_block("test.total", block_id="total")
    p.connect("src.table", "scale.table")
    p.connect("scale.table", "total.table")
    return p


def new_session(registry, tmp_path, **kwargs) -> Executor:
    """A new executor on the store of ``tmp_path``: what a reopened pipeline starts with."""
    return Executor(registry, store=OutputStore(tmp_path / ".apb" / "p"), **kwargs)


def states(executor: Executor, pipeline: PipelineModel, tmp_path) -> dict[str, str]:
    return {b: str(s) for b, s in executor.states(pipeline, tmp_path).items()}


def test_every_finished_block_is_stored(registry, tmp_path):
    new_session(registry, tmp_path).run(chain(), tmp_path)
    entries, problems = OutputStore(tmp_path / ".apb" / "p").entries()
    assert sorted(entries) == ["scale", "src", "total"] and problems == {}
    assert entries["src"].log == ("3 values",) and entries["src"].usable
    assert entries["total"].outputs["value"]["value"] == 60.0


def test_a_reopened_pipeline_is_done_without_running(registry, tmp_path):
    pipeline = chain()
    new_session(registry, tmp_path).run(pipeline, tmp_path)
    Source.runs = Scale.runs = 0

    session = new_session(registry, tmp_path)  # the tool was closed and started again
    assert states(session, pipeline, tmp_path) == {
        "src": "done",
        "scale": "done",
        "total": "done",
    }
    assert Source.runs == Scale.runs == 0
    restored = session.restore(pipeline)
    assert set(restored) == {"src", "scale", "total"}
    result = restored["total"]
    assert result.restored and result.cached and result.state is BlockState.DONE
    assert result.finished[:2] == "20" and result.duration >= 0
    assert restored["src"].log == ["3 values"]
    assert isinstance(restored["scale"].outputs, StoredOutputs)
    assert restored["scale"].outputs["table"]["A"].tolist() == [10, 20, 30]
    assert restored["total"].outputs["value"] == 60.0
    assert Source.runs == Scale.runs == 0


def test_run_takes_up_to_date_blocks_from_the_store(registry, tmp_path):
    pipeline = chain()
    first = new_session(registry, tmp_path).run(pipeline, tmp_path)
    Source.runs = Scale.runs = 0
    events: list[RunEvent] = []
    session = Executor(registry, sink=events.append, store=OutputStore(tmp_path / ".apb" / "p"))
    second = session.run(pipeline, tmp_path)
    assert second.ok and Source.runs == Scale.runs == 0
    assert all(r.cached and r.restored for r in second.results.values())
    assert second.output("total", "value") == first.output("total", "value") == 60.0
    assert [e.kind for e in events if e.block_id == "total"] == [EventKind.BLOCK_CACHED]
    assert "stored 20" in next(e.message for e in events if e.kind is EventKind.BLOCK_CACHED)
    assert second.results["src"].log == ["3 values"]


def test_changes_make_the_right_blocks_stale(registry, tmp_path):
    new_session(registry, tmp_path).run(chain(), tmp_path)
    session = new_session(registry, tmp_path)
    assert states(session, chain(factor=3), tmp_path) == {
        "src": "done",
        "scale": "stale",  # its own parameter changed
        "total": "stale",  # its upstream changed
    }
    assert states(session, chain(values=(1, 2)), tmp_path) == dict.fromkeys(
        ("src", "scale", "total"), "stale"
    )
    Source.runs = Scale.runs = 0
    result = session.run(chain(factor=3), tmp_path)
    assert (Source.runs, Scale.runs) == (0, 1)
    assert result.results["src"].restored and not result.results["scale"].cached
    assert result.output("total", "value") == 18.0
    # The new results replaced the old ones in the store.
    assert states(new_session(registry, tmp_path), chain(factor=3), tmp_path)["total"] == "done"
    assert states(new_session(registry, tmp_path), chain(), tmp_path)["total"] == "stale"


def test_a_changed_input_file_makes_the_block_stale(registry, tmp_path):
    (tmp_path / "in.txt").write_text("one", encoding="utf-8")
    p = PipelineModel()
    p.add_block("test.text_file", block_id="txt", file="in.txt")
    new_session(registry, tmp_path).run(p, tmp_path)
    TextFile.runs = 0
    assert states(new_session(registry, tmp_path), p, tmp_path) == {"txt": "done"}
    (tmp_path / "in.txt").write_text("two", encoding="utf-8")
    session = new_session(registry, tmp_path)
    assert states(session, p, tmp_path) == {"txt": "stale"}
    assert session.run(p, tmp_path).output("txt", "text") == "two" and TextFile.runs == 1


def test_stale_entries_can_be_shown_but_are_not_used_by_a_run(registry, tmp_path):
    new_session(registry, tmp_path).run(chain(), tmp_path)
    session = new_session(registry, tmp_path)
    assert "scale" in session.restore(chain(factor=3))  # shown (marked outdated in the GUI)
    Scale.runs = 0
    assert session.run(chain(factor=3), tmp_path).output("scale", "table")["A"].tolist() == [
        3,
        6,
        9,
    ]
    assert Scale.runs == 1


def test_no_cache_recomputes_everything_and_writes_the_new_results(registry, tmp_path):
    pipeline = chain()
    new_session(registry, tmp_path).run(pipeline, tmp_path)
    Source.runs = Scale.runs = 0
    result = new_session(registry, tmp_path, use_stored=False).run(pipeline, tmp_path)
    assert Source.runs == Scale.runs == 1 and not any(r.cached for r in result.results.values())
    assert states(new_session(registry, tmp_path), pipeline, tmp_path)["total"] == "done"


def test_a_failed_block_loses_its_stored_output(registry, tmp_path):
    p = PipelineModel()
    p.add_block("test.fail", block_id="fail")
    p.add_block("test.source", block_id="src")
    session = new_session(registry, tmp_path)
    assert not session.run(p, tmp_path).ok
    assert sorted(OutputStore(tmp_path / ".apb" / "p").entries()[0]) == ["src"]
    # A block that had a stored result and now fails does not keep showing the old one.
    q = chain()
    session.run(q, tmp_path)
    assert "scale" in OutputStore(tmp_path / ".apb" / "p").entries()[0]
    q.block("scale").type_id = "test.fail"
    q.block("scale").params = {}
    assert not session.run(q, tmp_path).ok
    assert "scale" not in OutputStore(tmp_path / ".apb" / "p").entries()[0]


def test_without_a_store_a_new_session_starts_idle(registry, tmp_path):
    Executor(registry).run(chain(), tmp_path)
    assert set(states(Executor(registry), chain(), tmp_path).values()) == {"idle"}
    assert not (tmp_path / ".apb").exists()  # a pipeline that was not saved leaves no trace


def test_first_save_writes_what_is_done_in_memory(registry, tmp_path):
    pipeline = chain()
    session = Executor(registry)  # unsaved pipeline: memory only
    session.run(pipeline, tmp_path)
    session.attach_store(OutputStore(tmp_path / ".apb" / "p"))  # the first Save
    assert session.persist(pipeline, tmp_path) == 3
    assert session.persist(pipeline, tmp_path) == 0  # nothing new
    assert set(states(new_session(registry, tmp_path), pipeline, tmp_path).values()) == {"done"}
    # Outputs that are stale in memory are not written.
    edited = chain(factor=3)
    other = Executor(registry, store=OutputStore(tmp_path / ".apb" / "q"))
    other._cache = dict(session._cache)
    assert other.persist(edited, tmp_path) == 1  # only the unchanged source block


def test_persist_removes_entries_of_blocks_that_no_longer_exist(registry, tmp_path):
    pipeline = chain()
    session = new_session(registry, tmp_path)
    session.run(pipeline, tmp_path)
    ghost = OutputStore(tmp_path / ".apb" / "p")
    ghost.save(
        "ghost", type_id="x", type_version=1, cache_key="k", outputs={"v": 1.0}, log=[], duration=0
    )
    pipeline.blocks = [b for b in pipeline.blocks if b.id != "total"]
    pipeline.connections = [c for c in pipeline.connections if c.target.block != "total"]
    session = new_session(registry, tmp_path)
    session.persist(pipeline, tmp_path)
    assert sorted(OutputStore(tmp_path / ".apb" / "p").entries()[0]) == ["scale", "src"]


def test_clear_everything(registry, tmp_path):
    pipeline = chain()
    session = new_session(registry, tmp_path)
    session.run(pipeline, tmp_path)
    assert session.clear_outputs(pipeline) == ["scale", "src", "total"]
    assert set(states(session, pipeline, tmp_path).values()) == {"idle"}
    assert not (tmp_path / ".apb").exists()
    assert set(states(new_session(registry, tmp_path), pipeline, tmp_path).values()) == {"idle"}
    Source.runs = 0
    session.run(pipeline, tmp_path)
    assert Source.runs == 1  # recomputed


def test_clearing_a_block_clears_everything_downstream(registry, tmp_path):
    pipeline = chain()
    session = new_session(registry, tmp_path)
    session.run(pipeline, tmp_path)
    assert session.clear_outputs(pipeline, ["scale"]) == ["scale", "total"]
    assert states(session, pipeline, tmp_path) == {"src": "done", "scale": "idle", "total": "idle"}
    assert sorted(OutputStore(tmp_path / ".apb" / "p").entries()[0]) == ["src"]
    Source.runs = Scale.runs = 0
    result = session.run(pipeline, tmp_path)
    assert (Source.runs, Scale.runs) == (0, 1) and result.output("total", "value") == 60.0
    assert session.clear_outputs(pipeline, ["total"]) == ["total"]
    assert session.clear_outputs(pipeline, ["nope"]) == []


def test_broken_entries_are_reported_and_recomputed(registry, tmp_path):
    pipeline = chain()
    new_session(registry, tmp_path).run(pipeline, tmp_path)
    (tmp_path / ".apb" / "p" / "scale" / "meta.json").write_text("{ broken", encoding="utf-8")
    (tmp_path / ".apb" / "p" / "total" / "meta.json").unlink()
    session = new_session(registry, tmp_path)
    assert states(session, pipeline, tmp_path) == {"src": "done", "scale": "idle", "total": "idle"}
    assert set(session.store_problems) == {"scale", "total"}
    assert set(session.restore(pipeline)) == {"src"}
    Scale.runs = 0
    assert session.run(pipeline, tmp_path).ok and Scale.runs == 1
    assert session.store_problems  # still the problems found when the store was read


def test_a_damaged_data_file_stops_the_block_that_needs_it_with_a_clear_message(registry, tmp_path):
    pipeline = chain()
    new_session(registry, tmp_path).run(pipeline, tmp_path)
    parquet = tmp_path / ".apb" / "p" / "scale" / "table.parquet"
    parquet.write_bytes(b"x" * parquet.stat().st_size)  # same size, damaged content
    pipeline.block("total").params = {}
    chain_with_change = chain(values=(1, 2, 3))
    chain_with_change.block("total").label = "changed"  # the key of 'total' does not change
    session = new_session(registry, tmp_path)
    # Make 'total' need its input: clear it so that it must run, leaving 'scale' stored.
    session.clear_outputs(chain_with_change, ["total"])
    result = session.run(chain_with_change, tmp_path)
    assert result.results["total"].state is BlockState.ERROR
    assert "Stored output 'table' of block 'scale' cannot be read" in result.results["total"].error
    assert "Clear the stored output" in result.results["total"].error


def test_other_tool_version_is_not_reused(registry, tmp_path, monkeypatch):
    pipeline = chain()
    monkeypatch.setattr(store_module, "__version__", "0.0.1")
    new_session(registry, tmp_path).run(pipeline, tmp_path)
    monkeypatch.undo()
    session = new_session(registry, tmp_path)
    assert set(states(session, pipeline, tmp_path).values()) == {"stale"}
    assert session.restore(pipeline) == {}
    Source.runs = 0
    session.run(pipeline, tmp_path)
    assert Source.runs == 1
    assert set(states(new_session(registry, tmp_path), pipeline, tmp_path).values()) == {"done"}


def test_values_that_cannot_be_stored_stay_in_memory_with_a_note(registry, tmp_path):
    from apb.engine import Block, NoParams, Port, PortType

    class Odd(Block[NoParams]):
        type_id, category, title = "test.odd", "Test", "Odd"
        outputs = (Port("table", PortType.TABLE),)

        def run(self, params, inputs, ctx):
            return {"table": pd.DataFrame({"A": [{"x": 1}, {"y": 2}]})}

    registry.register(Odd)
    p = PipelineModel()
    p.add_block("test.odd", block_id="odd")
    p.add_block("test.source", block_id="src")
    events: list[RunEvent] = []
    session = Executor(registry, sink=events.append, store=OutputStore(tmp_path / ".apb" / "p"))
    result = session.run(p, tmp_path)
    assert result.ok  # the run is not affected
    assert any(
        "Outputs not stored" in line and "only text is stored" in line
        for line in result.results["odd"].log
    )
    assert any(e.kind is EventKind.LOG and e.block_id == "odd" for e in events)
    assert sorted(OutputStore(tmp_path / ".apb" / "p").entries()[0]) == ["src"]
    assert session.run(p, tmp_path).results["odd"].cached  # still cached in memory


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_a_read_only_folder_gives_one_warning_and_the_run_goes_on(registry, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        events: list[RunEvent] = []
        session = Executor(registry, sink=events.append, store=OutputStore(locked / ".apb" / "p"))
        result = session.run(chain(), locked)
        assert result.ok
        warnings = [e for e in events if e.kind is EventKind.LOG and e.block_id is None]
        assert len(warnings) == 1  # once, not once per block
        assert "Results cannot be stored next to the pipeline" in warnings[0].message
        assert session.store_warning == warnings[0].message
        assert session.run(chain(), locked).results["total"].cached  # memory still works
    finally:
        locked.chmod(0o700)


def test_blocks_that_write_files_are_done_only_while_the_files_exist(registry, tmp_path):
    from apb.engine import default_registry

    real = default_registry()
    (tmp_path / "data.csv").write_text("A\n1\n2\n", encoding="utf-8")
    p = PipelineModel()
    p.add_block("table.read_csv", block_id="csv", file="data.csv")
    p.add_block("table.write_excel", block_id="xlsx", file="out/book.xlsx")
    p.connect("csv.table", "xlsx.tables")
    session = Executor(real, store=OutputStore(tmp_path / ".apb" / "p"))
    assert session.run(p, tmp_path).ok and (tmp_path / "out" / "book.xlsx").exists()
    entry = session.stored_entry("xlsx")
    assert entry is not None and entry.written_files == ("out/book.xlsx",)
    assert states(session, p, tmp_path)["xlsx"] == "done"
    reopened = Executor(real, store=OutputStore(tmp_path / ".apb" / "p"))
    assert states(reopened, p, tmp_path) == {"csv": "done", "xlsx": "done"}
    (tmp_path / "out" / "book.xlsx").unlink()
    assert states(reopened, p, tmp_path) == {"csv": "done", "xlsx": "stale"}
    assert states(session, p, tmp_path)["xlsx"] == "stale"  # also in the session that wrote it
    assert reopened.run(p, tmp_path).ok and (tmp_path / "out" / "book.xlsx").exists()
    assert states(reopened, p, tmp_path)["xlsx"] == "done"


def test_the_store_entry_index_is_read_once(registry, tmp_path, monkeypatch):
    pipeline = chain()
    new_session(registry, tmp_path).run(pipeline, tmp_path)
    session = new_session(registry, tmp_path)
    calls = []
    original = OutputStore.entries
    monkeypatch.setattr(OutputStore, "entries", lambda self: calls.append(1) or original(self))
    for _ in range(5):  # the GUI refreshes the states twice a second
        states(session, pipeline, tmp_path)
    assert len(calls) == 1
