from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pytest
from docx import Document
from pydantic import TypeAdapter, ValidationError

from apb.blocks.report.block import WordReport, referenced_items
from apb.blocks.report.sections import FormatError, Section, fill_text, format_value, python_format
from apb.blocks.report.writer import ReportBuilder, ReportError
from apb.engine import Executor, PipelineModel, default_registry, load_pipeline, prepare
from apb.engine.graph import python_name
from apb.fileutil import OutputFileError
from apb.units import Quantity, UnitSystem, set_units
from tests.conftest import DATA_DIR, PLATE_H5, run_block

SECTIONS = TypeAdapter(list[Section])


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("0.00", ".2f"),
        ("0", ".0f"),
        ("#,##0.0", ",.1f"),
        ("0.0E+00", ".1E"),
        ("0.0%", ".1%"),
        (".3e", ".3e"),
        ("auto", ".4g"),
    ],
)
def test_python_format(spec, expected):
    assert python_format(spec) == expected


def test_bad_format():
    with pytest.raises(FormatError):
        python_format("0.0x")


def test_format_value():
    assert format_value(1001, "0.0") == "1001.0"
    assert format_value(1001) == "1001"
    assert format_value(float("nan"), "0.00") == ""
    assert format_value(12345.678, "#,##0.0") == "12,345.7"
    assert format_value("Pull-off", "0.00") == "Pull-off"


def test_fill_text():
    values = {"allowable": Quantity(410.0, "MPa"), "ms": 0.234, "name": "rib"}
    assert (
        fill_text("A {allowable:.1f}, MS {ms:.2f} {{x}} {name}", values)
        == "A 410.0 MPa, MS 0.23 {x} rib"
    )
    with pytest.raises(FormatError, match="Unknown placeholder"):
        fill_text("{nope}", values)
    with pytest.raises(FormatError, match="not allowed"):
        fill_text("{name.__class__}", values)


def test_section_validation():
    with pytest.raises(ValidationError):
        SECTIONS.validate_python([{"kind": "table", "item": "t", "formats": {"A": "bad%%"}}])
    with pytest.raises(ValidationError):
        SECTIONS.validate_python([{"kind": "heading", "text": "x", "level": 5}])
    with pytest.raises(ValidationError):
        SECTIONS.validate_python([{"kind": "chart"}])


def items() -> dict:
    margins = pd.DataFrame({"EID": [1, 2, 3], "MS": [0.5, -0.1, 0.25], "F": [10.0, 20.0, 30.0]})
    set_units(margins, UnitSystem.SI_MM, {"F": "N"})
    wide = pd.DataFrame(
        [[1, 1.0, 2.0, "LC2", 3.0, 4.0, "LC2"]],
        columns=pd.MultiIndex.from_tuples(
            [
                ("EID", ""),
                ("T", "LC1"),
                ("T", "LC2"),
                ("T", "MAX_CASE"),
                ("S", "LC1"),
                ("S", "LC2"),
                ("S", "MAX_CASE"),
            ]
        ),
    )
    set_units(wide, UnitSystem.SI_MM, {"T": "N", "S": "N"})
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    return {
        "margins": margins,
        "wide": wide,
        "plot": fig,
        "min_ms": -0.1,
        "allowable": Quantity(410.0, "MPa"),
        "note": "All good",
    }


def build(sections: list[dict], **kw) -> Document:
    builder = ReportBuilder(
        items(),
        {
            "title": "My report",
            "document_number": "D-1",
            "revision": "B",
            "author": "me",
            "date": "2026-01-01",
        },
        **kw,
    )
    return builder.build(SECTIONS.validate_python(sections))


def table_text(table) -> list[list[str]]:
    return [[c.text for c in row.cells] for row in table.rows]


def test_full_report_content():
    doc = build(
        [
            {"kind": "title"},
            {"kind": "heading", "text": "1 Results for {title}", "level": 1},
            {"kind": "paragraph", "text": "Allowable {allowable:.0f}, min MS {min_ms:.2f}. {note}"},
            {"kind": "value", "item": "allowable", "label": "Allowable", "format": "0.0"},
            {
                "kind": "table",
                "item": "margins",
                "caption": "Margins",
                "columns": ["EID", "F", "MS"],
                "formats": {"MS": "0.00"},
                "highlight": "MS < 0",
            },
            {"kind": "table", "item": "wide", "caption": "Loads"},
            {"kind": "figure", "item": "plot", "caption": "Plot", "width_cm": 10},
            {"kind": "page_break"},
            {"kind": "units"},
        ]
    )
    texts = [p.text for p in doc.paragraphs]
    assert "1 Results for My report" in texts
    assert "Allowable 410 MPa, min MS -0.10. All good" in texts
    assert "Allowable: 410.0 MPa" in texts
    assert any(t.startswith("Table") and t.endswith(": Margins") for t in texts)
    assert any(t.startswith("Figure") and t.endswith(": Plot") for t in texts)
    assert any("SEQ Table" in p._p.xml for p in doc.paragraphs)
    assert (
        "Unit system SI_mm: lengths in mm, forces in N, moments in N·mm, stresses in MPa, "
        "masses in t." in texts
    )
    assert doc.sections[0].header.paragraphs[0].text.startswith("My report")
    assert "D-1  Rev. B" in doc.sections[0].header.paragraphs[0].text

    title_table, margins, wide = doc.tables
    assert table_text(title_table)[0] == ["Document", "D-1"]
    assert table_text(margins) == [
        ["EID", "F [N]", "MS"],
        ["1", "10", "0.50"],
        ["2", "20", "-0.10"],
        ["3", "30", "0.25"],
    ]
    shaded = ['w:fill="FFC7CE"' in c._tc.xml for c in margins.rows[2].cells]
    assert all(shaded) and not any('w:fill="FFC7CE"' in c._tc.xml for c in margins.rows[1].cells)
    rows = table_text(wide)
    assert rows[0][:4] == ["EID", "T [N]", "T [N]", "T [N]"]  # merged cells repeat their text
    assert rows[1][1:4] == ["LC1", "LC2", "MAX_CASE"]
    assert rows[2] == ["1", "1", "2", "LC2", "3", "4", "LC2"]
    assert len(doc.inline_shapes) == 1


def test_max_rows_and_errors():
    doc = build([{"kind": "table", "item": "margins", "max_rows": 2}])
    assert len(doc.tables[0].rows) == 3
    assert "(First 2 of 3 rows shown.)" in [p.text for p in doc.paragraphs]
    with pytest.raises(ReportError, match=r"No connected item 'x'\. Available: allowable"):
        build([{"kind": "table", "item": "x"}])
    with pytest.raises(ReportError, match="not a table"):
        build([{"kind": "table", "item": "plot"}])
    with pytest.raises(ReportError, match=r"\['NOPE'\] not in item"):
        build([{"kind": "table", "item": "margins", "columns": ["NOPE"]}])
    with pytest.raises(ReportError, match="clash"):
        ReportBuilder({"title": 1.0}, {"title": "x"})


def test_empty_report_is_valid(tmp_path):
    builder = ReportBuilder({}, {})
    builder.build([])
    builder.save(tmp_path / "e.docx")
    assert Document(str(tmp_path / "e.docx")).paragraphs


def test_custom_base_document(tmp_path):
    base = Document()
    base.add_paragraph("Front page: {title} / {document_number} / {unknown}")
    base.save(str(tmp_path / "base.docx"))
    doc = build([{"kind": "paragraph", "text": "Body"}], base_document=tmp_path / "base.docx")
    assert [p.text for p in doc.paragraphs][:2] == [
        "Front page: My report / D-1 / {unknown}",
        "Body",
    ]


def test_referenced_items():
    sections = SECTIONS.validate_python(
        [
            {"kind": "table", "item": "a"},
            {"kind": "paragraph", "text": "{b:.1f} and {c}"},
            {"kind": "figure", "item": "d", "caption": "{e}"},
        ]
    )
    assert referenced_items(sections) == {"a", "b", "c", "d", "e"}


def report_pipeline() -> PipelineModel:
    p = PipelineModel()
    p.add_block(
        "nastran.hdf5_eigenvalues", block_id="eig", label="Eigen frequencies", file=str(PLATE_H5)
    )
    p.add_block("table.pick", block_id="f1", column="FREQ", row="first")
    p.add_block("table.constant", block_id="k", value=2.0)
    p.add_block(
        "report.word",
        block_id="rep",
        output_file="out/r.docx",
        sections=[
            {"kind": "table", "item": "eigen_frequencies", "formats": {"FREQ": "0.0"}},
            {"kind": "paragraph", "text": "First frequency {f1:.1f}, factor {k}."},
            {"kind": "traceability"},
        ],
    )
    p.connect("eig.table", "f1.table")
    p.connect("eig.table", "rep.items")
    p.connect("f1.value", "rep.items", name="f1")
    p.connect("k.value", "rep.items", name="k")
    return p


def test_report_block_in_pipeline(tmp_path):
    p = report_pipeline()
    graph = prepare(p, default_registry())
    assert graph.input_names("rep") == {"items": ["eigen_frequencies", "f1", "k"]}
    result = Executor(default_registry()).run(p, tmp_path, pipeline_file=tmp_path / "x.apb.json")
    assert result.ok, result.results["rep"].error
    doc = Document(str(tmp_path / "out" / "r.docx"))
    texts = [p.text for p in doc.paragraphs]
    assert "First frequency 18.4 Hz, factor 2.0." in texts
    assert any("synthetic_plate.h5" in t and "SYNTHETIC TEST DATA" in t for t in texts)
    assert any("built-in test base" in line for line in result.results["rep"].log)


def test_report_block_errors(tmp_path, monkeypatch):
    with pytest.raises(ReportError, match=r"\.docx"):
        run_block(WordReport, tmp=tmp_path, output_file="r.pdf")
    with pytest.raises(ReportError, match="Base document not found"):
        run_block(WordReport, tmp=tmp_path, output_file="r.docx", base_document="nope.docx")

    def locked(self, target):
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "replace", locked)
    with pytest.raises(OutputFileError, match="Close it in Word"):
        run_block(WordReport, tmp=tmp_path, output_file="r.docx")


def test_python_name():
    assert python_name("Fastener load table") == "fastener_load_table"
    assert python_name("3D plot") == "_3d_plot"
    assert python_name("!!!") == "item"


def test_fastener_example_report(tmp_path):
    path = DATA_DIR.parent / "fastener_loads.apb.json"
    result = Executor(default_registry()).run(
        load_pipeline(path), path.parent, output_dir=tmp_path, pipeline_file=path
    )
    assert result.ok
    doc = Document(str(tmp_path / "output" / "fastener_report.docx"))
    texts = [p.text for p in doc.paragraphs]
    assert "The minimum margin of safety is 0.23." in texts
    assert len(doc.tables) == 3 and len(doc.inline_shapes) == 1


def test_mass_in_kg_in_report_tables():
    weight = pd.DataFrame({"DIRECTION": [1], "MASS": [0.0125]})
    set_units(weight, UnitSystem.SI_MM, {"MASS": "t"})
    builder = ReportBuilder({"weight": weight}, {})
    doc = builder.build(
        SECTIONS.validate_python(
            [
                {"kind": "units"},
                {"kind": "table", "item": "weight"},
                {"kind": "table", "item": "weight", "mass_in_kg": False},
            ]
        )
    )
    kg, tonne = doc.tables
    assert table_text(kg)[0] == ["DIRECTION", "MASS [kg]"] and table_text(kg)[1][1] == "12.5"
    assert table_text(tonne)[0] == ["DIRECTION", "MASS [t]"]
    assert any(p.text.endswith("masses in t (kg in the tables).") for p in doc.paragraphs)
