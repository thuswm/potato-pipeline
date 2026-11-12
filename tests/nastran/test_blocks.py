"""Nastran blocks run through the real engine, and the CLI end to end."""

from __future__ import annotations

import shutil

from typer.testing import CliRunner

from apb.cli import app
from apb.engine import BlockState, Executor, PipelineModel, default_registry, save_pipeline
from apb.units import UnitSystem
from tests.conftest import JOINT_H5, PLATE_H5


def plate_pipeline() -> PipelineModel:
    p = PipelineModel()
    p.variables["h5_file"] = PLATE_H5.name
    p.add_block(
        "nastran.hdf5_results",
        block_id="stress",
        file="${h5_file}",
        result="ELEMENTAL/STRESS/QUAD4",
        subcases=[1, 2],
    )
    p.add_block("nastran.hdf5_eigenvalues", block_id="eig", file="${h5_file}", unit_system="SI")
    p.add_block("nastran.hdf5_masses", block_id="mass", file="${h5_file}")
    return p


def test_nastran_blocks_in_pipeline():
    result = Executor(default_registry()).run(plate_pipeline(), PLATE_H5.parent)
    assert result.ok
    stress = result.output("stress", "table")
    assert len(stress) == 80
    assert stress.attrs["unit_system"] == UnitSystem.SI_MM  # pipeline default
    assert result.output("eig", "table").attrs["unit_system"] == "SI"
    assert any("synthetic" in line for line in result.results["stress"].log)


def test_rbe2_block():
    p = PipelineModel()
    p.add_block("nastran.hdf5_rbe2_loads", block_id="rbe", file=str(JOINT_H5), source="gpforce")
    result = Executor(default_registry()).run(p, JOINT_H5.parent)
    assert result.ok
    assert len(result.output("rbe", "table")) == 8  # 2 RBE2 x 4 subcases


def test_missing_file_is_block_error(tmp_path):
    p = PipelineModel()
    p.add_block("nastran.hdf5_results", block_id="x", file="nope.h5", result="NODAL/DISPLACEMENT")
    result = Executor(default_registry()).run(p, tmp_path)
    assert result.results["x"].state is BlockState.ERROR
    assert "File not found" in result.results["x"].error


def test_cli_run_validate_and_blocks(tmp_path):
    shutil.copy(PLATE_H5, tmp_path / PLATE_H5.name)
    save_pipeline(plate_pipeline(), tmp_path / "plate.apb.json")
    runner = CliRunner()

    res = runner.invoke(app, ["validate", str(tmp_path / "plate.apb.json")])
    assert res.exit_code == 0, res.output
    res = runner.invoke(app, ["run", str(tmp_path / "plate.apb.json")])
    assert res.exit_code == 0, res.output
    assert "Pipeline completed" in res.output
    res = runner.invoke(app, ["run", str(tmp_path / "plate.apb.json"), "--set", "h5_file=other.h5"])
    assert res.exit_code == 1
    res = runner.invoke(app, ["blocks"])
    assert "nastran.hdf5_results" in res.output


def test_cli_reports_invalid_pipeline(tmp_path):
    p = PipelineModel()
    p.add_block("nastran.hdf5_results", block_id="x", file="a.h5")  # 'result' missing
    save_pipeline(p, tmp_path / "bad.apb.json")
    res = CliRunner().invoke(app, ["validate", str(tmp_path / "bad.apb.json")])
    assert res.exit_code == 1
    assert "result" in res.output


def test_example_pipeline_runs():
    from apb.engine import load_pipeline
    from tests.conftest import DATA_DIR

    path = DATA_DIR.parent / "plate_stress.apb.json"
    result = Executor(default_registry()).run(load_pipeline(path), path.parent)
    assert result.ok
