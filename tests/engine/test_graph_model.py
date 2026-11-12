from __future__ import annotations

import json
from typing import Any

import pytest

from apb.engine import Block, BlockRegistry, PipelineModel, load_pipeline, prepare, save_pipeline
from apb.engine.registry import RegistryError
from apb.engine.variables import parse_assignment, referenced_names, resolve
from tests.conftest import Scale, Source


def messages(pipeline: PipelineModel, registry: BlockRegistry) -> list[str]:
    return [i.message for i in prepare(pipeline, registry).issues]


# ---- validation ------------------------------------------------------------------------


def test_valid_pipeline_has_no_issues(registry):
    p = PipelineModel()
    p.add_block("test.source", block_id="a")
    p.add_block("test.total", block_id="b")
    p.connect("a.table", "b.table")
    graph = prepare(p, registry)
    assert graph.issues == []
    assert graph.order == ["a", "b"]


def test_unknown_type_and_missing_ports(registry):
    p = PipelineModel()
    p.add_block("nope.block", block_id="x")
    p.add_block("test.source", block_id="a")
    p.add_block("test.total", block_id="b")
    p.connect("a.wrong", "b.table")
    msgs = messages(p, registry)
    assert any("Unknown block type 'nope.block'" in m for m in msgs)
    assert any("no output port 'wrong'" in m for m in msgs)
    assert any("is not connected" in m for m in msgs)


def test_incompatible_types_are_refused(registry):
    p = PipelineModel()
    p.add_block("test.source", block_id="a")
    p.add_block("test.total", block_id="t")
    p.add_block("test.scale", block_id="s")
    p.connect("a.table", "t.table")
    p.connect("t.value", "s.table")
    assert any("Cannot connect Scalar output" in m for m in messages(p, registry))


def test_one_source_per_single_input(registry):
    p = PipelineModel()
    p.add_block("test.source", block_id="a")
    p.add_block("test.source", block_id="b")
    p.add_block("test.total", block_id="t")
    p.connect("a.table", "t.table")
    p.connect("b.table", "t.table")
    assert any("more than one connection" in m for m in messages(p, registry))


def test_cycle_is_detected(registry):
    p = PipelineModel()
    p.add_block("test.scale", block_id="a")
    p.add_block("test.scale", block_id="b")
    p.connect("a.table", "b.table")
    p.connect("b.table", "a.table")
    graph = prepare(p, registry)
    assert graph.order == []
    assert any("cycle" in i.message for i in graph.issues)


def test_newer_block_version_is_refused_and_older_migrated():
    class V2(Block[Any]):
        type_id = "test.v2"
        category = "Test"
        title = "V2"
        version = 2
        Params = Source.Params

        @classmethod
        def migrate(cls, old_version: int, params: dict[str, Any]) -> dict[str, Any]:
            return {"values": params.pop("old_values")}

        def run(self, params, inputs, ctx):
            return {}

    p = PipelineModel()
    p.add_block("test.v2", block_id="newer", values=[1.0]).type_version = 3
    p.add_block("test.v2", block_id="older", old_values=[5.0]).type_version = 1
    graph = prepare(p, BlockRegistry([V2]))
    assert [i.block_id for i in graph.issues] == ["newer"]
    assert graph.blocks["older"].params.values == [5.0]  # type: ignore[union-attr]


def test_registry_rejects_duplicates_and_incomplete_classes():
    class Clash(Source):
        pass

    reg = BlockRegistry([Source])
    with pytest.raises(RegistryError, match="Duplicate"):
        reg.register(Clash)

    class Incomplete(Block[Any]):
        def run(self, params, inputs, ctx):
            return {}

    with pytest.raises(RegistryError, match="type_id"):
        reg.register(Incomplete)


# ---- file format ------------------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path, registry):
    p = PipelineModel()
    p.metadata.title = "Rib check, ö"
    p.variables = {"h5_file": "results/rib.h5", "allowable": 410.0}
    p.add_block("test.source", block_id="a", label="Source", values=[1.5])
    p.add_block("test.scale", block_id="b", factor="${allowable}")
    p.connect("a.table", "b.table")
    path = tmp_path / "x.apb.json"
    save_pipeline(p, path)
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    data = json.loads(raw.decode("utf-8"))
    assert data["schema_version"] == 1
    assert data["connections"][0]["source"] == {"block": "a", "port": "table"}
    assert load_pipeline(path) == p


def test_unknown_keys_in_file_are_rejected():
    with pytest.raises(ValueError, match="extra"):
        PipelineModel.from_json('{"schema_version": 1, "blokcs": []}')


# ---- variables --------------------------------------------------------------------------


def test_resolve_keeps_type_for_whole_reference():
    variables = {"a": 410.0, "f": "rib.h5"}
    assert resolve("${a}", variables) == 410.0
    assert resolve("results/${f}", variables) == "results/rib.h5"
    assert resolve({"x": ["${a}", 1]}, variables) == {"x": [410.0, 1]}
    assert resolve("cost $${a}", variables) == "cost ${a}"


def test_resolve_unknown_variable():
    with pytest.raises(KeyError, match="Undefined variable"):
        resolve("${missing}", {})


def test_referenced_names_and_cli_assignment():
    assert referenced_names({"a": ["${x}", "${y}/z"]}) == {"x", "y"}
    assert parse_assignment("allowable=410.5") == ("allowable", 410.5)
    assert parse_assignment("f=results/new.h5") == ("f", "results/new.h5")
    with pytest.raises(ValueError):
        parse_assignment("novalue")


def test_scale_block_class_attributes():
    assert Scale.input_ports(None)[0].name == "table"
