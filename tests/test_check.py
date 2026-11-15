"""The requirement check block (spec 03 §3.4)."""

from __future__ import annotations

import math

import pytest

from apb.blocks.check.requirement import CheckError, RequirementCheck, evaluate
from apb.engine import Executor, PipelineModel, RunContext, default_registry
from apb.units import Quantity
from tests.conftest import MODAL_H5


def run(inputs: dict, **params):
    logs: list[str] = []
    ctx = RunContext(block_id="c", pipeline_dir=MODAL_H5.parent, output_dir=MODAL_H5.parent)
    ctx.log = logs.append  # type: ignore[method-assign]
    parsed = RequirementCheck.Params.model_validate(params)
    full = {"value": None, "limit": None} | inputs
    return RequirementCheck().run(parsed, full, ctx), logs


@pytest.mark.parametrize(
    ("comparison", "value", "limit", "passed"),
    [
        ("greater_than", 25.0, 20.0, True),
        ("greater_than", 20.0, 20.0, False),  # equality passes only for greater_equal
        ("greater_equal", 20.0, 20.0, True),
        ("greater_equal", 19.9, 20.0, False),
        ("less_than", 10.0, 20.0, True),
        ("less_than", 20.0, 20.0, False),
        ("less_equal", 20.0, 20.0, True),
        ("less_equal", 20.1, 20.0, False),
    ],
)
def test_evaluate_status(comparison, value, limit, passed):
    assert evaluate(value, limit, comparison)[0] is passed


def test_margins():
    assert evaluate(25.0, 20.0, "greater_than")[1] == pytest.approx(0.25)
    assert evaluate(10.0, 20.0, "less_than")[1] == pytest.approx(1.0)  # limit / value - 1
    assert evaluate(15.0, 20.0, "greater_than")[1] == pytest.approx(-0.25)  # fail: negative
    margin, note = evaluate(5.0, 0.0, "greater_than")[1:]
    assert math.isnan(margin) and "limit is not positive" in note
    margin, note = evaluate(0.0, 5.0, "less_than")[1:]
    assert math.isnan(margin) and "value is not positive" in note
    assert evaluate(0.0, 5.0, "less_than")[0] is True  # the status does not depend on the margin


def test_pass_with_unit_from_the_value():
    out, logs = run(
        {"value": Quantity(25.7119, "Hz")},
        requirement="REQ-1 First eigenfrequency",
        limit=20.0,
    )
    assert out["status"] == "PASS"
    assert out["margin"] == pytest.approx(0.28559, abs=1e-5)
    table = out["result"]
    assert list(table.columns) == [
        "REQUIREMENT",
        "VALUE",
        "CONDITION",
        "LIMIT",
        "MARGIN",
        "STATUS",
    ]
    assert table.iloc[0].tolist()[:3] == ["REQ-1 First eigenfrequency", 25.7119, ">"]
    assert table.attrs["units"] == {"VALUE": "Hz", "LIMIT": "Hz", "MARGIN": "-"}
    assert "limit 20 interpreted as Hz" in logs
    assert logs[-1] == "REQ-1 First eigenfrequency: 25.7119 Hz > 20 Hz -> PASS (margin 0.2856)"


def test_failure_is_logged_but_not_an_error():
    out, logs = run({"value": 15.0}, comparison="greater_equal", limit=20.0, unit="Hz")
    assert out["status"] == "FAIL"
    assert out["result"]["STATUS"].iloc[0] == "FAIL"
    assert out["result"]["CONDITION"].iloc[0] == "≥"
    assert out["result"].attrs["units"]["VALUE"] == "Hz"  # unit parameter used
    assert logs[-1].startswith("WARNING: requirement not met. 15 Hz ≥ 20 Hz -> FAIL")


def test_limit_input_overrides_the_parameter():
    out, _ = run({"value": 30.0, "limit": 25.0}, limit=100.0)
    assert out["status"] == "PASS"
    assert out["result"]["LIMIT"].iloc[0] == 25.0


def test_unit_clash_and_other_errors():
    with pytest.raises(CheckError, match=r"Value is in Hz but the limit in rad/s.*Convert units"):
        run({"value": Quantity(1.0, "Hz"), "limit": Quantity(2.0, "rad/s")})
    with pytest.raises(CheckError, match="No limit"):
        run({"value": 1.0})
    with pytest.raises(CheckError, match="NaN"):
        run({"value": math.nan}, limit=1.0)
    with pytest.raises(CheckError, match="must be a number"):
        run({"value": True}, limit=1.0)
    with pytest.raises(ValueError, match="unknown unit"):
        RequirementCheck.Params.model_validate({"unit": "furlong"})


def test_undefined_margin_is_logged():
    out, logs = run({"value": 5.0}, limit=0.0)
    assert math.isnan(out["margin"])
    assert "margin undefined: the limit is not positive" in logs


def build_pipeline(limit: float) -> PipelineModel:
    p = PipelineModel()
    p.add_block(
        "nastran.hdf5_results",
        block_id="modal",
        file=str(MODAL_H5),
        result="MODAL/MODAL_EFFECTIVE_MASS",
        unit_system="SI",
    )
    p.add_block("table.pick", block_id="f1", column="FREQUENCY", row="min")
    p.add_block(
        "check.requirement",
        block_id="req",
        requirement="First eigenfrequency",
        comparison="greater_than",
        limit="${min_frequency}",
    )
    p.connect("modal.table", "f1.table")
    p.connect("f1.value", "req.value")
    p.variables["min_frequency"] = limit
    return p


def test_check_on_real_modal_results(tmp_path):
    result = Executor(default_registry()).run(build_pipeline(20.0), tmp_path)
    assert result.ok
    assert result.output("req", "status") == "PASS"
    assert result.output("req", "margin") == pytest.approx(25.711863 / 20 - 1, rel=1e-6)
    row = result.output("req", "result").iloc[0]
    assert row["VALUE"] == pytest.approx(25.711863, rel=1e-6)
    assert row["LIMIT"] == 20.0
    log = result.results["req"].log
    assert "limit 20 interpreted as Hz" in log


def test_failing_requirement_does_not_stop_the_run(tmp_path):
    result = Executor(default_registry()).run(build_pipeline(30.0), tmp_path)
    assert result.ok  # the block is done, not in error
    assert result.output("req", "status") == "FAIL"
    assert any(
        line.startswith("WARNING: requirement not met") for line in result.results["req"].log
    )


def test_block_is_in_the_library():
    cls = default_registry().get("check.requirement")
    assert (cls.category, cls.title) == ("Checks", "Requirement check")
    assert [p.name for p in cls.outputs] == ["result", "status", "margin"]
    assert [p.name for p in cls.inputs if p.optional] == ["limit"]
