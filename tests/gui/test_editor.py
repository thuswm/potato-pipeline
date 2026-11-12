from __future__ import annotations

import json

import pytest

from apb.engine import default_registry
from apb.gui.editor import ConnectionKey, EditError, EditorState
from tests.conftest import DATA_DIR


@pytest.fixture
def state() -> EditorState:
    return EditorState(default_registry())


def key(a: str, ap: str, b: str, bp: str) -> ConnectionKey:
    return ConnectionKey(a, ap, b, bp)


def test_add_blocks_get_readable_ids_and_labels(state):
    a = state.add_block("table.read_csv", 10, 20)
    b = state.add_block("table.sort", 30, 40)
    assert (a.id, b.id) == ("b1", "b2")
    assert a.label == "Read CSV" and a.position == (10, 20)
    assert state.dirty and state.display_name == "untitled.apb.json *"
    state.remove_block("b1")
    assert state.add_block("table.sort", 0, 0).id == "b3"  # no reuse of a taken id
    with pytest.raises(EditError, match="Unknown block type"):
        state.add_block("nope", 0, 0)


def test_connection_rules(state):
    csv = state.add_block("table.read_csv", 0, 0).id
    sort = state.add_block("table.sort", 0, 0).id
    sort2 = state.add_block("table.sort", 0, 0).id
    const = state.add_block("table.constant", 0, 0).id
    stack = state.add_block("table.concat", 0, 0).id

    state.connect(key(csv, "table", sort, "table"))
    with pytest.raises(EditError, match="already connected; remove"):
        state.connect(key(sort2, "table", sort, "table"))
    with pytest.raises(EditError, match=r"already connected$"):
        state.connect(key(csv, "table", sort, "table"))
    with pytest.raises(EditError, match="Cannot connect a Scalar output to a Table input"):
        state.connect(key(const, "value", sort2, "table"))
    with pytest.raises(EditError, match="itself"):
        state.connect(key(sort, "table", sort, "table"))
    state.connect(key(sort, "table", sort2, "table"))
    state.disconnect(key(csv, "table", sort, "table"))  # free sort's input
    with pytest.raises(EditError, match="loop"):
        state.connect(key(sort2, "table", sort, "table"))
    # Multiple input accepts several connections.
    state.connect(key(csv, "table", stack, "tables"))
    state.connect(key(sort2, "table", stack, "tables"))
    assert len(state.pipeline.connections) == 3
    state.remove_block(sort2)
    assert len(state.pipeline.connections) == 1  # sort->sort2 and sort2->stack removed


def test_any_ports_and_python_ports(state):
    csv = state.add_block("table.read_csv", 0, 0).id
    conv = state.add_block("units.convert", 0, 0).id
    state.connect(key(csv, "table", conv, "value"))  # Table -> Any
    py = state.add_block("python.script", 0, 0)
    assert state.ports(py) == ((), ())  # no valid params yet: no ports
    py.params = {
        "script": "s.py",
        "inputs": [{"name": "x", "type": "Table"}],
        "outputs": [{"name": "y", "type": "Scalar"}],
    }
    assert [p.name for p in state.ports(py)[0]] == ["x"]
    state.connect(key(conv, "value", py.id, "x"))


def test_open_save_roundtrip(state, tmp_path):
    state.open(DATA_DIR.parent / "fastener_loads.apb.json")
    assert not state.dirty and state.display_name == "fastener_loads.apb.json"
    spec = state.graph_spec()
    assert len(spec["nodes"]) == 16 and len(spec["connections"]) == 24
    node = next(n for n in spec["nodes"] if n["id"] == "margins")
    assert [p["name"] for p in node["inputs"]][:2] == ["loads", "allowable_tension"]
    assert node["category"] == "python"

    state.move_block("names", 100, 200)
    assert state.dirty
    out = state.save(tmp_path / "copy")  # suffix added
    assert out.name == "copy.apb.json" and not state.dirty
    data = json.loads(out.read_text(encoding="utf-8"))
    assert next(b for b in data["blocks"] if b["id"] == "names")["position"] == [100, 200]


def test_open_errors_and_save_without_name(state, tmp_path):
    with pytest.raises(EditError, match="Cannot open"):
        state.open(tmp_path / "missing.apb.json")
    bad = tmp_path / "bad.apb.json"
    bad.write_text('{"schema_version": 1, "blocks": [{"type_id": 3}]}', encoding="utf-8")
    with pytest.raises(EditError, match="not a valid pipeline"):
        state.open(bad)
    with pytest.raises(EditError, match="Save as"):
        state.save()


def test_unknown_block_type_is_shown(state):
    state.pipeline.add_block("other.package_block", block_id="x")
    spec = state.node_spec(state.pipeline.block("x"))
    assert spec["category"] == "unknown" and spec["type_title"].startswith("Unknown")


def test_change_listener(state):
    calls = []
    state.on_change(lambda: calls.append(state.display_name))
    state.add_block("table.sort", 0, 0)
    state.new()
    assert calls == ["untitled.apb.json *", "untitled.apb.json"]
