"""Templates, static check and never-overwrite rules of the Python block (spec 04 §3.5)."""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from apb.blocks.python.block import PythonBlock
from apb.blocks.python.scripting import (
    PortSpec,
    ScriptError,
    check_script_source,
    modified_text,
    ports_to_params,
    read_script,
    write_new_file,
    write_skeleton,
)
from apb.blocks.python.templates import (
    TEMPLATES,
    create_script,
    get_template,
    plan_ports,
    template_titles,
)
from apb.cli import app
from apb.engine import PipelineModel, RunContext, load_pipeline, save_pipeline
from apb.engine.types import PortType


def ports(*specs: tuple[str, PortType]) -> list[PortSpec]:
    return [PortSpec(name=n, type=t) for n, t in specs]


TABLE, SCALAR, TEXT, FIGURE = PortType.TABLE, PortType.SCALAR, PortType.TEXT, PortType.FIGURE


def sample() -> pd.DataFrame:
    df = pd.DataFrame({"EID": [1, 2, 3], "SUBCASE": [1, 1, 1], "SHEAR": [100.0, 0.0, 300.0]})
    df.attrs["units"] = {"SHEAR": "N"}
    return df


def run_script(tmp_path: Path, script: Path, ins: list[PortSpec], outs: list[PortSpec], **inputs):
    params = PythonBlock.Params.model_validate(
        {
            "script": script.name,
            "inputs": ports_to_params(ins),
            "outputs": ports_to_params(outs),
        }
    )
    ctx = RunContext(block_id="b", pipeline_dir=tmp_path, output_dir=tmp_path)
    return PythonBlock().run(params, inputs, ctx)


# ---- templates ------------------------------------------------------------------------


def test_template_list():
    assert list(TEMPLATES) == ["blank", "table_calculation", "table_summary", "plot"]
    assert all(" — " in text for text in template_titles().values())
    with pytest.raises(ScriptError, match=r"Unknown template 'nope'.*blank"):
        get_template("nope")


@pytest.mark.parametrize("key", [k for k in TEMPLATES if k != "blank"])
def test_default_ports_script_is_valid_checked_and_runs(tmp_path, key):
    path = tmp_path / "s.py"
    ins, outs = create_script(path, key)
    ast.parse(path.read_text(encoding="utf-8"))
    assert check_script_source(path.read_text(encoding="utf-8"), ins, outputs=outs).ok
    inputs = {"table": sample(), "allowable": 1000.0}
    result = run_script(tmp_path, path, ins, outs, **{p.name: inputs[p.name] for p in ins})
    assert set(result) == {p.name for p in outs}


def test_table_calculation_values_and_units(tmp_path):
    path = tmp_path / "s.py"
    ins, outs = create_script(path, "table_calculation")
    out = run_script(tmp_path, path, ins, outs, table=sample(), allowable=1000.0)["result"]
    assert out["MS"].iloc[0] == pytest.approx(1000 / (1.5 * 100) - 1)
    assert pd.isna(out["MS"].iloc[1])  # zero load: no margin
    assert out.attrs["units"]["MS"] == "-"
    assert out.attrs["units"]["SHEAR"] == "N"
    assert out.attrs["title"] == "Margins of safety (SHEAR)"  # default sheet name


def test_table_summary_and_plot(tmp_path):
    path = tmp_path / "s.py"
    ins, outs = create_script(path, "table_summary")
    out = run_script(tmp_path, path, ins, outs, table=sample())
    assert out["max_value"] == 300.0
    assert out["where"] == "EID 3, SUBCASE 1"
    with pytest.raises(ScriptError, match="The table is empty"):
        run_script(tmp_path, path, ins, outs, table=sample().iloc[0:0])
    path = tmp_path / "p.py"
    ins, outs = create_script(path, "plot")
    assert type(run_script(tmp_path, path, ins, outs, table=sample())["plot"]).__name__ == "Figure"


def test_default_ports_only_for_a_block_without_ports():
    tpl = get_template("table_summary")
    ins, outs, mapping = plan_ports(tpl, [], [])
    assert [(p.name, p.type) for p in ins] == [("table", TABLE)]
    assert [p.name for p in outs] == ["max_value", "where"]
    assert mapping == {"table": "table", "max_value": "max_value", "where": "where"}
    # Declared ports are never changed; roles are matched by type, in order.
    mine_in = ports(("forces", TABLE), ("note", TEXT))
    mine_out = ports(("worst", SCALAR), ("loc", TEXT), ("spare", SCALAR))
    ins, outs, mapping = plan_ports(tpl, mine_in, mine_out)
    assert (ins, outs) == (mine_in, mine_out)
    assert mapping == {"table": "forces", "max_value": "worst", "where": "loc"}


def test_template_for_declared_ports_matches_them(tmp_path):
    ins = ports(("forces", TABLE), ("limit", SCALAR), ("note", TEXT), ("fig", FIGURE))
    outs = ports(("margins", TABLE), ("extra", SCALAR))
    path = tmp_path / "s.py"
    assert create_script(path, "table_calculation", ins, outs) == (ins, outs)
    source = path.read_text(encoding="utf-8")
    assert check_script_source(source, ins).ok  # extra ports are accepted as arguments
    assert "from matplotlib.figure import Figure" in source
    assert "extra = 0.0  # TODO: not used by the template" in source
    result = run_script(tmp_path, path, ins, outs, forces=sample(), limit=1000.0, note="", fig=None)
    assert set(result) == {"margins", "extra"}


def test_template_without_matching_port_is_refused(tmp_path):
    with pytest.raises(ScriptError, match=r"needs an input port of type Table.*blank template"):
        create_script(tmp_path / "s.py", "plot", ports(("x", SCALAR)), ports(("y", FIGURE)))
    assert not (tmp_path / "s.py").exists()


def test_blank_template_is_the_skeleton(tmp_path):
    ins, outs = ports(("loads", TABLE)), ports(("ms", SCALAR))
    create_script(tmp_path / "a" / "s.py", "blank", ins, outs)
    write_skeleton(tmp_path / "b" / "s.py", ins, outs)
    assert (tmp_path / "a" / "s.py").read_text() == (tmp_path / "b" / "s.py").read_text()


# ---- never overwritten (PY-13) ---------------------------------------------------------


def test_existing_script_is_never_replaced(tmp_path):
    path = tmp_path / "scripts" / "mine.py"
    path.parent.mkdir()
    path.write_text("# my work\n", encoding="utf-8")
    with pytest.raises(ScriptError, match="already exists"):
        create_script(path, "table_summary")
    with pytest.raises(ScriptError, match="already exists"):
        write_skeleton(path, [], [])
    with pytest.raises(ScriptError, match="already exists"):
        write_new_file(path, "x")
    assert path.read_text() == "# my work\n"
    write_new_file(path, "# forced\n", overwrite=True)  # command line --force only
    assert path.read_text() == "# forced\n"


def test_creation_is_exclusive_and_makes_folders(tmp_path):
    path = tmp_path / "new" / "folder" / "s.py"
    create_script(path, "plot")
    assert path.is_file()
    assert "\r" not in path.read_text(encoding="utf-8")


# ---- static check (PY-14) --------------------------------------------------------------

LOADS = ports(("loads", TABLE), ("k", SCALAR))


def check(source: str, inputs: list[PortSpec] = LOADS) -> list[str]:
    return list(check_script_source(source, inputs, "calc.py").problems)


def test_check_accepts_a_matching_script():
    assert check("def run(loads, k):\n    return {}\n") == []
    assert check("def run(loads, k, extra=1):\n    return {}\n") == []  # default: optional
    assert check("def run(*, loads, k):\n    return {}\n") == []
    assert check("def run(loads, **kw):\n    return {}\n") == []  # k goes to **kw


def test_check_reports_mismatches():
    assert check("def run(loads):\n    pass\n") == [
        "run() has no argument 'k' for the input port 'k'"
    ]
    assert check("def run(loads, k, other):\n    pass\n") == [
        "run() argument 'other' has no matching input port"
    ]
    assert check("def run(loads, k, *, flag):\n    pass\n") == [
        "run() argument 'flag' has no matching input port"
    ]


def test_check_reports_syntax_errors_and_missing_run():
    assert check("x = 1\ny = (\n")[0].startswith("Syntax error in calc.py, line ")
    assert check("def other():\n    pass\n") == ["calc.py defines no run() function"]
    assert check("class A:\n    def run(self):\n        pass\n") == [
        "calc.py defines no run() function"
    ]  # only a top-level function counts


def test_check_does_not_execute_the_script(tmp_path):
    marker = tmp_path / "ran"
    source = f"open({str(marker)!r}, 'w').close()\n\ndef run(loads, k):\n    return {{}}\n"
    assert check(source) == []
    assert not marker.exists()


def test_read_script_errors(tmp_path):
    with pytest.raises(ScriptError, match="File not found"):
        read_script(tmp_path / "missing.py")
    (tmp_path / "bin.py").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ScriptError, match="not a UTF-8 text file"):
        read_script(tmp_path / "bin.py")
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert read_script(tmp_path / "ok.py") == "x = 1\n"
    assert len(modified_text(tmp_path / "ok.py")) == 16
    assert modified_text(tmp_path / "missing.py") == ""


# ---- command line ----------------------------------------------------------------------


def make_pipeline(tmp_path: Path, **params) -> Path:
    p = PipelineModel()
    p.add_block("python.script", block_id="py", script="scripts/calc.py", **params)
    path = tmp_path / "p.apb.json"
    save_pipeline(p, path)
    return path


def test_cli_list_templates():
    res = CliRunner().invoke(app, ["new-script", "--list-templates"])
    assert res.exit_code == 0
    assert "table_calculation: Table calculation — " in res.output
    assert "blank: Blank" in res.output


def test_cli_template_adds_default_ports_and_saves(tmp_path):
    path = make_pipeline(tmp_path)
    res = CliRunner().invoke(app, ["new-script", str(path), "py", "--template", "table_summary"])
    assert res.exit_code == 0, res.output
    assert "Added the ports of the template" in res.output
    block = load_pipeline(path).block("py")
    assert block.params["inputs"] == [{"name": "table", "type": "Table"}]
    assert [p["name"] for p in block.params["outputs"]] == ["max_value", "where"]
    assert (tmp_path / "scripts" / "calc.py").is_file()
    res = CliRunner().invoke(app, ["run", str(path)])  # no connector yet: the input is missing
    assert res.exit_code != 0


def test_cli_template_keeps_declared_ports_and_never_overwrites(tmp_path):
    path = make_pipeline(
        tmp_path,
        inputs=[{"name": "forces", "type": "Table"}],
        outputs=[{"name": "peak", "type": "Scalar"}, {"name": "at", "type": "Text"}],
    )
    before = path.read_text(encoding="utf-8")
    runner = CliRunner()
    res = runner.invoke(app, ["new-script", str(path), "py", "--template", "table_summary"])
    assert res.exit_code == 0, res.output
    assert "Added the ports" not in res.output
    assert path.read_text(encoding="utf-8") == before  # the pipeline file is untouched
    script = tmp_path / "scripts" / "calc.py"
    mine = script.read_text(encoding="utf-8")
    assert "peak = float(forces.loc[row, column])" in mine
    script.write_text("# edited by hand\n", encoding="utf-8")
    res = runner.invoke(app, ["new-script", str(path), "py", "--template", "blank"])
    assert res.exit_code == 1 and "already exists (use --force to overwrite)" in res.output
    assert script.read_text(encoding="utf-8") == "# edited by hand\n"
    res = runner.invoke(
        app, ["new-script", str(path), "py", "--template", "table_summary", "--force"]
    )
    assert res.exit_code == 0
    assert script.read_text(encoding="utf-8") == mine


def test_cli_template_errors(tmp_path):
    path = make_pipeline(tmp_path, inputs=[{"name": "x", "type": "Scalar"}])
    runner = CliRunner()
    res = runner.invoke(app, ["new-script", str(path), "py", "--template", "plot"])
    assert res.exit_code == 1 and "needs an input port of type Table" in res.output
    res = runner.invoke(app, ["new-script", str(path), "py", "--template", "nope"])
    assert res.exit_code == 1 and "Unknown template" in res.output
    res = runner.invoke(app, ["new-script"])
    assert res.exit_code == 2 and "Give the pipeline file and the block id" in res.output
    assert not (tmp_path / "scripts" / "calc.py").exists()


# ---- fixes in both directions, outputs from the code (PY-21, PY-22) --------------------


def test_returned_dict_keys_are_compared_with_the_output_ports():
    from apb.blocks.python.signature import output_differences

    outs = ports(("margins", TABLE), ("summary", TEXT))
    src = (
        "def run(loads, k):\n"
        "    def helper():\n"
        "        return 3  # nested: not a return of run()\n"
        "    if k:\n"
        "        return {'margins': loads, 'extra': 1}\n"
        "    return {'margins': loads}\n"
    )
    diff = output_differences(src, outs)
    assert (diff.new, diff.unused) == (["extra"], ["summary"])
    problems = list(check_script_source(src, LOADS, "calc.py", outs).problems)
    assert problems == [
        "run() returns 'extra', which is not an output port",
        "The output port 'summary' is not in the dict run() returns",
    ]
    assert check_script_source(src, LOADS, "calc.py").ok  # outputs not checked: no outputs


@pytest.mark.parametrize(
    "body",
    [
        "    result = make()\n    return result\n",  # a variable, not a dict literal
        "    result = {'a': 1}\n    result = {'b': 2}\n    return result\n",  # assigned twice
        "    result = {'a': 1}\n    result.update(b=2)\n    return result\n",
        "    result = {'a': 1}\n    result[name] = 2\n    return result\n",
        "    result = {'a': 1}\n    del result['a']\n    return result\n",
        "    if x:\n        result = {'a': 1}\n    return result\n",  # not in run()'s body
        "    return dict(a=1)\n",
        "    return {**base, 'a': 1}\n",
        "    return {name: 1}\n",  # key not a plain string
        "    if x:\n        return {'a': 1}\n    return out\n",  # one return unknown
        "    pass\n",  # no return
    ],
)
def test_returned_keys_unknown_means_no_output_check(body: str):
    from apb.blocks.python.signature import output_differences

    src = "def run(x, base=None, name='', out=None):\n" + body
    assert output_differences(src, ports(("y", TABLE))) == output_differences("", [])
    assert check_script_source(src, ports(("x", TABLE)), outputs=ports(("y", TABLE))).ok


@pytest.mark.parametrize(
    ("body", "keys"),
    [
        ("    result = {'table': x}\n    return result\n", ["table"]),
        (
            "    result = {'table': x}\n"
            "    if x is not None:\n"
            "        result['extra'] = 1\n"
            "    return result\n",
            ["table", "extra"],
        ),
        (
            "    out = {'a': 1}\n    if x:\n        return {'b': 2}\n    return out\n",
            ["a", "b"],
        ),
    ],
)
def test_returned_keys_through_a_named_dict(body: str, keys: list[str]):
    """``result = {...}`` ... ``return result`` (spec 04 §3.9.1, second re-test)."""
    import ast

    from apb.blocks.python.scripting import find_run, returned_keys
    from apb.blocks.python.signature import output_differences

    src = "def run(x):\n" + body
    func = find_run(ast.parse(src))
    assert func is not None and returned_keys(func) == keys
    diff = output_differences(src, ports(("table", TABLE)))
    assert diff.new == [k for k in keys if k != "table"]


def test_ports_missing_from_run():
    from apb.blocks.python.signature import ports_missing_from_run

    two = ports(("loads", TABLE), ("k", SCALAR))
    assert [p.name for p in ports_missing_from_run("def run(loads):\n    return {}\n", two)] == [
        "k"
    ]
    assert ports_missing_from_run("def run(loads, **kw):\n    return {}\n", two) == []
    assert ports_missing_from_run("def run(:\n", two) == []  # syntax error: nothing offered
