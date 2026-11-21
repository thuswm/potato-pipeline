"""The parts of the Nastran analysis block that need no Nastran (spec 09)."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from apb.blocks.nastran import command as cmd
from apb.blocks.nastran.deck import DeckError, find_includes, has_hdf5_request
from apb.blocks.nastran.f06 import Severity, parse_f06, read_text
from apb.blocks.solvers.fingerprint import fingerprint
from apb.blocks.solvers.process import run_command
from apb.blocks.solvers.record import (
    ADOPTED,
    FINISHED,
    RUNNING,
    RunRecord,
    read_record,
    record_path,
    write_record,
)
from apb.blocks.solvers.runmode import Action, Results, RunMode, decide
from apb.config import config_path
from tests.conftest import FEM_DIR

# ---- f06 --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("job", "information", "warnings"),
    [("modal", 65, 0), ("global_loads", 52, 0), ("local_loads", 50, 1)],
)
def test_real_f06_files(job, information, warnings):
    report = parse_f06(read_text(FEM_DIR / f"{job}.f06"))
    assert report.finished
    assert report.of(Severity.FATAL) == []
    assert len(report.of(Severity.WARNING)) == warnings
    assert sum(m.count for m in report.of(Severity.INFORMATION)) == information


def test_the_one_warning_of_local_loads():
    (warning,) = parse_f06(read_text(FEM_DIR / "local_loads.f06")).of(Severity.WARNING)
    assert (warning.number, warning.source, warning.line, warning.count) == (
        9058,
        "SUBDMAP SESTATIC",
        246,
        1,
    )
    assert warning.text == (
        "THE SOLUTION FOR THE RESIDUAL STRUCTURE AND THE APPLIED LOADS FOR THE CURRENT "
        "SUBCASE ARE ZERO."
    )  # the page header below it is not part of the text


def test_severities_numbers_and_text():
    text = """ *** USER FATAL MESSAGE 2 (OUTPT2)
     FIRST LINE
     SECOND LINE

 *** SYSTEM FATAL MESSAGE 99 (XSEM)
     SYSTEM TROUBLE
 *** USER ALARM MESSAGE 1 (OLD)
     AN ALARM
 *** USER WARNING MESSAGE  (NO NUMBER)
     A WARNING
 *** USER INFORMATION MESSAGE 3 (crdb::nastran::Processor::writeInput)
 *** USER WARNING MESSAGE 7 TEXT ON THE SAME LINE
"""
    report = parse_f06(text)
    by = {(m.severity, m.number): m for m in report.messages}
    assert by[(Severity.FATAL, 2)].text == "FIRST LINE SECOND LINE"
    assert by[(Severity.FATAL, 99)].source == "XSEM"
    assert by[(Severity.WARNING, 1)].text == "AN ALARM"  # ALARM counts as a warning
    assert by[(Severity.WARNING, 0)].source == "NO NUMBER"
    assert by[(Severity.INFORMATION, 3)].source == "crdb::nastran::Processor::writeInput"
    assert by[(Severity.WARNING, 7)].text == "TEXT ON THE SAME LINE"
    assert [m.severity for m in report.messages] == [
        Severity.FATAL,
        Severity.FATAL,
        Severity.WARNING,
        Severity.WARNING,
        Severity.WARNING,
        Severity.INFORMATION,
    ]  # worst first
    assert not report.finished


def test_repeated_messages_are_grouped_with_the_first_line():
    block = " *** USER WARNING MESSAGE 2038 (SEMG)\n     SAME TEXT\n"
    report = parse_f06(
        "header\n" + block * 3 + " *** USER WARNING MESSAGE 2038 (SEMG)\n     OTHER\n"
    )
    first, second = report.messages
    assert (first.count, first.line, first.text) == (3, 2, "SAME TEXT")
    assert (second.count, second.text) == (1, "OTHER")


def test_text_stops_at_blank_line_page_header_and_limit():
    long_text = " *** USER WARNING MESSAGE 1 (X)\n" + "".join(f"     LINE {i}\n" for i in range(30))
    (message,) = parse_f06(long_text).messages
    assert message.text.count("LINE") == 10
    paged = (
        " *** USER WARNING MESSAGE 1 (X)\n     TEXT\n1    STATIC     PAGE     7\n     NOT TEXT\n"
    )
    assert parse_f06(paged).messages[0].text == "TEXT"


def test_end_of_job_empty_file_and_line_endings():
    assert parse_f06("1      * * * END OF JOB * * *\n").finished
    empty = parse_f06("")
    assert not empty.finished and empty.messages == () and empty.lines == 0
    crlf = parse_f06(" *** USER WARNING MESSAGE 5 (X)\r\n     TEXT\r\n* * * END OF JOB * * *\r\n")
    assert crlf.messages[0].text == "TEXT" and crlf.finished


# ---- deck -------------------------------------------------------------------------------


def test_hdf5_request(tmp_path):
    def check(text: str) -> bool:
        deck = tmp_path / "d.bdf"
        deck.write_text(text, encoding="utf-8")
        return has_hdf5_request([deck])

    assert check("BEGIN BULK\nHDF5OUT CMPRMTHDNONE\n")
    assert check("begin bulk\nhdf5out\n")
    assert check("MDLPRM,HDF5,0\n")
    assert check("MDLPRM   HDF5    0\n")
    assert check("mdlprm , hdf5 , 0\n")
    assert not check("$ HDF5OUT is off\nBEGIN BULK\n")
    assert not check("MDLPRM,MESH,1\nPARAM,POST,-1\n")
    assert not check("")


def test_hdf5_request_in_an_included_file(tmp_path):
    main, extra = tmp_path / "m.bdf", tmp_path / "options.bdf"
    main.write_text("INCLUDE 'options.bdf'\n")
    extra.write_text("HDF5OUT\n")
    assert has_hdf5_request([main, extra]) and not has_hdf5_request([main])


def test_includes_quotes_relative_paths_and_nesting(tmp_path):
    (tmp_path / "loads").mkdir()
    (tmp_path / "loads" / "a.bdf").write_text("INCLUDE 'b.bdf'\n$ INCLUDE 'ignored.bdf'\n")
    (tmp_path / "b.bdf").write_text("GRID,1\n")
    (tmp_path / "c.bdf").write_text("GRID,2\n")
    (tmp_path / "d.bdf").write_text("GRID,3\n")
    deck = tmp_path / "m.bdf"
    deck.write_text(
        "INCLUDE 'loads/a.bdf'\ninclude \"c.bdf\"\n  Include loads\\..\\d.bdf\nINCLUDE 'c.bdf'\n"
    )
    names = [p.name for p in find_includes(deck)]
    assert names == ["a.bdf", "b.bdf", "c.bdf", "d.bdf"]  # nested found, each once, in order


def test_include_problems(tmp_path):
    deck = tmp_path / "m.bdf"
    deck.write_text("INCLUDE 'missing.bdf'\n")
    with pytest.raises(DeckError, match=r"includes missing.bdf, which does not exist"):
        find_includes(deck)
    (tmp_path / "a.bdf").write_text("INCLUDE 'b.bdf'\n")
    (tmp_path / "b.bdf").write_text("INCLUDE 'a.bdf'\n")
    deck.write_text("INCLUDE 'a.bdf'\n")
    with pytest.raises(DeckError, match="Include cycle"):
        find_includes(deck)
    (tmp_path / "self.bdf").write_text("INCLUDE 'self.bdf'\n")
    with pytest.raises(DeckError, match="Include cycle"):
        find_includes(tmp_path / "self.bdf")
    assert find_includes(FEM_DIR / "modal.bdf") == []  # the real decks include nothing


def test_fingerprint_follows_every_file(tmp_path):
    a, b = tmp_path / "a.bdf", tmp_path / "b.bdf"
    a.write_text("one")
    b.write_text("two")
    first, listing = fingerprint([a, b], tmp_path)
    assert [item["path"] for item in listing] == ["a.bdf", "b.bdf"]
    assert fingerprint([a, b], tmp_path)[0] == first
    b.write_text("TWO")
    assert fingerprint([a, b], tmp_path)[0] != first
    assert fingerprint([b, a], tmp_path)[0] != fingerprint([a, b], tmp_path)[0]


# ---- command ----------------------------------------------------------------------------


def test_default_command_and_placeholders(tmp_path):
    settings = cmd.resolve("", "", [])
    assert (settings.command, settings.command_from) == (cmd.DEFAULT_COMMAND, "default")
    deck = tmp_path / "my job.bdf"
    line = cmd.build_command(settings, deck)
    assert line.startswith("nastran ")
    assert "'my job.bdf'" in line or '"my job.bdf"' in line  # quoted for the shell
    assert line.endswith("batch=no old=no scr=yes")  # {options} is empty: no stray spaces


def test_every_placeholder_and_a_compound_command(tmp_path):
    deck = tmp_path / "modal.bdf"
    settings = cmd.resolve(
        "cd {folder} && nastran {input} jid={job} {options} # {input_path}", "", ["mem=4gb"]
    )
    line = cmd.build_command(settings, deck)
    assert f"cd {tmp_path}" in line.replace("'", "") and "modal.bdf" in line
    assert "jid=modal mem=4gb" in line and str(tmp_path / "modal.bdf") in line.replace("'", "")


def test_unknown_placeholder_and_broken_braces():
    with pytest.raises(
        cmd.CommandError, match=r"Unknown placeholder \{deck\}.*Available: \{input\}"
    ):
        cmd.check_template("nastran {deck}")
    with pytest.raises(cmd.CommandError, match="Cannot read the command"):
        cmd.check_template("nastran {input")


def test_options_are_checked_and_merged_later_wins():
    for bad in ("mem 4gb", "mem", "=4gb", "mem=4gb;rm", "mem=$X", "mem=a b"):
        with pytest.raises(cmd.CommandError, match="key=value"):
            cmd.validate_option(bad)
    assert cmd.merge_options(["mem=4gb", "parallel=2"], ["MEM=8gb"]) == ("MEM=8gb", "parallel=2")
    defaults = cmd.Defaults(options=("mem=8gb", "parallel=4"))
    assert cmd.resolve("", "", ["mem=16gb"], defaults).options == ("mem=16gb", "parallel=4")


def test_block_beats_config_file_beats_default(tmp_path):
    defaults = cmd.Defaults(command="nast {input}", path="/opt/msc/bin")
    assert (
        cmd.resolve("", "", [], cmd.Defaults()).command_from,
        cmd.resolve("", "", [], cmd.Defaults()).path_from,
    ) == ("default", "PATH")
    from_file = cmd.resolve("", "", [], defaults)
    assert (from_file.command, from_file.command_from, from_file.path_from) == (
        "nast {input}",
        "config file",
        "config file",
    )
    own = cmd.resolve("mynast {input}", "/other", [], defaults)
    assert (own.command, own.command_from, own.path, own.path_from) == (
        "mynast {input}",
        "block",
        "/other",
        "block",
    )


def test_config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    assert cmd.load_defaults(path) == cmd.Defaults()  # no file: no defaults
    path.write_text(
        '[nastran]\ncommand = "nast {input}"\npath = "C:/MSC/bin"\noptions = ["mem=8gb"]\n',
        encoding="utf-8",
    )
    assert cmd.load_defaults(path) == cmd.Defaults("nast {input}", "C:/MSC/bin", ("mem=8gb",))
    path.write_text("[other]\nx = 1\n")
    assert cmd.load_defaults(path) == cmd.Defaults()
    path.write_text("[nastran\n")
    with pytest.raises(cmd.CommandError, match="cannot be read"):
        cmd.load_defaults(path)
    path.write_text('[nastran]\noptions = ["bad option"]\n')
    with pytest.raises(cmd.CommandError, match="key=value"):
        cmd.load_defaults(path)
    path.write_text('[nastran]\ncommand = "nast {nope}"\n')
    with pytest.raises(cmd.CommandError, match="Unknown placeholder"):
        cmd.load_defaults(path)
    monkeypatch.setenv("APB_CONFIG", str(tmp_path / "elsewhere.toml"))
    assert config_path() == tmp_path / "elsewhere.toml"
    monkeypatch.delenv("APB_CONFIG")
    assert config_path().name == "config.toml" and config_path().parent.name == "apb"


def test_path_goes_in_front_of_path(tmp_path):
    settings = cmd.resolve("", "/opt/msc/bin", [])
    env = cmd.environment(settings, {"PATH": "/usr/bin", "KEEP": "1"})
    assert env["PATH"] == "/opt/msc/bin" + os.pathsep + "/usr/bin" and env["KEEP"] == "1"
    assert cmd.environment(cmd.resolve("", "", []), {"PATH": "/usr/bin"})["PATH"] == "/usr/bin"
    assert cmd.environment(settings, {})["PATH"] == "/opt/msc/bin"


def test_program_status(tmp_path, fake_nastran):
    env = {"PATH": str(fake_nastran.bin)}
    status = cmd.program_status("nastran job.bdf batch=no", env)
    assert status.checked and status.program == "nastran" and status.found
    assert Path(status.found).parent == fake_nastran.bin
    missing = cmd.program_status("nastran job.bdf", {"PATH": str(tmp_path)})
    assert missing.checked and missing.found is None and missing.program == "nastran"
    for compound in ("module load x && nastran a.bdf", "nastran a.bdf | tee log", "a; b"):
        assert not cmd.program_status(compound, env).checked
    assert not cmd.program_status("", env).checked


# ---- run mode ---------------------------------------------------------------------------

HASH, OPTIONS = "h1", ["batch=no"]


def results(
    *, f06=True, h5=True, complete=True, newest_input=100.0, mtimes=(200.0, 200.0), record=None
):
    files = {}
    if f06:
        files["job.f06"] = mtimes[0]
    if h5:
        files["job.h5"] = mtimes[1]
    return Results(files, ("job.f06", "job.h5"), complete, newest_input, record)


def record(status=FINISHED, digest=HASH, options=None):
    return RunRecord(
        status, digest, options=list(options or OPTIONS), finished="2026-11-15T14:25:00+01:00"
    )


def action(mode, res):
    return decide(mode, res, HASH, OPTIONS)


def test_always_runs_whatever_exists():
    assert action(RunMode.ALWAYS, results()).action is Action.RUN
    assert action(RunMode.ALWAYS, results(f06=False, h5=False)).action is Action.RUN


def test_never_uses_what_exists_and_fails_if_it_does_not():
    fail = action(RunMode.NEVER, results(h5=False))
    assert fail.action is Action.FAIL
    assert "Result files not found: job.h5" in fail.reason and "If input changed" in fail.reason
    use = action(RunMode.NEVER, results(record=record()))
    assert (use.action, use.warning) == (Action.USE, "")
    assert action(RunMode.NEVER, results(record=record(digest="other"))).warning == (
        "The input changed after these results were made"
    )
    assert action(RunMode.NEVER, results(record=record(RUNNING))).warning == (
        "The run that made these results did not finish"
    )
    assert action(RunMode.NEVER, results()).warning == ""  # no record, but newer than the input
    assert (
        action(RunMode.NEVER, results(newest_input=300.0)).warning
        == "The input is newer than the results"
    )


def test_if_changed_runs_without_results():
    assert action(RunMode.IF_CHANGED, results(f06=False)).action is Action.RUN
    assert "job.f06" in action(RunMode.IF_CHANGED, results(f06=False)).reason
    assert (
        action(RunMode.IF_CHANGED, results(f06=False, h5=False, record=record())).action
        is Action.RUN
    )


def test_if_changed_with_a_record_the_fingerprint_decides():
    same = action(RunMode.IF_CHANGED, results(record=record()))
    assert same.action is Action.USE and "up to date: made 2026-11-15 14:25" in same.reason
    assert action(RunMode.IF_CHANGED, results(record=record(ADOPTED))).action is Action.USE
    changed = action(RunMode.IF_CHANGED, results(record=record(digest="other")))
    assert changed.action is Action.RUN and "input or the options changed" in changed.reason
    assert (
        action(RunMode.IF_CHANGED, results(record=record(options=["mem=1gb"]))).action is Action.RUN
    )
    assert "did not finish" in action(RunMode.IF_CHANGED, results(record=record(RUNNING))).reason
    # A record beats the file times: old files with a matching record are up to date.
    assert (
        action(RunMode.IF_CHANGED, results(newest_input=999.0, record=record())).action
        is Action.USE
    )


def test_if_changed_without_a_record_the_file_times_decide():
    adopted = action(RunMode.IF_CHANGED, results())
    assert adopted.action is Action.ADOPT and "newer than the input" in adopted.reason
    older = action(RunMode.IF_CHANGED, results(mtimes=(200.0, 50.0)))  # .h5 older than the input
    assert older.action is Action.RUN and "older than the input" in older.reason
    equal = action(RunMode.IF_CHANGED, results(newest_input=200.0))  # as old: not newer
    assert equal.action is Action.RUN
    assert action(RunMode.IF_CHANGED, results(complete=False)).action is Action.RUN
    assert "incomplete" in action(RunMode.IF_CHANGED, results(complete=False)).reason


# ---- run record -------------------------------------------------------------------------


def test_run_record_roundtrip_and_damage(tmp_path):
    deck = tmp_path / "model.bdf"
    assert record_path(deck) == tmp_path / "model.apb-run.json"
    assert read_record(record_path(deck)) is None
    original = RunRecord(
        FINISHED,
        "abc",
        [{"path": "model.bdf", "sha256": "x"}],
        ["mem=1gb"],
        "nastran model.bdf",
        "t1",
        "t2",
        0,
        12.5,
    )
    write_record(record_path(deck), original)
    assert read_record(record_path(deck)) == original and original.complete
    assert not RunRecord(RUNNING, "abc").complete and RunRecord(ADOPTED, "abc").complete
    assert [p.name for p in tmp_path.iterdir()] == ["model.apb-run.json"]  # no temporary file left
    for text in (
        "{ broken",
        "[]",
        '{"schema": 2, "status": "finished", "input_hash": "a"}',
        '{"schema": 1, "status": "weird", "input_hash": "a"}',
        '{"schema": 1}',
    ):
        record_path(deck).write_text(text, encoding="utf-8")
        assert read_record(record_path(deck)) is None


# ---- process ----------------------------------------------------------------------------

PY = f'"{sys.executable}"'


def run(command, tmp_path, *, cancel=None, timeout=None):
    lines: list[str] = []
    result = run_command(
        command,
        cwd=tmp_path,
        env=dict(os.environ),
        on_line=lines.append,
        cancel=cancel or threading.Event(),
        timeout=timeout,
        poll=0.05,
    )
    return result, lines


def test_command_output_exit_code_and_working_folder(tmp_path):
    result, lines = run(
        f"{PY} -c \"import os; print(os.getcwd()); print('second'); raise SystemExit(4)\"", tmp_path
    )
    assert result.exit_code == 4 and not result.cancelled and not result.timed_out
    assert lines == [str(tmp_path.resolve()), "second"] or lines == [str(tmp_path), "second"]
    assert result.tail == tuple(lines) and result.duration > 0


def test_stderr_is_part_of_the_output_and_a_missing_program_is_an_exit_code(tmp_path):
    result, lines = run(
        f"{PY} -c \"import sys; print('out'); print('err', file=sys.stderr)\"", tmp_path
    )
    assert sorted(lines) == ["err", "out"] and result.exit_code == 0
    missing, _ = run("definitely_no_such_program_xyz", tmp_path)
    assert missing.exit_code != 0  # the shell reports "not found" as an exit code


def test_stop_ends_the_command_and_what_it_started(tmp_path):
    cancel = threading.Event()
    marker = tmp_path / "child_alive.txt"
    child = f"import time; time.sleep(60); open({str(marker)!r}, 'w').write('x')"
    script = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n"
    )
    (tmp_path / "parent.py").write_text(script)
    threading.Timer(1.0, cancel.set).start()
    began = time.monotonic()
    result, lines = run(f"{PY} parent.py", tmp_path, cancel=cancel)
    assert result.cancelled and result.exit_code is None and lines == ["started"]
    assert time.monotonic() - began < 15
    assert not marker.exists()


def test_time_limit(tmp_path):
    began = time.monotonic()
    result, lines = run(
        f"{PY} -c \"import time; print('x', flush=True); time.sleep(60)\"", tmp_path, timeout=1.0
    )
    assert result.timed_out and result.exit_code is None and not result.cancelled
    assert lines == ["x"] and time.monotonic() - began < 15


def test_long_output_is_streamed_and_only_the_tail_is_kept(tmp_path):
    result, lines = run(f'{PY} -c "[print(i) for i in range(500)]"', tmp_path)
    assert len(lines) == 500 and result.tail == tuple(str(i) for i in range(490, 500))
