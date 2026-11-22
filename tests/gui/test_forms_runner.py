from __future__ import annotations

import asyncio
import sys
from typing import Literal

import pytest
from pydantic import BaseModel, Field

from apb.blocks.python.scripting import PortSpec
from apb.blocks.report.block import ReportMetadata, WordReportParams
from apb.engine import default_registry
from apb.engine.events import BlockState
from apb.gui.editor import UNSET, EditorState
from apb.gui.forms import classify, default_of, humanize, short_path, suffix_hint
from apb.gui.runner import RunController, summary
from apb.units import UnitSystem
from tests.conftest import DATA_DIR


class Sample(BaseModel):
    text: str = "a"
    count: int = 3
    ratio: float
    flag: bool = False
    mode: Literal["x", "y"] = "x"
    unit: UnitSystem | None = None
    names: list[str] = Field(default_factory=list)
    modes: list[Literal["max", "min"]] = Field(default_factory=lambda: ["max"])
    labels: dict[int, str] = Field(default_factory=dict)
    ports: list[PortSpec] = Field(default_factory=list)
    meta: ReportMetadata = Field(default_factory=ReportMetadata)


@pytest.mark.parametrize(
    ("field", "kind", "optional"),
    [
        ("text", "text", False),
        ("count", "number", False),
        ("ratio", "number", False),
        ("flag", "bool", False),
        ("mode", "choice", False),
        ("unit", "choice", True),
        ("names", "list", False),
        ("modes", "multichoice", False),
        ("labels", "mapping", False),
        ("ports", "ports", False),
        ("meta", "model", False),
    ],
)
def test_classify(field, kind, optional):
    got_kind, _, got_optional = classify(Sample.model_fields[field].annotation)
    assert (got_kind, got_optional) == (kind, optional)


def test_report_sections_fall_back_to_json():
    assert classify(WordReportParams.model_fields["sections"].annotation)[0] == "json"


def test_defaults_and_helpers():
    f = Sample.model_fields
    assert default_of(f["ratio"]) is None  # required
    assert default_of(f["modes"]) == ["max"]
    assert default_of(f["meta"])["title"] == ""
    assert humanize("default_unit_system") == "Default unit system"
    assert suffix_hint("File Nastran .h5 result file") == ".h5"
    assert suffix_hint("Script Python file defining run") == ".py"
    assert suffix_hint("Base document Word file") == ".docx"


def test_set_param_prunes_connections_when_ports_change():
    state = EditorState(default_registry())
    state.open(DATA_DIR.parent / "fastener_loads.apb.json")
    py = state.pipeline.block("margins")
    inputs = [p for p in py.params["inputs"] if p["name"] != "safety_factor"]
    removed = state.set_param("margins", "inputs", inputs)
    assert [c.source.block for c in removed] == ["fos"]
    assert state.set_param("margins", "inputs", inputs) == []  # unchanged
    state.set_param("fos", "value", 2.0)
    state.set_param("fos", "value", UNSET)
    assert "value" not in state.pipeline.block("fos").params


def test_settings_and_variables():
    state = EditorState(default_registry())
    state.set_variables({"a": 1})
    state.set_settings(default_unit_system=UnitSystem.SI, title="T")
    assert state.pipeline.settings.default_unit_system is UnitSystem.SI
    assert state.pipeline.metadata.title == "T" and state.pipeline.variables == {"a": 1}


def test_runner_states_and_summary():
    state = EditorState(default_registry())
    state.open(DATA_DIR.parent / "plate_stress.apb.json")
    events = []
    runner = RunController(state, events.append)
    assert set(runner.display_states().values()) == {"idle"}
    result = asyncio.run(runner.run())
    runner.drain()
    assert not isinstance(result, list) and result.ok
    assert any(e.block_id == "modes" for e in events)
    assert set(runner.display_states().values()) == {"done"}
    assert summary(result) == ("Run finished: 4 block(s) done", "positive")

    state.set_param("modes", "file", "data/missing.h5")
    runner.mark_edited("modes")
    assert runner.display_states()["modes"] == "stale"
    result = asyncio.run(runner.run())
    assert result.results["modes"].state is BlockState.ERROR
    runner.edited.clear()
    assert runner.display_states()["modes"] == "error"
    text, kind = summary(result)
    assert kind == "negative" and "1 error" in text


def test_runner_reports_validation_issues():
    state = EditorState(default_registry())
    b = state.add_block("table.sort", 0, 0)
    issues = asyncio.run(RunController(state, lambda e: None).run())
    assert isinstance(issues, list) and any(i.block_id == b.id for i in issues)


def test_unit_options_keep_an_unknown_stored_unit():
    from apb.gui.forms import unit_options

    options = unit_options()
    assert next(iter(options.items())) == ("", "(none)")
    assert "Hz" in options and "-" not in options
    assert unit_options("lbf")["lbf"] == "lbf (unknown unit)"
    assert unit_options("lbf", other=True)["lbf"] == "lbf"


def test_short_path_keeps_the_separator_of_the_path():
    if sys.platform == "win32":
        win = r"C:\Users\runneradmin\AppData\Local\Temp\pytest-0\bin\nastran.BAT"
        assert short_path(win, limit=30).endswith("…\\nastran.BAT")
        return
    posix = "/home/someone/projects/a/very/long/folder/bin/nastran"
    assert short_path(posix, limit=30).endswith("…/nastran")
