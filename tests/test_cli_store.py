"""Stored outputs on the command line (spec 08 §3.10)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from apb.cli import app
from apb.engine import PipelineModel, save_pipeline

runner = CliRunner()


@pytest.fixture
def pipeline(tmp_path) -> Path:
    (tmp_path / "data.csv").write_text("A\n3\n1\n2\n", encoding="utf-8")
    p = PipelineModel()
    p.variables["file"] = "data.csv"
    p.add_block("table.read_csv", block_id="csv", label="Data", file="${file}")
    p.add_block("table.sort", block_id="sort", label="Sorted", by=["A"])
    p.add_block("table.write_excel", block_id="xlsx", label="Workbook", file="out/book.xlsx")
    p.connect("csv.table", "sort.table")
    p.connect("sort.table", "xlsx.tables")
    path = tmp_path / "p.apb.json"
    save_pipeline(p, path)
    return path


def up_to_date(output: str) -> list[str]:
    return [line.split(":")[0].lstrip("✔ ") for line in output.splitlines() if "Up to date" in line]


def test_second_run_reuses_the_stored_results(pipeline):
    first = runner.invoke(app, ["run", str(pipeline)])
    assert first.exit_code == 0 and up_to_date(first.output) == []
    assert (pipeline.parent / ".apb" / "p" / "sort" / "meta.json").is_file()
    second = runner.invoke(app, ["run", str(pipeline)])
    assert second.exit_code == 0
    assert up_to_date(second.output) == ["Data", "Sorted"]  # the workbook is always written
    assert "Done in" in second.output.split("Workbook")[-1]


def test_no_cache_recomputes_everything(pipeline):
    runner.invoke(app, ["run", str(pipeline)])
    result = runner.invoke(app, ["run", str(pipeline), "--no-cache"])
    assert result.exit_code == 0 and up_to_date(result.output) == []


def test_a_changed_input_is_recomputed(pipeline):
    runner.invoke(app, ["run", str(pipeline)])
    (pipeline.parent / "data.csv").write_text("A\n9\n8\n", encoding="utf-8")
    result = runner.invoke(app, ["run", str(pipeline)])
    assert result.exit_code == 0 and up_to_date(result.output) == []


def test_runs_with_overrides_do_not_touch_the_store(pipeline, tmp_path):
    (tmp_path / "other.csv").write_text("A\n5\n", encoding="utf-8")
    result = runner.invoke(app, ["run", str(pipeline), "--set", "file=other.csv"])
    assert result.exit_code == 0
    assert not (tmp_path / ".apb").exists()  # the results belong to a modified pipeline
    runner.invoke(app, ["run", str(pipeline)])
    result = runner.invoke(app, ["run", str(pipeline), "--set", "file=other.csv"])
    assert up_to_date(result.output) == []  # and the stored ones are not used for it


def test_broken_entries_are_reported_and_recomputed(pipeline):
    runner.invoke(app, ["run", str(pipeline)])
    (pipeline.parent / ".apb" / "p" / "csv" / "meta.json").write_text("{ no", encoding="utf-8")
    result = runner.invoke(app, ["run", str(pipeline)])
    assert result.exit_code == 0
    assert "Stored output of Data ignored: meta.json cannot be read" in result.output
    assert up_to_date(result.output) == ["Sorted"]  # only the damaged entry is recomputed


def test_outputs_lists_the_entries(pipeline):
    before = runner.invoke(app, ["outputs", str(pipeline)])
    assert before.exit_code == 0 and "csv              idle" in before.output
    runner.invoke(app, ["run", str(pipeline)])
    out = runner.invoke(app, ["outputs", str(pipeline)])
    assert out.exit_code == 0
    assert "Store: " in out.output and "/.apb/p" in out.output.replace("\\", "/")
    lines = {line.split()[0]: line for line in out.output.splitlines()[1:]}
    assert lines["csv"].split()[1] == "done" and "Data" in lines["csv"]
    assert lines["xlsx"].split()[1] == "done"
    (pipeline.parent / "out" / "book.xlsx").unlink()
    assert runner.invoke(app, ["outputs", str(pipeline)]).output.count("stale") == 1


def test_clear_outputs(pipeline):
    runner.invoke(app, ["run", str(pipeline)])
    one = runner.invoke(app, ["clear-outputs", str(pipeline), "--block", "sort"])
    assert one.exit_code == 0 and "Cleared 2 block(s): sort, xlsx" in one.output
    assert runner.invoke(app, ["outputs", str(pipeline)]).output.count("idle") == 2
    everything = runner.invoke(app, ["clear-outputs", str(pipeline)])
    assert "Cleared 1 block(s): csv" in everything.output
    assert not (pipeline.parent / ".apb").exists()
    nothing = runner.invoke(app, ["clear-outputs", str(pipeline)])
    assert nothing.exit_code == 0 and "nothing was stored" in nothing.output
    assert (pipeline.parent / "out" / "book.xlsx").exists()  # written files are never touched


def test_clear_outputs_unknown_block(pipeline):
    result = runner.invoke(app, ["clear-outputs", str(pipeline), "--block", "nope"])
    assert result.exit_code == 2 and "No such block: nope" in result.output
