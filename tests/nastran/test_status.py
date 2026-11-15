"""What the analysis panel shows without running Nastran (spec 09 §3.9)."""

from __future__ import annotations

import os
import shutil

from apb.blocks.nastran.analysis import NastranRun
from apb.blocks.nastran.status import analysis_status
from tests.conftest import FEM_DIR, FakeNastran, write_deck

NOW = 1_800_000_000.0


def stamp(path, when):
    os.utime(path, (when, when))


def status(tmp_path, **stored):
    return analysis_status(stored, tmp_path)


def test_problems_are_reported_not_raised(tmp_path, fake_nastran):
    assert status(tmp_path).problem == "Choose the input deck"
    missing = status(tmp_path, input_file="nope.bdf", path=str(fake_nastran.bin))
    assert missing.problem.startswith("Input deck not found") and not missing.deck_exists
    assert missing.program is not None and missing.program.found  # the program is still checked
    bad = status(tmp_path, input_file="a.bdf", options=["bad option"])
    assert "key=value" in bad.problem
    deck = write_deck(tmp_path, "a", include="missing.bdf")
    assert (
        "includes missing.bdf"
        in status(tmp_path, input_file=str(deck), path=str(fake_nastran.bin)).problem
    )


def test_program_found_or_not(tmp_path, fake_nastran):
    deck = write_deck(tmp_path)
    found = status(tmp_path, input_file=str(deck), path=str(fake_nastran.bin))
    assert found.command == "nastran job.bdf batch=no old=no scr=yes"
    assert (found.command_from, found.path_from) == ("default", "block")
    assert found.program and found.program.found and found.deck_exists
    gone = status(tmp_path, input_file=str(deck), command="definitely_missing {input}")
    assert (
        gone.program and gone.program.found is None and gone.program.program == "definitely_missing"
    )
    compound = status(tmp_path, input_file=str(deck), command="cd {folder} && nastran {input}")
    assert compound.program and not compound.program.checked


def test_input_lines(tmp_path, fake_nastran):
    deck = write_deck(tmp_path, include="loads.bdf")
    (tmp_path / "loads.bdf").write_text("FORCE,1\n")
    result = status(tmp_path, input_file=str(deck))
    assert result.includes == 1 and result.hdf5_request is True and result.modified
    plain = write_deck(tmp_path, "plain", hdf5=False)
    assert status(tmp_path, input_file=str(plain)).hdf5_request is False


def test_relation_of_the_results_to_the_input(tmp_path):
    deck = write_deck(tmp_path)
    stored = {"input_file": str(deck)}
    first = analysis_status(stored, tmp_path)
    assert first.relation == "no results yet" and [r.exists for r in first.results] == [
        False,
        False,
    ]
    shutil.copyfile(FEM_DIR / "global_loads.f06", deck.with_suffix(".f06"))
    assert analysis_status(stored, tmp_path).relation == "result file(s) missing"
    shutil.copyfile(FEM_DIR / "global_loads.h5", deck.with_suffix(".h5"))
    stamp(deck, NOW)
    for suffix in (".f06", ".h5"):
        stamp(deck.with_suffix(suffix), NOW + 60)
    newer = analysis_status(stored, tmp_path)
    assert newer.relation == "made outside the tool, newer than the input (will be used)"
    assert [r.exists for r in newer.results] == [True, True] and newer.results[0].modified
    stamp(deck, NOW + 120)
    assert analysis_status(stored, tmp_path).relation == (
        "made outside the tool, older than the input (will be replaced)"
    )
    deck.with_suffix(".f06").write_text("cut off")
    assert "incomplete" in analysis_status(stored, tmp_path).relation


def test_relation_with_a_run_record(tmp_path, fake_nastran: FakeNastran):
    from apb.engine import RunContext

    deck = write_deck(tmp_path)
    ctx = RunContext(block_id="n", pipeline_dir=tmp_path, output_dir=tmp_path)
    params = NastranRun.Params.model_validate(
        {"input_file": str(deck), "path": str(fake_nastran.bin)}
    )
    NastranRun().run(params, {}, ctx)
    stored = {"input_file": str(deck)}
    assert analysis_status(stored, tmp_path).relation == "made by this tool, input unchanged"
    deck.write_text(deck.read_text() + "$ edited\n")
    assert analysis_status(stored, tmp_path).relation == "made by this tool, input changed since"
    from apb.blocks.solvers.record import RUNNING, read_record, record_path, write_record

    record = read_record(record_path(deck))
    assert record is not None
    record.status = RUNNING
    write_record(record_path(deck), record)
    assert (
        analysis_status(stored, tmp_path).relation
        == "made by this tool, but the run did not finish"
    )


def test_variables_are_resolved(tmp_path):
    deck = write_deck(tmp_path)
    resolved = analysis_status(
        {"input_file": "${deck}"},
        tmp_path,
        lambda value: {"input_file": str(deck)} if isinstance(value, dict) else value,
    )
    assert resolved.deck_exists and resolved.deck == deck.resolve()
