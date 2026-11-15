"""The Nastran analysis block against a fake Nastran (spec 09 §3.11)."""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from apb.blocks.nastran.analysis import NastranError, NastranRun
from apb.blocks.solvers.record import ADOPTED, FINISHED, RUNNING, read_record, record_path
from apb.engine import BlockState, Executor, PipelineModel, RunContext, default_registry
from apb.engine.block import BlockCancelledError
from apb.engine.store import OutputStore
from tests.conftest import FEM_DIR, FakeNastran, write_deck

NOW = 1_800_000_000.0  # fixed times make "newer" and "older" exact


def make(tmp_path, fake: FakeNastran, **params):
    """Return ``(deck, run)``: ``run()`` runs the block once and returns outputs and log."""
    deck = write_deck(tmp_path / "work", params.pop("job", "job"))
    params.setdefault("path", str(fake.bin))

    def run(**override):
        merged = {"input_file": str(deck)} | params | override
        log: list[str] = []
        ctx = RunContext(block_id="nas", pipeline_dir=tmp_path, output_dir=tmp_path)
        ctx.log = log.append  # type: ignore[method-assign]
        ctx.cancel_event = threading.Event()
        parsed = NastranRun.Params.model_validate(merged)
        return NastranRun().run(parsed, {}, ctx), log

    return deck, run


def stamp(path: Path, when: float) -> None:
    os.utime(path, (when, when))


# ---- running ----------------------------------------------------------------------------


def test_first_run_starts_nastran_in_the_folder_of_the_deck(tmp_path, fake_nastran):
    deck, run = make(tmp_path, fake_nastran)
    out, log = run()
    assert fake_nastran.calls() == [(str(deck.parent), "job.bdf batch=no old=no scr=yes")]
    assert Path(out["f06"]) == deck.with_suffix(".f06") and Path(out["h5"]).is_file()
    assert out["status"] == "OK"
    table = out["messages"]
    assert list(table.columns) == ["SEVERITY", "NUMBER", "MODULE", "COUNT", "LINE", "TEXT"]
    assert table.empty and table.attrs["title"] == "Nastran messages"
    assert table.attrs["source_file"] == out["f06"]
    assert any(line.startswith("Command: nastran job.bdf batch=no old=no scr=yes") for line in log)
    assert "fake nastran: job job" in log and any("Nastran finished" in line for line in log)
    record = read_record(record_path(deck))
    assert record and record.status == FINISHED and record.exit_code == 0
    assert record.command == "nastran job.bdf batch=no old=no scr=yes"
    assert record.inputs[0]["path"] == "job.bdf"


def test_second_run_finds_the_results_up_to_date(tmp_path, fake_nastran):
    _, run = make(tmp_path, fake_nastran)
    run()
    _, log = run()
    assert len(fake_nastran.calls()) == 1
    assert any("Results are up to date" in line for line in log)


def test_a_changed_deck_or_include_runs_again(tmp_path, fake_nastran):
    deck, run = make(tmp_path, fake_nastran)
    run()
    deck.write_text(deck.read_text() + "$ edited\n")
    _, log = run()
    assert len(fake_nastran.calls()) == 2
    assert any("input or the options changed" in line for line in log)
    included = deck.parent / "loads.bdf"
    included.write_text("FORCE,1,1,0,1.,0.,0.,1.\n")
    deck.write_text(deck.read_text().replace("ENDDATA", "INCLUDE 'loads.bdf'\nENDDATA"))
    run()
    assert len(fake_nastran.calls()) == 3
    run()
    assert len(fake_nastran.calls()) == 3  # up to date again, with the include
    included.write_text("FORCE,1,1,0,2.,0.,0.,1.\n")
    _, log = run()
    assert len(fake_nastran.calls()) == 4 and any("Input: job.bdf + 1 included" in x for x in log)


def test_changed_options_run_again(tmp_path, fake_nastran):
    _, run = make(tmp_path, fake_nastran)
    run()
    run(options=["mem=4gb"])
    assert fake_nastran.calls()[-1][1] == "job.bdf batch=no old=no scr=yes mem=4gb"
    run(options=["mem=4gb"])
    assert len(fake_nastran.calls()) == 2


def test_results_made_outside_the_tool_are_adopted_if_newer(tmp_path, fake_nastran):
    deck, run = make(tmp_path, fake_nastran)
    for name in ("job.f06", "job.h5"):  # as if made by hand
        shutil.copyfile(FEM_DIR / ("global_loads." + name.split(".")[1]), deck.parent / name)
    stamp(deck, NOW)
    stamp(deck.with_suffix(".f06"), NOW + 60)
    stamp(deck.with_suffix(".h5"), NOW + 61)
    out, log = run()
    assert fake_nastran.calls() == []  # nothing was run, nothing overwritten
    assert out["status"] == "OK"
    assert any("newer than the input and are used" in line for line in log)
    record = read_record(record_path(deck))
    assert record and record.status == ADOPTED and record.input_hash
    # From now on the fingerprint decides, not the file times.
    stamp(deck, NOW + 500)
    _, log = run()
    assert fake_nastran.calls() == [] and any("up to date" in line for line in log)
    deck.write_text(deck.read_text() + "$ edited\n")
    run()
    assert len(fake_nastran.calls()) == 1


@pytest.mark.parametrize("older", ["f06", "h5", "equal"])
def test_results_made_outside_the_tool_that_are_not_newer_are_replaced(
    tmp_path, fake_nastran, older
):
    deck, run = make(tmp_path, fake_nastran)
    for suffix in (".f06", ".h5"):
        shutil.copyfile(FEM_DIR / f"global_loads{suffix}", deck.with_suffix(suffix))
    stamp(deck, NOW)
    stamp(deck.with_suffix(".f06"), NOW + (-5 if older == "f06" else 0 if older == "equal" else 60))
    stamp(deck.with_suffix(".h5"), NOW + (-5 if older == "h5" else 0 if older == "equal" else 60))
    _, log = run()
    assert len(fake_nastran.calls()) == 1 and any("older than the input" in x for x in log)


def test_an_incomplete_f06_without_record_is_not_adopted(tmp_path, fake_nastran):
    deck, run = make(tmp_path, fake_nastran)
    deck.with_suffix(".f06").write_text("cut off")
    shutil.copyfile(FEM_DIR / "global_loads.h5", deck.with_suffix(".h5"))
    stamp(deck, NOW)
    stamp(deck.with_suffix(".f06"), NOW + 60)
    stamp(deck.with_suffix(".h5"), NOW + 60)
    _, log = run()
    assert len(fake_nastran.calls()) == 1 and any("incomplete" in x for x in log)


def test_always_runs_every_time_and_replaces_without_asking(tmp_path, fake_nastran):
    _, run = make(tmp_path, fake_nastran, run_mode="always")
    run()
    run()
    assert len(fake_nastran.calls()) == 2
    assert NastranRun.always_runs(NastranRun.Params(input_file="x.bdf", run_mode="always"))
    assert not NastranRun.always_runs(NastranRun.Params(input_file="x.bdf"))
    assert not NastranRun.always_runs(None)


def test_old_results_are_deleted_before_a_run(tmp_path, fake_nastran):
    deck, run = make(tmp_path, fake_nastran, run_mode="always")
    for suffix in (".f06", ".f04", ".log", ".h5"):
        deck.with_suffix(suffix).write_text("OLD")
    fake_nastran.behave("background")  # Nastran returns at once and writes nothing
    with pytest.raises(NastranError, match=r"did not write job.f06"):
        run()
    assert not any(deck.with_suffix(s).exists() for s in (".f06", ".f04", ".log", ".h5"))


# ---- never ------------------------------------------------------------------------------


def copy_example(tmp_path, job: str):
    folder = tmp_path / "work"
    folder.mkdir(exist_ok=True)
    for suffix in (".bdf", ".f06", ".h5"):
        shutil.copyfile(FEM_DIR / f"{job}{suffix}", folder / f"{job}{suffix}")
    deck = folder / f"{job}.bdf"
    stamp(deck, NOW)
    stamp(deck.with_suffix(".f06"), NOW + 10)
    stamp(deck.with_suffix(".h5"), NOW + 10)
    return deck


def test_never_uses_the_real_results_and_finds_the_warning(tmp_path, fake_nastran):
    deck = copy_example(tmp_path, "local_loads")
    params = {"input_file": str(deck), "run_mode": "never", "path": str(fake_nastran.bin)}
    ctx = RunContext(block_id="nas", pipeline_dir=tmp_path, output_dir=tmp_path)
    log: list[str] = []
    ctx.log = log.append  # type: ignore[method-assign]
    out = NastranRun().run(NastranRun.Params.model_validate(params), {}, ctx)
    assert fake_nastran.calls() == [] and out["status"] == "WARNINGS"
    table = out["messages"]
    assert table[["SEVERITY", "NUMBER", "MODULE", "COUNT"]].values.tolist() == [
        ["WARNING", 9058, "SUBDMAP SESTATIC", 1]
    ]
    assert table["LINE"].iloc[0] == 246 and "APPLIED LOADS" in table["TEXT"].iloc[0]
    assert any(x.startswith("WARNING: 9058 (SUBDMAP SESTATIC): THE SOLUTION") for x in log)
    assert not any(line.startswith("INFORMATION") for line in log)


@pytest.mark.parametrize(
    ("job", "status"), [("modal", "OK"), ("global_loads", "OK"), ("local_loads", "WARNINGS")]
)
def test_never_on_the_three_real_analyses(tmp_path, fake_nastran, job, status):
    deck = copy_example(tmp_path, job)
    ctx = RunContext(block_id="nas", pipeline_dir=tmp_path, output_dir=tmp_path)
    out = NastranRun().run(
        NastranRun.Params.model_validate({"input_file": str(deck), "run_mode": "never"}), {}, ctx
    )
    assert out["status"] == status and Path(out["h5"]) == deck.with_suffix(".h5")


def test_never_without_results_and_with_an_outdated_input(tmp_path, fake_nastran):
    deck, run = make(tmp_path, fake_nastran, run_mode="never")
    with pytest.raises(NastranError, match=r"Result files not found: job.f06, job.h5"):
        run()
    run(run_mode="if_changed")  # make results (and a record)
    deck.write_text(deck.read_text() + "$ changed\n")
    _, log = run(run_mode="never")
    assert len(fake_nastran.calls()) == 1
    assert "WARNING: The input changed after these results were made" in log


def test_ignore_messages_fail_on_and_information(tmp_path, fake_nastran):
    deck = copy_example(tmp_path, "local_loads")
    ctx = RunContext(block_id="nas", pipeline_dir=tmp_path, output_dir=tmp_path)

    def go(**extra):
        log: list[str] = []
        ctx.log = log.append  # type: ignore[method-assign]
        params = {"input_file": str(deck), "run_mode": "never"} | extra
        return NastranRun().run(NastranRun.Params.model_validate(params), {}, ctx), log

    out, log = go(ignore_messages=[9058])
    assert out["status"] == "OK" and out["messages"].empty
    assert any(line.startswith("Ignored: WARNING 9058") for line in log)
    with pytest.raises(
        NastranError, match=r"contains 1 message\(s\) that fail the block: WARNING 9058"
    ):
        go(fail_on="warning")
    assert go(fail_on="warning", ignore_messages=[9058])[0]["status"] == "OK"
    assert go(fail_on="never")[0]["status"] == "WARNINGS"
    with_info, log = go(include_information=True)
    assert set(with_info["messages"]["SEVERITY"]) == {"WARNING", "INFORMATION"}
    assert with_info["messages"]["SEVERITY"].iloc[0] == "WARNING"  # worst first
    assert any(line.startswith("INFORMATION") for line in log)


# ---- failures ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        (
            "exit3",
            r"Nastran exited with code 3\. Last output: "
            r"fake nastran: job job \| fake nastran: finished",
        ),
        ("fatal", "Nastran exited with code 1"),
        (
            "fatal_exit0",
            r"contains 1 message\(s\) that fail the block: FATAL 2 \(FAKE\): THIS IS A FATAL",
        ),
        ("no_end", "ends without END OF JOB"),
        ("no_h5", "did not write job.h5"),
        ("background", "did not write job.f06"),
    ],
)
def test_failures_have_clear_messages(tmp_path, fake_nastran, mode, message):
    _deck, run = make(tmp_path, fake_nastran)
    fake_nastran.behave(mode)
    with pytest.raises(NastranError, match=message):
        run()


def test_a_fatal_message_can_be_reported_without_failing(tmp_path, fake_nastran):
    _, run = make(tmp_path, fake_nastran, fail_on="never")
    fake_nastran.behave("fatal_exit0")
    out, log = run()
    assert out["status"] == "FATAL" and out["messages"]["SEVERITY"].tolist() == ["FATAL"]
    assert any(line.startswith("FATAL: 2 (FAKE): THIS IS A FATAL") for line in log)


def test_a_warning_gives_status_warnings(tmp_path, fake_nastran):
    _, run = make(tmp_path, fake_nastran)
    fake_nastran.behave("warning")
    out, _ = run()
    assert out["status"] == "WARNINGS" and out["messages"]["NUMBER"].tolist() == [9058]


def test_problems_before_the_run(tmp_path, fake_nastran):
    _deck, run = make(tmp_path, fake_nastran)
    with pytest.raises(NastranError, match=r"Input deck not found"):
        run(input_file=str(tmp_path / "nope.bdf"))
    plain = write_deck(tmp_path / "plain", "plain", hdf5=False)
    with pytest.raises(NastranError, match=r"has no HDF5 request.*HDF5OUT"):
        run(input_file=str(plain))
    assert fake_nastran.calls() == []  # nothing was started, nothing deleted
    broken = write_deck(tmp_path / "inc", "inc", include="missing.bdf")
    with pytest.raises(NastranError, match=r"includes missing.bdf, which does not exist"):
        run(input_file=str(broken))
    with pytest.raises(NastranError, match=r"Nastran program 'definitely_missing' not found"):
        run(command="definitely_missing {input}")
    assert fake_nastran.calls() == []


def test_bad_settings_are_refused_when_the_block_is_validated():
    with pytest.raises(ValueError, match=r"key=value"):
        NastranRun.Params(input_file="a.bdf", options=["bad option"])
    with pytest.raises(ValueError, match=r"Unknown placeholder"):
        NastranRun.Params(input_file="a.bdf", command="nastran {deck}")
    with pytest.raises(ValueError, match=r"greater than 0"):
        NastranRun.Params(input_file="a.bdf", timeout_minutes=0)


def test_a_broken_config_file_is_reported_with_its_name(tmp_path, fake_nastran, monkeypatch):
    bad = tmp_path / "config.toml"
    bad.write_text("[nastran\n")
    monkeypatch.setenv("APB_CONFIG", str(bad))
    _, run = make(tmp_path, fake_nastran)
    with pytest.raises(NastranError, match=r"Config file .*config.toml cannot be read"):
        run()


# ---- settings ---------------------------------------------------------------------------


def test_the_computer_default_in_the_config_file(tmp_path, fake_nastran, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(
        f'[nastran]\npath = "{fake_nastran.bin.as_posix()}"\noptions = ["mem=8gb"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("APB_CONFIG", str(config))
    _, run = make(tmp_path, fake_nastran, path="")  # nothing set in the block
    run()
    assert fake_nastran.calls()[0][1] == "job.bdf batch=no old=no scr=yes mem=8gb"
    run(run_mode="always", options=["mem=16gb"])  # the block wins
    assert fake_nastran.calls()[1][1].endswith("mem=16gb")


def test_a_command_of_your_own(tmp_path, fake_nastran):
    _, run = make(tmp_path, fake_nastran)
    run(command="nastran {job}.bdf jid={job} {options}", options=["mem=2gb"])
    assert fake_nastran.calls()[0][1] == "job.bdf jid=job mem=2gb"


def test_file_names_with_spaces_work(tmp_path, fake_nastran):
    _deck, run = make(tmp_path, fake_nastran, job="my model")
    out, _ = run()
    assert Path(out["f06"]).name == "my model.f06" and Path(out["h5"]).is_file()
    # One argument for the deck: the fake found the job "my model" and wrote its files.
    assert fake_nastran.calls()[0][1].startswith("my model.bdf batch=no")


# ---- time limit and Stop ----------------------------------------------------------------


def test_time_limit_leaves_an_unfinished_record(tmp_path, fake_nastran):
    deck, run = make(tmp_path, fake_nastran, timeout_minutes=0.02)
    fake_nastran.behave("hang")
    began = time.monotonic()
    with pytest.raises(NastranError, match=r"did not finish within 0.02 minutes and was stopped"):
        run()
    assert time.monotonic() - began < 20
    record = read_record(record_path(deck))
    assert record and record.status == RUNNING
    fake_nastran.behave("ok")
    for suffix in (".f06", ".h5"):  # results from elsewhere, as if the stopped run had made them
        shutil.copyfile(FEM_DIR / f"global_loads{suffix}", deck.with_suffix(suffix))
    _, log = run()  # the next run knows the last one did not finish
    assert any("A previous run did not finish" in x for x in log)
    assert read_record(record_path(deck)).status == FINISHED  # type: ignore[union-attr]


def test_stop_ends_nastran_and_skips_the_block(tmp_path, fake_nastran):
    deck = write_deck(tmp_path / "work")
    pipeline = PipelineModel()
    pipeline.add_block(
        "nastran.run", block_id="nas", input_file=str(deck), path=str(fake_nastran.bin)
    )
    fake_nastran.behave("hang")
    executor = Executor(default_registry())
    holder = {}
    worker = threading.Thread(target=lambda: holder.update(result=executor.run(pipeline, tmp_path)))
    began = time.monotonic()
    worker.start()
    while not fake_nastran.calls() and time.monotonic() - began < 20:
        time.sleep(0.05)
    time.sleep(0.5)
    executor.stop()
    worker.join(timeout=20)
    assert not worker.is_alive() and time.monotonic() - began < 25
    result = holder["result"].results["nas"]
    assert result.state is BlockState.SKIPPED and "stopped by the user" in result.error
    assert read_record(record_path(deck)).status == RUNNING  # type: ignore[union-attr]
    assert holder["result"].cancelled and isinstance(BlockCancelledError("x"), Exception)


# ---- in a pipeline: downstream blocks ---------------------------------------------------


def analysis_pipeline(tmp_path, fake: FakeNastran, mode="if_changed") -> PipelineModel:
    deck = write_deck(tmp_path / "work")
    p = PipelineModel()
    p.add_block(
        "nastran.run", block_id="nas", input_file=str(deck), path=str(fake.bin), run_mode=mode
    )
    p.add_block("nastran.hdf5_results", block_id="res", result="ELEMENTAL/ELEMENT_FORCE/BUSH")
    p.connect("nas.h5", "res.file")
    return p


def test_the_h5_flows_to_the_result_block(tmp_path, fake_nastran):
    result = Executor(default_registry()).run(analysis_pipeline(tmp_path, fake_nastran), tmp_path)
    assert result.ok
    forces = result.output("res", "table")
    assert forces["EID"].tolist() == [17, 18, 19]  # the global_loads.h5 the fake delivers
    assert forces.attrs["source_file"].endswith("job.h5")
    assert result.output("nas", "status") == "OK"


def test_a_rewritten_h5_makes_the_downstream_block_stale(tmp_path, fake_nastran):
    pipeline = analysis_pipeline(tmp_path, fake_nastran)
    executor = Executor(default_registry())
    executor.run(pipeline, tmp_path)
    states = executor.states(pipeline, tmp_path)
    assert states == {"nas": BlockState.DONE, "res": BlockState.DONE}
    shutil.copyfile(
        FEM_DIR / "local_loads.h5", tmp_path / "work" / "job.h5"
    )  # replaced from outside
    assert executor.states(pipeline, tmp_path) == {"nas": BlockState.DONE, "res": BlockState.STALE}
    again = executor.run(pipeline, tmp_path)
    assert again.results["nas"].cached and not again.results["res"].cached
    assert again.output("res", "table")["EID"].tolist() == [2, 3, 4, 5]


def test_downstream_blocks_see_the_files_written_in_the_same_run(
    tmp_path, fake_nastran, monkeypatch
):
    pipeline = analysis_pipeline(tmp_path, fake_nastran, mode="always")
    executor = Executor(default_registry())
    first = executor.run(pipeline, tmp_path)
    assert first.output("res", "table")["EID"].tolist() == [17, 18, 19]
    monkeypatch.setenv("FAKE_NASTRAN_H5", str(FEM_DIR / "local_loads.h5"))  # a different result
    second = executor.run(pipeline, tmp_path)
    assert len(fake_nastran.calls()) == 2
    # Nastran ran in this run and wrote another .h5: the result block was recomputed *now*.
    assert not second.results["res"].cached
    assert second.output("res", "table")["EID"].tolist() == [2, 3, 4, 5]
    third = executor.run(pipeline, tmp_path)  # the same file again: the result block is cached
    assert third.results["res"].cached


def test_a_reopened_pipeline_with_an_analysis_is_done_without_running(tmp_path, fake_nastran):
    pipeline = analysis_pipeline(tmp_path, fake_nastran, mode="always")
    store = OutputStore(tmp_path / ".apb" / "p")
    Executor(default_registry(), store=store).run(pipeline, tmp_path)
    reopened = Executor(default_registry(), store=OutputStore(tmp_path / ".apb" / "p"))
    assert reopened.states(pipeline, tmp_path) == {"nas": BlockState.DONE, "res": BlockState.DONE}
    (tmp_path / "work" / "job.f06").unlink()  # a delivered file is gone: not done any more
    assert reopened.states(pipeline, tmp_path)["nas"] is BlockState.STALE
    assert reopened.states(pipeline, tmp_path)["res"] is BlockState.STALE


def test_messages_of_several_analyses_stack_into_one_table(tmp_path, fake_nastran):
    folder = tmp_path / "work"
    pipeline = PipelineModel()
    for job in ("modal", "local_loads"):
        copy_example(tmp_path, job)
        pipeline.add_block(
            "nastran.run", block_id=job, input_file=str(folder / f"{job}.bdf"), run_mode="never"
        )
    pipeline.add_block("table.concat", block_id="all", add_source_column=True)
    pipeline.connect("modal.messages", "all.tables")
    pipeline.connect("local_loads.messages", "all.tables")
    result = Executor(default_registry()).run(pipeline, tmp_path)
    assert result.ok
    table: pd.DataFrame = result.output("all", "table")
    assert table[["SEVERITY", "NUMBER"]].values.tolist() == [["WARNING", 9058]]
    assert table["MODULE"].tolist() == ["SUBDMAP SESTATIC"]
    assert table["SOURCE"].tolist() == ["local_loads.f06"]  # the stacking block names the file
