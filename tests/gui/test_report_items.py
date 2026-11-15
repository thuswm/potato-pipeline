"""Item names of the report block in the editor state (spec 05 §3.4, REP-15)."""

from __future__ import annotations

import pytest

from apb.engine import default_registry
from apb.engine.types import PortType
from apb.gui.editor import ConnectionKey, EditError, EditorState

PORTS_OUT = [
    {"name": "margins", "type": "Table"},
    {"name": "min_ms", "type": "Scalar"},
]


def make_state() -> EditorState:
    state = EditorState(default_registry())
    p = state.pipeline
    p.add_block("table.read_csv", block_id="csv", label="Locations")
    for block_id, label in (("g", "Global loads"), ("l", "Local loads")):
        p.add_block(
            "python.script",
            block_id=block_id,
            label=label,
            script=f"{block_id}.py",
            inputs=[{"name": "table", "type": "Table"}],
            outputs=PORTS_OUT,
        )
        p.connect("csv.table", f"{block_id}.table")
    p.add_block("check.requirement", block_id="chk", label="Frequency check", limit=1.0)
    p.add_block("report.word", block_id="rep", output_file="r.docx")
    return state


def link(state: EditorState, source: str, port: str) -> ConnectionKey:
    key = ConnectionKey(source, port, "rep", "items")
    state.connect(key)
    return key


def test_item_names_types_and_sources():
    state = make_state()
    link(state, "csv", "table")
    link(state, "g", "margins")
    link(state, "g", "min_ms")
    infos = state.item_infos("rep")
    assert [(i.name, i.type, i.source, i.explicit) for i in infos] == [
        ("locations", PortType.TABLE, "Locations", False),  # one output: block label
        ("margins", PortType.TABLE, "Global loads (margins)", False),  # several: port name
        ("min_ms", PortType.SCALAR, "Global loads (min_ms)", False),
    ]
    assert infos[1].connection == ("g", "margins", "rep", "items")
    assert state.item_infos("rep", "nothing") == []
    assert state.item_infos("csv") == []


def test_same_names_get_suffixes_until_the_connector_is_named():
    state = make_state()
    first = link(state, "g", "margins")
    second = link(state, "l", "margins")
    assert [i.name for i in state.item_infos("rep")] == ["margins", "margins_2"]

    state.dirty = False
    assert state.rename_connector(second, "local_margins") == {"margins_2": "local_margins"}
    assert state.dirty
    infos = state.item_infos("rep")
    assert [(i.name, i.explicit) for i in infos] == [("margins", False), ("local_margins", True)]
    assert state.rename_connector(first, " global_margins ") == {"margins": "global_margins"}
    assert [i.name for i in state.item_infos("rep")] == ["global_margins", "local_margins"]
    # The name is stored on the connection, so it survives saving.
    stored = {c.source.block: c.name for c in state.pipeline.connections if c.target.block == "rep"}
    assert stored == {"g": "global_margins", "l": "local_margins"}


def test_clearing_the_name_goes_back_to_the_derived_name():
    state = make_state()
    link(state, "g", "margins")
    second = link(state, "l", "margins")
    state.rename_connector(second, "local_margins")
    assert state.rename_connector(second, "") == {"local_margins": "margins_2"}
    assert state.item_infos("rep")[1].explicit is False


def test_names_must_be_valid_and_unique():
    state = make_state()
    first = link(state, "g", "margins")
    second = link(state, "l", "margins")
    for bad in ("2fast", "_x", "a b", "m-1", "é"):
        with pytest.raises(EditError, match="letters, digits and _"):
            state.rename_connector(first, bad)
    state.rename_connector(second, "local_margins")
    state.rename_connector(first, "global_margins")
    with pytest.raises(EditError, match="already used by another item"):
        state.rename_connector(second, "global_margins")
    third = link(state, "g", "min_ms")
    with pytest.raises(EditError, match="already used"):
        state.rename_connector(third, "local_margins")
    with pytest.raises(EditError, match="does not exist"):
        state.rename_connector(ConnectionKey("g", "nope", "rep", "items"), "x")
    # Nothing changed by the refused renames.
    assert [i.name for i in state.item_infos("rep")] == [
        "global_margins",
        "local_margins",
        "min_ms",
    ]


def test_naming_the_first_of_two_derived_names_renames_the_second_too():
    state = make_state()
    first = link(state, "g", "margins")
    link(state, "l", "margins")  # derived name margins_2
    assert state.rename_connector(first, "global_margins") == {
        "margins": "global_margins",
        "margins_2": "margins",
    }
    assert [i.name for i in state.item_infos("rep")] == ["global_margins", "margins"]


def test_names_can_swap():
    state = make_state()
    first = link(state, "g", "margins")
    link(state, "l", "margins")
    changes = state.rename_connector(first, "margins_2")
    assert changes == {"margins": "margins_2", "margins_2": "margins"}
    assert [i.name for i in state.item_infos("rep")] == ["margins_2", "margins"]


def test_unchanged_name_does_not_mark_the_pipeline_dirty():
    state = make_state()
    key = link(state, "g", "margins")
    state.rename_connector(key, "global_margins")
    state.dirty = False
    assert state.rename_connector(key, "global_margins") == {}
    assert not state.dirty
