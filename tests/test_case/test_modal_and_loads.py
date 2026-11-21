"""Acceptance test of the concrete test case (spec 07): real Nastran files to report.

Runs ``examples/test_case.apb.json`` headless on the files in ``examples/fem`` and checks
the numbers of spec 07 §5, the Excel workbook and the Word report.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from docx import Document

from apb.engine import Executor, default_registry, load_pipeline

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
PIPELINE = EXAMPLES / "test_case.apb.json"
HIGHLIGHT = "FFC7CE"


def run(tmp_path: Path, **overrides: float):
    """Run the test case; ``overrides`` change constants like ``apb run --set`` (CON-09)."""
    pipeline = load_pipeline(PIPELINE)
    executor = Executor(default_registry())
    result = executor.run(
        pipeline, EXAMPLES, output_dir=tmp_path, pipeline_file=PIPELINE, overrides=overrides
    )
    return pipeline, executor, result


@pytest.fixture(scope="module")
def outcome(tmp_path_factory):
    out = tmp_path_factory.mktemp("test_case")
    return out, run(out)[2]


def test_pipeline_file_is_what_the_generator_writes():
    spec = importlib.util.spec_from_file_location(
        "make_test_case", EXAMPLES.parent / "tools" / "make_test_case.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert PIPELINE.read_text(encoding="utf-8") == module.build().to_json()


def test_whole_pipeline_runs(outcome):
    _, result = outcome
    assert result.ok
    assert len(result.results) == 15


def test_the_analyses_check_their_f06_files(outcome):
    _, result = outcome
    assert [result.output(b, "status") for b in ("modal_run", "global_run", "local_run")] == [
        "OK",
        "OK",
        "WARNINGS",
    ]
    assert result.output("local_run", "h5").endswith("fem/local_loads.h5")
    assert result.output("modal_run", "messages").empty
    messages = result.output("msgs", "table")
    assert len(messages) == 1  # the 9058 warning of the local loads, nothing else
    row = messages.iloc[0]
    assert (row["SOURCE"], row["SEVERITY"], row["NUMBER"], row["MODULE"]) == (
        "local_loads.f06",
        "WARNING",
        9058,
        "SUBDMAP SESTATIC",
    )
    assert "APPLIED LOADS" in row["TEXT"]
    log = result.results["local_run"].log
    assert any(line.startswith("WARNING: 9058 (SUBDMAP SESTATIC)") for line in log)
    assert not any(line.startswith("WARNING") for line in result.results["global_run"].log)


def test_first_eigenfrequency_requirement(outcome):
    _, result = outcome
    assert result.output("f1", "value").value == pytest.approx(25.7119, abs=1e-4)
    assert result.output("req", "status") == "PASS"
    assert result.output("req", "margin") == pytest.approx(0.2856, abs=1e-4)
    row = result.output("req", "result").iloc[0]
    assert (row["REQUIREMENT"], row["CONDITION"], row["LIMIT"]) == (
        "REQ-DYN-001 First eigenfrequency",
        ">",
        20.0,
    )
    assert result.output("req", "result").attrs["units"]["VALUE"] == "Hz"
    assert "limit 20 interpreted as Hz" not in result.results["req"].log  # the limit has a unit


EXPECTED = {  # (EID, location) -> margin of safety for the global loads (spec 07 §5)
    (17, "Joint A (M6 steel)"): 2.2,
    (17, "Joint B (M5 aluminium)"): 0.4667,
    (18, "Joint A (M6 steel)"): 4.3333,
    (18, "Joint B (M5 aluminium)"): 1.4444,
    (19, "Joint A (M6 steel)"): 15.0,
    (19, "Joint B (M5 aluminium)"): 6.3333,
}


def test_global_margins_per_element_and_location(outcome):
    _, result = outcome
    table: pd.DataFrame = result.output("global_margins", "margins")
    assert list(table.columns) == ["SUBCASE", "EID", "LOCATION", "TENSION", "SHEAR", "MS"]
    assert len(table) == 6  # 1 subcase x 3 elements x 2 locations
    got = {(int(r.EID), r.LOCATION): r.MS for r in table.itertuples()}
    assert got == pytest.approx(EXPECTED, abs=1e-4)
    assert table["TENSION"].tolist() == [2500.0, 2500.0, 1500.0, 1500.0, 500.0, 500.0]
    assert table["SHEAR"].abs().max() < 1e-9  # numerical noise (2e-14 N) in the file
    assert table.attrs["units"] == {"TENSION": "N", "SHEAR": "N", "MS": "-"}
    assert result.output("global_margins", "min_ms") == pytest.approx(0.4667, abs=1e-4)
    assert "Minimum margin 0.467 at element 17, Joint B (M5 aluminium), subcase 1" in (
        result.results["global_margins"].log
    )


def test_local_loads_are_zero_and_have_no_margins(outcome):
    _, result = outcome
    table = result.output("local_margins", "margins")
    assert len(table) == 8  # 1 subcase x 4 elements x 2 locations
    assert sorted(table["EID"].unique()) == [2, 3, 4, 5]
    assert (table["TENSION"] == 0).all() and (table["SHEAR"] == 0).all()
    assert table["MS"].isna().all()
    assert math.isnan(result.output("local_margins", "min_ms"))
    assert "All loads are zero: no margin of safety is defined" in (
        result.results["local_margins"].log
    )


def test_excel_workbook(outcome):
    out, _ = outcome
    book = openpyxl.load_workbook(out / "output" / "test_case_modal.xlsx")
    assert book.sheetnames == ["Effective mass"]
    sheet = book["Effective mass"]
    rows = list(sheet.values)
    assert len(rows) == 11  # header + 10 modes
    assert "FREQUENCY [Hz]" in rows[0] and "T1 [kg]" in rows[0] and "R1 [kg·m²]" in rows[0]
    freq = rows[0].index("FREQUENCY [Hz]")
    assert rows[1][freq] == pytest.approx(25.7119, abs=1e-4)


def paragraphs(doc) -> list[str]:
    return [p.text for p in doc.paragraphs]


def table_text(table) -> list[list[str]]:
    return [[c.text for c in row.cells] for row in table.rows]


def shaded(table) -> int:
    xml = table._tbl.xml
    return xml.count(f'w:fill="{HIGHLIGHT}"')


def test_word_report(outcome):
    out, _ = outcome
    doc = Document(str(out / "output" / "test_case_report.docx"))
    text = paragraphs(doc)
    assert "The first eigenfrequency is 25.71 Hz: the requirement is PASS." in text
    assert "Minimum margin of safety: 0.47." in text
    assert any(
        "higher than 20.0 Hz" in t and "factor on the CBUSH loads is 1.50" in t for t in text
    )
    assert any("Unit system SI" in t for t in text)
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert headings == [
        "1 Introduction",
        "2 Modal analysis",
        "3 Global loads",
        "4 Local loads",
        "5 Analysis checks",
        "Traceability",
    ]

    constants = table_text(doc.tables[1])
    assert constants[0] == ["NAME", "VALUE", "UNIT", "DESCRIPTION"]
    assert [row[:3] for row in constants[1:]] == [["f_min", "20", "Hz"], ["SF", "1.5", ""]]
    requirement, modal, global_margins, local_margins = (table_text(t) for t in doc.tables[2:6])
    assert requirement[0] == [
        "REQUIREMENT",
        "VALUE [Hz]",
        "CONDITION",
        "LIMIT [Hz]",
        "MARGIN",
        "STATUS",
    ]
    assert requirement[1][1:] == ["25.71", ">", "20.0", "0.29", "PASS"]
    assert len(modal) == 11 and modal[0][1] == "FREQUENCY [Hz]"
    assert modal[1][:2] == ["1", "25.71"]
    assert len(global_margins) == 7
    assert global_margins[1] == ["1", "17", "Joint A (M6 steel)", "2500", "0", "2.20"]
    assert global_margins[6][3:] == ["500", "0", "6.33"]
    assert len(local_margins) == 9
    assert local_margins[1][3:] == ["0", "0", ""]  # no margin: empty cell, not "nan"
    assert all(shaded(t) == 0 for t in doc.tables[2:6])  # nothing fails
    messages = table_text(doc.tables[6])
    assert messages[0] == ["SOURCE", "SEVERITY", "NUMBER", "MODULE", "TEXT"]
    assert messages[1][:4] == ["local_loads.f06", "WARNING", "9058", "SUBDMAP SESTATIC"]
    assert len(messages) == 2 and len(doc.tables) == 7

    trace = " ".join(text)
    for needle in (
        "fem/modal.h5",
        "fem/global_loads.h5",
        "fem/local_loads.h5",
        "fem/modal.f06",
        "fem/local_loads.bdf",
        "msc20233",
    ):
        assert needle in trace, needle
    assert "scripts/cbush_margins.py" in trace and "test_case/locations.csv" in trace


def test_a_failing_requirement_is_reported_not_hidden(tmp_path):
    _, _, result = run(tmp_path, f_min=30.0)
    assert result.ok  # a failed requirement does not stop the pipeline
    assert result.output("req", "status") == "FAIL"
    assert result.output("req", "margin") == pytest.approx(25.7119 / 30 - 1, abs=1e-4)
    log = result.results["req"].log
    assert any(line.startswith("WARNING: requirement not met") for line in log)
    doc = Document(str(tmp_path / "output" / "test_case_report.docx"))
    assert "The first eigenfrequency is 25.71 Hz: the requirement is FAIL." in paragraphs(doc)
    requirement = doc.tables[2]
    assert table_text(requirement)[1][-1] == "FAIL"
    assert shaded(requirement) > 0  # the row of the failed requirement is highlighted


def test_second_run_takes_unchanged_blocks_from_the_cache(tmp_path):
    pipeline, executor, first = run(tmp_path)
    assert first.ok and not any(r.cached for r in first.results.values())
    second = executor.run(pipeline, EXAMPLES, output_dir=tmp_path, pipeline_file=PIPELINE)
    assert second.ok
    cached = {b for b, r in second.results.items() if r.cached}
    assert cached == {
        "modal_run",
        "global_run",
        "local_run",
        "msgs",
        "modal",
        "f1",
        "const",
        "req",
        "locs",
        "global_forces",
        "local_forces",
        "global_margins",
        "local_margins",
    }  # the report and the workbook always run


def test_the_run_records_of_the_example_decks_still_match():
    from apb.blocks.solvers.fingerprint import fingerprint
    from apb.blocks.solvers.record import read_record, record_path

    for job in ("modal", "global_loads", "local_loads"):
        deck = EXAMPLES / "fem" / f"{job}.bdf"
        record = read_record(record_path(deck))
        assert record is not None and record.complete
        assert record.input_hash == fingerprint([deck], deck.parent)[0], job


def test_no_analysis_warns_about_file_times(outcome):
    """With a run record the result does not depend on when the files were copied."""
    _, result = outcome
    for block in ("modal_run", "global_run"):
        assert not any(line.startswith("WARNING") for line in result.results[block].log), block
