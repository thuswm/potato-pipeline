"""Editing rules of the Constants block in the GUI (spec 03 §3.5.3, CON-10)."""

from __future__ import annotations

import pytest

from apb.engine import default_registry
from apb.engine.model import PortRef
from apb.gui.constants_panel import DraftRow, parse_value, row_problem, to_stored
from apb.gui.editor import ConnectionKey, EditError, EditorState

SF = {"name": "SF", "value": 1.5}
F_MIN = {"name": "f_min", "value": 20.0, "unit": "Hz"}


@pytest.fixture
def state() -> EditorState:
    """Constants -> requirement limit (port), report items (port), compute (${SF})."""
    s = EditorState(default_registry())
    p = s.pipeline
    p.add_block(
        "table.constants", block_id="const", label="Project constants", constants=[SF, F_MIN]
    )
    p.add_block("table.constant", block_id="f1", value=25.7, unit="Hz")
    p.add_block(
        "check.requirement",
        block_id="req",
        label="Frequency requirement",
        comparison="greater_than",
    )
    p.add_block("table.read_csv", block_id="csv", file="x.csv")
    p.add_block(
        "table.compute",
        block_id="design",
        label="Design loads",
        new_column="FZ_D",
        expression="FZ * ${SF} + $${SF}",
    )
    p.add_block(
        "report.word",
        block_id="rep",
        label="Report",
        output_file="r.docx",
        sections=[{"kind": "paragraph", "text": "Safety factor {SF:.2f}, limit {f_min}."}],
    )
    p.connect("f1.value", "req.value")
    p.connect("const.f_min", "req.limit")
    p.connect("csv.table", "design.table")
    p.connect("const.SF", "rep.items")
    p.connect("const.f_min", "rep.items")
    return s


def test_ports_of_the_block(state: EditorState):
    _, outputs = state.ports(state.pipeline.block("const"))
    assert [p.name for p in outputs] == ["table", "SF", "f_min"]


def test_variables_include_constants(state: EditorState):
    state.pipeline.variables["doc"] = "A-1"
    assert state.variables() == {"doc": "A-1", "SF": 1.5, "f_min": 20.0}


def test_users_of_a_constant(state: EditorState):
    assert state.constant_users("const", "SF") == ["Design loads (${SF})", "Report (items)"]
    assert state.constant_users("const", "f_min") == [
        "Frequency requirement (limit)",
        "Report (items)",
    ]


def test_names_outside(state: EditorState):
    state.pipeline.variables["doc"] = "A-1"
    state.pipeline.add_block(
        "table.constants", block_id="more", constants=[{"name": "k", "value": 1}]
    )
    assert state.names_outside("const") == {"doc", "k"}
    assert state.names_outside("more") == {"doc", "SF", "f_min"}


def test_rename_moves_connectors_references_and_report_items(state: EditorState):
    changes = state.rename_constant("const", "SF", "SF_ult")
    p = state.pipeline
    assert [r["name"] for r in p.block("const").params["constants"]] == ["SF_ult", "f_min"]
    assert any(c.source == PortRef(block="const", port="SF_ult") for c in p.connections)
    assert not any(c.source.port == "SF" for c in p.connections)
    # ${SF} follows, the escaped literal $${SF} does not.
    assert p.block("design").params["expression"] == "FZ * ${SF_ult} + $${SF}"
    # The report item had a derived name: the text follows; the other item is untouched.
    text = p.block("rep").params["sections"][0]["text"]
    assert text == "Safety factor {SF_ult:.2f}, limit {f_min}."
    assert len(changes) == 3
    assert state.dirty


def test_rename_with_single_and_multiple_inputs(state: EditorState):
    state.pipeline.add_block("table.constant", block_id="x", value=1.0)
    state.pipeline.add_block("check.requirement", block_id="req2", comparison="less_than")
    state.pipeline.connect("x.value", "req2.value")
    state.pipeline.connect("const.SF", "req2.limit")  # a single input next to report items
    state.rename_constant("const", "SF", "SF_ult")
    sources = {(c.source.port, c.target.block) for c in state.pipeline.connections}
    assert ("SF_ult", "req2") in sources and ("SF_ult", "rep") in sources


def test_rename_keeps_an_explicit_item_name(state: EditorState):
    state.rename_connector(ConnectionKey("const", "SF", "rep", "items"), "safety")
    state.pipeline.block("rep").params["sections"][0]["text"] = "SF {safety}"
    state.rename_constant("const", "SF", "SF_ult")
    assert state.pipeline.block("rep").params["sections"][0]["text"] == "SF {safety}"


def test_rename_refuses_bad_or_taken_names(state: EditorState):
    with pytest.raises(EditError, match="letters"):
        state.rename_constant("const", "SF", "S F")
    with pytest.raises(EditError, match="already used"):
        state.rename_constant("const", "SF", "f_min")
    state.pipeline.variables["doc"] = "A"
    with pytest.raises(EditError, match="already used"):
        state.rename_constant("const", "SF", "doc")
    with pytest.raises(EditError, match="no constant"):
        state.rename_constant("const", "nope", "x")
    assert state.rename_constant("const", "SF", "SF") == []


def test_removing_a_constant_prunes_its_connectors(state: EditorState):
    removed = state.set_param("const", "constants", [SF])
    assert {(c.target.block, c.target.port) for c in removed} == {
        ("req", "limit"),
        ("rep", "items"),
    }


# --------------------------------------------------------------------------------------
# Panel helpers
# --------------------------------------------------------------------------------------


def test_parse_value():
    assert parse_value("1.5") == 1.5
    assert parse_value(" 2e5 ") == 2e5
    assert parse_value("1,25") == 1.25
    assert parse_value("abc") is None
    assert parse_value("nan") is None
    assert parse_value("") is None


def test_row_problem():
    ok = DraftRow("SF", "1.5")
    assert row_problem(ok, [], set()) == ""
    assert "letters" in row_problem(DraftRow("S F", "1"), [], set())
    assert "already used" in row_problem(ok, [DraftRow("SF", "2")], set())
    assert "already used" in row_problem(ok, [], {"SF"})
    assert "number" in row_problem(DraftRow("SF", "x"), [], set())
    assert "Unknown unit" in row_problem(DraftRow("SF", "1", "furlong"), [], set())


def test_draft_round_trip():
    stored = {"name": "f_min", "value": 20.0, "unit": "Hz", "description": "REQ"}
    draft = DraftRow.from_stored(stored)
    assert (draft.value, draft.stored) == ("20", "f_min")
    assert to_stored(draft) == stored
    assert to_stored(DraftRow("SF", "1.5")) == {"name": "SF", "value": 1.5}
