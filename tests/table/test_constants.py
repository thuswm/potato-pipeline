"""Tests of the Constants block and of ``${name}`` from constants (spec 03 §3.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from apb.blocks.table.constants import Constants, ConstantsParams, name_problem
from apb.engine import BlockState, Executor, PipelineModel
from apb.engine.graph import collect_variables, prepare
from apb.engine.registry import default_registry
from apb.units import Quantity
from tests.conftest import run_block

SF = {"name": "SF", "value": 1.5, "description": "Safety factor"}
F_MIN = {"name": "f_min", "value": 20, "unit": "Hz", "description": "REQ-DYN-001"}


# --------------------------------------------------------------------------------------
# Parameters (CON-01 .. CON-04)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("SF_yield", True),
        ("_x", True),
        ("f2", True),
        ("2f", False),
        ("SF yield", False),
        ("SF-yield", False),
        ("", False),
        ("class", False),
        ("table", False),
        ("é", False),
    ],
)
def test_name_rule(name: str, ok: bool):
    assert (name_problem(name) == "") is ok


def test_params_validation():
    ConstantsParams.model_validate({"constants": [SF, F_MIN]})
    with pytest.raises(ValidationError, match="used twice"):
        ConstantsParams.model_validate({"constants": [SF, SF]})
    with pytest.raises(ValidationError, match="Unknown unit"):
        ConstantsParams.model_validate({"constants": [{**SF, "unit": "furlong"}]})
    with pytest.raises(ValidationError, match="finite"):
        ConstantsParams.model_validate({"constants": [{**SF, "value": float("inf")}]})
    with pytest.raises(ValidationError):
        ConstantsParams.model_validate({"constants": [{**SF, "value": "high"}]})


# --------------------------------------------------------------------------------------
# Outputs (CON-05, CON-06)
# --------------------------------------------------------------------------------------


def test_outputs_table_and_one_port_per_constant():
    out = run_block(Constants, constants=[SF, F_MIN])
    table = out["table"]
    assert list(table.columns) == ["NAME", "VALUE", "UNIT", "DESCRIPTION"]
    assert table["NAME"].tolist() == ["SF", "f_min"]
    assert table["VALUE"].tolist() == [1.5, 20.0]
    assert table["UNIT"].tolist() == ["", "Hz"]
    assert out["SF"] == 1.5
    assert out["f_min"] == Quantity(20.0, "Hz")


def test_ports_follow_the_constants():
    params = ConstantsParams.model_validate({"constants": [SF, F_MIN]})
    assert [p.name for p in Constants.output_ports(params)] == ["table", "SF", "f_min"]
    assert [p.name for p in Constants.output_ports(None)] == ["table"]
    assert Constants.provided_variables(params) == {"SF": 1.5, "f_min": 20.0}


# --------------------------------------------------------------------------------------
# ${name} in other blocks (CON-07 .. CON-09)
# --------------------------------------------------------------------------------------


def pipeline(tmp: Path, **constants_params: Any) -> PipelineModel:
    """Constants -> requirement check (limit from ${f_min}) and a computed column."""
    (tmp / "loads.csv").write_text("FZ\n100\n200\n", encoding="utf-8")
    p = PipelineModel()
    p.add_block(
        "table.constants",
        block_id="const",
        label="Project constants",
        constants=[SF, F_MIN],
        **constants_params,
    )
    p.add_block("table.read_csv", block_id="csv", file="loads.csv")
    p.add_block("table.compute", block_id="design", new_column="FZ_D", expression="FZ * ${SF}")
    p.add_block("table.constant", block_id="f1", value=25.7, unit="Hz")
    p.add_block(
        "check.requirement",
        block_id="req",
        requirement="REQ-DYN-001",
        comparison="greater_than",
        limit="${f_min}",
    )
    p.connect("csv.table", "design.table")
    p.connect("f1.value", "req.value")
    return p


def test_constants_resolve_in_other_blocks(tmp_path: Path):
    result = Executor(default_registry()).run(pipeline(tmp_path), tmp_path)
    assert result.ok, {k: r.error for k, r in result.results.items()}
    assert result.output("design", "table")["FZ_D"].tolist() == [150.0, 300.0]
    assert result.output("req", "status") == "PASS"


def test_changing_a_constant_makes_exactly_its_users_stale(tmp_path: Path):
    p = pipeline(tmp_path)
    executor = Executor(default_registry())
    executor.run(p, tmp_path)
    p.block("const").params["constants"] = [{**SF, "value": 2.0}, F_MIN]
    states = executor.states(p, tmp_path)
    assert states["const"] is BlockState.STALE
    assert states["design"] is BlockState.STALE  # uses ${SF}
    assert states["req"] is BlockState.DONE  # uses only ${f_min}
    assert states["csv"] is BlockState.DONE
    rerun = executor.run(p, tmp_path)
    assert rerun.output("design", "table")["FZ_D"].tolist() == [200.0, 400.0]


def test_port_connection_to_requirement_limit(tmp_path: Path):
    p = pipeline(tmp_path)
    p.block("req").params.pop("limit")
    p.connect("const.f_min", "req.limit")
    result = Executor(default_registry()).run(p, tmp_path)
    assert result.ok
    assert result.output("req", "result")["LIMIT"].tolist() == [20.0]


def test_cli_override_changes_users_and_the_block_itself(tmp_path: Path):
    result = Executor(default_registry()).run(pipeline(tmp_path), tmp_path, overrides={"SF": 1.4})
    assert result.output("design", "table")["FZ_D"].tolist() == pytest.approx([140.0, 280.0])
    assert result.output("const", "SF") == 1.4
    assert result.output("const", "table")["VALUE"].tolist() == [1.4, 20.0]


def test_clash_with_pipeline_variable(tmp_path: Path):
    p = pipeline(tmp_path)
    p.variables["SF"] = 2.0
    graph = prepare(p, default_registry())
    messages = [str(i) for i in graph.issues]
    assert any(
        "same name as a pipeline variable" in m and "Project constants" in m for m in messages
    )


def test_clash_between_two_constants_blocks(tmp_path: Path):
    p = pipeline(tmp_path)
    p.add_block("table.constants", block_id="more", label="More", constants=[{**SF, "value": 9}])
    variables, issues = collect_variables(p, default_registry())
    assert variables["SF"] == 1.5  # the first definition wins
    assert len(issues) == 1
    assert "defined twice" in str(issues[0]) and "'Project constants'" in str(issues[0])
    assert issues[0].block_id == "more"


def test_constants_block_cannot_use_variables(tmp_path: Path):
    p = pipeline(tmp_path)
    p.block("const").params["constants"] = [{**SF, "value": "${x}"}]
    p.variables["x"] = 3
    _, issues = collect_variables(p, default_registry())
    assert any("cannot use ${...}" in str(i) for i in issues)


def test_invalid_constants_block_leaves_users_undefined(tmp_path: Path):
    p = pipeline(tmp_path)
    p.block("const").params["constants"] = [{**SF, "name": "bad name"}, F_MIN]
    messages = [str(i) for i in prepare(p, default_registry()).issues]
    assert any("Undefined variable ${SF}" in m for m in messages)
    assert any("bad name" in m or "letters, digits" in m for m in messages)


def test_port_users_go_stale_only_for_their_own_constant(tmp_path: Path):
    p = pipeline(tmp_path)
    p.block("req").params.pop("limit")
    p.connect("const.f_min", "req.limit")
    p.add_block("table.concat", block_id="all", add_source_column=False)
    p.connect("const.table", "all.tables")
    executor = Executor(default_registry())
    executor.run(p, tmp_path)
    p.block("const").params["constants"] = [{**SF, "value": 2.0}, F_MIN]
    states = executor.states(p, tmp_path)
    assert states["req"] is BlockState.DONE  # connected to f_min, which did not change
    assert states["all"] is BlockState.STALE  # the table holds every constant
    p.block("const").params["constants"] = [SF, {**F_MIN, "value": 25.0}]
    assert executor.states(p, tmp_path)["req"] is BlockState.STALE
    # A new description changes the table, not the value on the port.
    p.block("const").params["constants"] = [SF, {**F_MIN, "description": "new text"}]
    states = executor.states(p, tmp_path)
    assert states["req"] is BlockState.DONE and states["all"] is BlockState.STALE
