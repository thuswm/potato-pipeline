from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from apb.blocks.python.block import PythonBlock
from apb.blocks.python.scripting import PortSpec, ScriptError, script_skeleton
from apb.cli import app
from apb.engine import (
    BlockState,
    Executor,
    PipelineModel,
    default_registry,
    load_pipeline,
    prepare,
    save_pipeline,
)
from apb.engine.types import PortType
from apb.units import Quantity, UnitSystem, get_units, set_units
from tests.conftest import DATA_DIR, run_block

PORTS_IN = [{"name": "table", "type": "Table"}, {"name": "factor", "type": "Scalar"}]
PORTS_OUT = [{"name": "result", "type": "Table"}, {"name": "total", "type": "Scalar"}]

SCRIPT = """
import numpy as np

def run(table, factor):
    print("scaling by", factor)
    out = table.assign(B=table["A"] * factor)
    return {"result": out, "total": np.float64(out["B"].sum())}
"""


def table() -> pd.DataFrame:
    df = pd.DataFrame({"A": [1.0, 2.0]})
    set_units(df, UnitSystem.SI_MM, {"A": "N"})
    return df


def write(tmp_path: Path, code: str, name: str = "calc.py") -> Path:
    path = tmp_path / name
    path.write_text(code, encoding="utf-8")
    return path


def run_script(tmp_path: Path, code: str, values=None, **params):
    write(tmp_path, code)
    return run_block(
        PythonBlock,
        {"table": table(), "factor": Quantity(3.0, "-")} if values is None else values,
        tmp=tmp_path,
        script="calc.py",
        inputs=params.pop("inputs", PORTS_IN),
        outputs=params.pop("outputs", PORTS_OUT),
        **params,
    )


def test_script_runs_and_units_survive(tmp_path):
    out = run_script(tmp_path, SCRIPT)
    assert out["result"]["B"].tolist() == [3.0, 6.0]
    assert type(out["total"]) is float and out["total"] == 9.0
    assert get_units(out["result"]) == {"A": "N"}
    assert out["result"].attrs["unit_system"] == "SI_mm"


def test_ports_come_from_params():
    params = PythonBlock.Params.model_validate(
        {"script": "x.py", "inputs": PORTS_IN, "outputs": PORTS_OUT}
    )
    assert [p.name for p in PythonBlock.input_ports(params)] == ["table", "factor"]
    assert PythonBlock.output_ports(params)[1].type is PortType.SCALAR


def test_params_validation():
    with pytest.raises(ValidationError, match="exactly one"):
        PythonBlock.Params.model_validate({"inputs": []})
    with pytest.raises(ValidationError, match="valid Python name"):
        PythonBlock.Params.model_validate({"script": "x.py", "inputs": [{"name": "my input"}]})
    with pytest.raises(ValidationError, match="duplicate"):
        PythonBlock.Params.model_validate(
            {"script": "x.py", "outputs": [{"name": "a"}, {"name": "a"}]}
        )


def test_error_reports_line_in_users_file(tmp_path):
    code = "def run(table, factor):\n    x = 1\n    return 1 / 0\n"
    with pytest.raises(ScriptError, match=r"ZeroDivisionError.*\(calc.py, line 3\)"):
        run_script(tmp_path, code)


def test_signature_mismatch(tmp_path):
    with pytest.raises(ScriptError, match="no argument 'factor'"):
        run_script(tmp_path, "def run(table):\n    return {}\n")
    with pytest.raises(ScriptError, match="'extra' has no matching input port"):
        run_script(tmp_path, "def run(table, factor, extra):\n    return {}\n")


def test_missing_script_and_missing_run(tmp_path):
    with pytest.raises(ScriptError, match="apb new-script"):
        run_block(PythonBlock, {}, tmp=tmp_path, script="nope.py")
    with pytest.raises(ScriptError, match="does not define a function 'run'"):
        run_script(tmp_path, "x = 1\n")


def test_helper_module_is_reloaded(tmp_path):
    write(tmp_path, "FACTOR = 2\n", "helper.py")
    code = "import helper\n\ndef run():\n    return helper.FACTOR\n"
    outputs = [{"name": "v", "type": "Scalar"}]
    assert run_script(tmp_path, code, {}, inputs=[], outputs=outputs)["v"] == 2
    write(tmp_path, "FACTOR = 5\n", "helper.py")
    assert run_script(tmp_path, code, {}, inputs=[], outputs=outputs)["v"] == 5


def test_single_output_may_be_returned_directly(tmp_path):
    out = run_script(
        tmp_path,
        "def run():\n    return 'hello'\n",
        {},
        inputs=[],
        outputs=[{"name": "msg", "type": "Text"}],
    )
    assert out == {"msg": "hello"}


def test_inline_code():
    code = "def run(table, factor):\n    return {'result': table, 'total': math.pi}\n"
    out = run_block(
        PythonBlock,
        {"table": table(), "factor": 1.0},
        inline_code=code,
        inputs=PORTS_IN,
        outputs=PORTS_OUT,
    )
    assert out["total"] == pytest.approx(3.14159, abs=1e-4)
    with pytest.raises(ScriptError, match="<block t>, line 1"):
        run_block(
            PythonBlock,
            {"table": table(), "factor": 1.0},
            inline_code="def run(table, factor):  return 1/0",
            inputs=PORTS_IN,
            outputs=PORTS_OUT,
        )


def test_mixed_unit_systems_refused(tmp_path):
    other = table()
    set_units(other, UnitSystem.SI, {})
    with pytest.raises(ScriptError, match="different unit systems"):
        run_script(
            tmp_path,
            SCRIPT,
            {"table": table(), "factor": other},
            inputs=[{"name": "table", "type": "Table"}, {"name": "factor", "type": "Table"}],
        )


def pipeline(tmp_path: Path) -> PipelineModel:
    p = PipelineModel()
    p.add_block("table.read_csv", block_id="csv", file="data.csv")
    p.add_block("table.constant", block_id="k", value=2.0)
    p.add_block(
        "python.script", block_id="py", script="scripts/calc.py", inputs=PORTS_IN, outputs=PORTS_OUT
    )
    p.connect("csv.table", "py.table")
    p.connect("k.value", "py.factor")
    (tmp_path / "data.csv").write_text("A\n1\n2\n", encoding="utf-8")
    return p


def test_pipeline_print_goes_to_log_and_edit_invalidates_cache(tmp_path):
    p = pipeline(tmp_path)
    (tmp_path / "scripts").mkdir()
    write(tmp_path / "scripts", SCRIPT)
    assert prepare(p, default_registry()).issues == []
    executor = Executor(default_registry())
    result = executor.run(p, tmp_path)
    assert result.ok
    assert result.results["py"].log == ["scaling by 2.0"]
    assert executor.states(p, tmp_path)["py"] is BlockState.DONE
    write(tmp_path / "scripts", SCRIPT.replace("scaling", "multiplying"))
    assert executor.states(p, tmp_path)["py"] is BlockState.STALE
    assert executor.run(p, tmp_path).results["py"].log == ["multiplying by 2.0"]


def test_skeleton_is_valid_and_matches_ports(tmp_path):
    ins = [
        PortSpec(name="loads", type=PortType.TABLE),
        PortSpec(name="k", type=PortType.SCALAR, optional=True),
    ]
    outs = [PortSpec(name="plot", type=PortType.FIGURE), PortSpec(name="ms", type=PortType.SCALAR)]
    source = script_skeleton(ins, outs, "Demo")
    ast.parse(source)
    assert "def run(loads: pd.DataFrame, k: float | None = None)" in source
    assert '"plot": plot' in source


def test_cli_new_script_then_run(tmp_path):
    p = pipeline(tmp_path)
    save_pipeline(p, tmp_path / "p.apb.json")
    runner = CliRunner()
    res = runner.invoke(app, ["new-script", str(tmp_path / "p.apb.json"), "py"])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "scripts" / "calc.py").exists()
    res = runner.invoke(app, ["new-script", str(tmp_path / "p.apb.json"), "py"])
    assert res.exit_code == 1 and "already exists" in res.output
    res = runner.invoke(app, ["run", str(tmp_path / "p.apb.json")])
    assert res.exit_code == 0, res.output  # skeleton returns valid placeholder outputs


def test_fastener_example_margins(tmp_path):
    path = DATA_DIR.parent / "fastener_loads.apb.json"
    result = Executor(default_registry()).run(load_pipeline(path), path.parent, output_dir=tmp_path)
    assert result.ok
    assert result.output("margins", "min_ms") == pytest.approx(0.2346, abs=1e-4)
    assert any(
        "Minimum margin 0.23 at fastener 1001" in line for line in result.results["margins"].log
    )
    assert len(result.output("crit_ms", "table")) == 6


def test_syntax_error_and_dataclass_in_script(tmp_path):
    with pytest.raises(ScriptError, match=r"Syntax error in calc.py, line 2"):
        run_script(tmp_path, "def run(table, factor):\n    return (\n")
    code = (
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n\n"
        "@dataclass\nclass Allowable:\n    value: float\n\n"
        "def run():\n    return Allowable(2.0).value\n"
    )
    assert run_script(tmp_path, code, {}, inputs=[], outputs=[{"name": "v", "type": "Scalar"}]) == {
        "v": 2.0
    }
    import sys

    assert not [m for m in sys.modules if m.startswith("apb_script_calc")]
