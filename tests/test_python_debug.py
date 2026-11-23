"""Python block, round 6: ports and run() in step, debugging help (spec 04 §3.9, §3.10)."""

from __future__ import annotations

import pandas as pd
import pytest

from apb import show
from apb.blocks.python.scripting import PortSpec, ScriptError
from apb.blocks.python.signature import ports_from_signature, rewrite_run_signature
from apb.debug import summarize
from apb.engine import BlockState, Executor, PipelineModel, default_registry
from apb.engine.types import PortType

SOURCE = '''"""Margins."""
import pandas as pd


def run(table: pd.DataFrame, allowable: float = 1.0) -> dict[str, object]:
    """Doc."""
    return {"result": table}
'''

TABLE = PortSpec(name="table", type=PortType.TABLE)
ALLOWABLE = PortSpec(name="allowable", type=PortType.SCALAR)


def def_line(source: str) -> str:
    return next(line for line in source.splitlines() if line.startswith("def run"))


# ---- run() follows the ports (PY-16, PY-17) ---------------------------------------------


def test_renaming_a_port_renames_the_argument_and_keeps_hints():
    forces = PortSpec(name="forces", type=PortType.TABLE)
    change = rewrite_run_signature(SOURCE, [TABLE, ALLOWABLE], [forces, ALLOWABLE])
    assert change.renamed == [("table", "forces")] and change.summary() == "renamed table → forces"
    assert def_line(change.source) == (
        "def run(forces: pd.DataFrame, allowable: float = 1.0) -> dict[str, object]:"
    )
    assert change.source.replace(def_line(change.source), "") == SOURCE.replace(
        def_line(SOURCE), ""
    )  # only the def line changed


def test_adding_and_removing_ports():
    loc = PortSpec(name="locations", type=PortType.TABLE, optional=True)
    added = rewrite_run_signature(SOURCE, [TABLE, ALLOWABLE], [TABLE, ALLOWABLE, loc])
    assert added.added == ["locations"]
    assert "locations: pd.DataFrame | None = None" in added.source
    assert "def run(\n    table: pd.DataFrame,\n" in added.source  # long: one per line
    compile(added.source, "s.py", "exec")
    removed = rewrite_run_signature(added.source, [TABLE, ALLOWABLE, loc], [TABLE])
    assert removed.removed == ["allowable", "locations"]
    assert def_line(removed.source) == "def run(table: pd.DataFrame) -> dict[str, object]:"
    assert not rewrite_run_signature(SOURCE, [TABLE, ALLOWABLE], [TABLE, ALLOWABLE]).changed


def test_other_arguments_and_kwargs_are_kept_in_a_valid_order():
    source = "def run(table, scale=2, **extra):\n    return {}\n"
    new = PortSpec(name="limit", type=PortType.SCALAR)
    change = rewrite_run_signature(source, [TABLE], [TABLE, new])
    assert def_line(change.source) == "def run(table, limit: float, scale=2, **extra):"


def test_unparsable_scripts_are_not_touched():
    with pytest.raises(ScriptError, match="syntax error"):
        rewrite_run_signature("def run(:\n", [], [TABLE])
    with pytest.raises(ScriptError, match="no run"):
        rewrite_run_signature("x = 1\n", [], [TABLE])


def test_ports_are_proposed_for_arguments_without_port():
    source = "def run(loads: pd.DataFrame, n: int, note: str, fig: Figure, x, k=1):\n  pass\n"
    proposed = ports_from_signature(source, [PortSpec(name="x")])
    assert [(p.name, p.type) for p in proposed] == [
        ("loads", PortType.TABLE),
        ("n", PortType.SCALAR),
        ("note", PortType.TEXT),
        ("fig", PortType.FIGURE),
    ]
    assert ports_from_signature("def run(:", []) == []


# ---- outputs (PY-18) --------------------------------------------------------------------------


def python_pipeline(code: str, outputs: list[str]) -> PipelineModel:
    p = PipelineModel()
    p.add_block("table.constant", block_id="k", value=2.0)
    p.add_block(
        "python.script",
        block_id="py",
        inline_code=code,
        inputs=[{"name": "factor", "type": "Scalar"}],
        outputs=[{"name": n, "type": "Any"} for n in outputs],
    )
    p.connect("k.value", "py.factor")
    return p


def test_output_mismatch_is_explained(tmp_path):
    code = "def run(factor):\n    return {'reslt': factor, 'b': 1}\n"
    result = Executor(default_registry()).run(python_pipeline(code, ["result", "b"]), tmp_path)
    error = result.results["py"].error
    assert "returned 'reslt', which is not an output port" in error
    assert "the output port 'result' is missing" in error


# ---- debugging (PY-19, PY-20) -------------------------------------------------------------------


def test_show_outside_the_tool_prints(capsys):
    df = pd.DataFrame({"EID": [1, 2], "F": [1.0, 2.0]})
    assert show(df, "forces") is df
    out = capsys.readouterr().out
    assert out.startswith("show: forces = DataFrame 2 rows x 2 columns: EID, F")


def test_summaries():
    df = pd.DataFrame({"A": range(8)})
    lines = summarize(df)
    assert lines[0] == "DataFrame 8 rows x 1 columns: A" and lines[-1] == "… 3 more rows"
    assert summarize(3.5) == ["3.5"]
    assert summarize("x" * 500)[0].endswith("…")


def test_a_failing_script_shows_where_the_variables_and_the_shown_values(tmp_path):
    code = (
        "import apb\n"
        "def run(factor):\n"
        "    table = pd.DataFrame({'EID': [1, 2], 'F': [10.0, 0.0]})\n"
        "    apb.show(table, 'start')\n"
        "    table['F'] = 5.0  # changed after show: the snapshot keeps 10, 0\n"
        "    ratio = factor / 0\n"
        "    return {'result': ratio}\n"
    )
    result = Executor(default_registry()).run(python_pipeline(code, ["result"]), tmp_path)
    block = result.results["py"]
    assert block.state is BlockState.ERROR and "ZeroDivisionError" in block.error
    log = "\n".join(block.log)
    assert "line 6 in run(): ratio = factor / 0" in log
    assert "factor = 2.0" in log
    assert "table = DataFrame 2 rows x 2 columns: EID, F" in log
    assert block.shown["start"]["F"].tolist() == [10.0, 0.0]
    assert "show: start = DataFrame 2 rows" in log


def test_shown_values_of_a_successful_run(tmp_path):
    code = (
        "from apb import show\n"
        "def run(factor):\n"
        "    show(factor * 2, 'twice')\n"
        "    return {'result': 1}\n"
    )
    result = Executor(default_registry()).run(python_pipeline(code, ["result"]), tmp_path)
    assert result.results["py"].shown == {"twice": 4.0}
