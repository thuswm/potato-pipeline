"""Tables read from the .f06: modal effective mass fraction, grid point weight (spec 02 §3.11)."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from apb.blocks.nastran.f06tables import (
    F06TableError,
    read_eigenvalues,
    read_grid_weight,
    read_mass_fractions,
)
from apb.blocks.nastran.h5reader import read_result
from apb.engine import Executor, PipelineModel, default_registry
from apb.units import get_units
from tests.conftest import FEM_DIR

MODAL_F06 = FEM_DIR / "modal.f06"
FRACTIONS = ["T1_FRAC", "T2_FRAC", "T3_FRAC", "R1_FRAC", "R2_FRAC", "R3_FRAC"]


def modal_text() -> str:
    return MODAL_F06.read_text(encoding="utf-8")


def test_mass_fractions_are_one_table_equal_to_the_h5():
    table = read_mass_fractions(modal_text())
    assert list(table.columns) == ["MODE", "FREQUENCY", *FRACTIONS]
    assert table["MODE"].tolist() == list(range(1, 11))
    h5 = read_result(FEM_DIR / "modal.h5", "MODAL/MODAL_MASS_FRACTION")
    assert np.allclose(table.to_numpy(float), h5[table.columns].to_numpy(float), rtol=1e-6)
    with_sums = read_mass_fractions(modal_text(), sums=True)
    assert with_sums.columns[-6:].tolist() == [c.replace("FRAC", "SUM") for c in FRACTIONS]
    assert with_sums["R1_SUM"].iloc[-1] == pytest.approx(0.5330817)


def test_a_table_over_two_pages_is_read_as_one():
    lines = modal_text().splitlines()
    title = next(i for i, line in enumerate(lines) if "(FOR TRANSLATIONAL" in line)
    first_row = next(i for i in range(title, len(lines)) if lines[i].strip().startswith("1 "))
    header = lines[title - 1 : first_row]  # title, kind, column heads, blank lines
    page_break = ["1    MODAL      PAGE    99", "", *header]
    split = [*lines[: first_row + 4], *page_break, *lines[first_row + 4 :]]
    assert read_mass_fractions("\n".join(split))["MODE"].tolist() == list(range(1, 11))


def test_mass_fraction_problems_are_explained():
    with pytest.raises(F06TableError, match="MEFFMASS"):
        read_mass_fractions("no tables here")
    text = modal_text()
    only_t = text[: text.index("(FOR ROTATIONAL")]
    with pytest.raises(F06TableError, match=r"rotational .* missing"):
        read_mass_fractions(only_t)
    start = text.index("MODAL EFFECTIVE MASS FRACTION") - 60
    block = text[start : text.index("MODAL PARTICIPATION")]
    with pytest.raises(F06TableError, match="appears twice"):
        read_mass_fractions(text + "\n" + block)


@pytest.mark.parametrize("name", ["modal.f06", "global_loads.f06"])
def test_grid_weight_matches_the_h5(name: str):
    table, count = read_grid_weight((FEM_DIR / name).read_text(encoding="utf-8"))
    assert count == 1 and table.attrs["reference_point"] == 0
    assert table["DIRECTION"].tolist() == ["X", "Y", "Z"]
    h5 = read_result(FEM_DIR / name.replace(".f06", ".h5"), "NODAL/GRID_WEIGHT")
    cols = ["MASS", "X_CG", "Y_CG", "Z_CG"]
    assert np.allclose(table[cols].to_numpy(), h5[cols].to_numpy(), rtol=1e-6, atol=1e-12)


def test_grid_weight_problems():
    with pytest.raises(F06TableError, match="GRDPNT"):
        read_grid_weight("nothing")
    text = modal_text()
    _, count = read_grid_weight(text + text)
    assert count == 2


def test_f06_blocks_read_from_an_analysis_block_without_h5(tmp_path: Path):
    """Round 6: a modal run read from the .f06 only; the .h5 output is not used."""
    for name in ("modal.bdf", "modal.f06", "modal.apb-run.json"):
        shutil.copy(FEM_DIR / name, tmp_path / name)
    p = PipelineModel()
    p.add_block("nastran.run", block_id="run", input_file="modal.bdf", run_mode="never")
    p.add_block("nastran.f06_mass_fraction", block_id="meff", sums=True)
    p.add_block("nastran.f06_grid_weight", block_id="gpwg", unit_system="SI")
    p.connect("run.f06", "meff.file")
    p.connect("run.f06", "gpwg.file")
    result = Executor(default_registry()).run(p, tmp_path)
    assert result.ok, {k: r.error for k, r in result.results.items() if r.error}
    meff = result.output("meff", "table")
    assert len(meff) == 10 and get_units(meff)["FREQUENCY"] == "Hz"
    gpwg = result.output("gpwg", "table")
    assert gpwg["MASS"].tolist() == [1225.0] * 3 and get_units(gpwg)["MASS"] == "kg"
    assert any("reference point 0" in line for line in result.results["gpwg"].log)


def test_eigenvalues_equal_the_h5():
    """Round 6 re-test: the eigenvalue table of the .f06 has the columns of the .h5."""
    table = read_eigenvalues(modal_text())
    h5 = read_result(FEM_DIR / "modal.h5", "SUMMARY/EIGENVALUE")
    assert list(table.columns) == [c for c in h5.columns if c not in ("RESFLG", "FLDFLG")]
    assert table["MODE"].tolist() == list(range(1, 11))
    assert np.allclose(table.to_numpy(float), h5[table.columns].to_numpy(float), rtol=1e-6)


def test_eigenvalue_problems_are_explained():
    with pytest.raises(F06TableError, match="REAL EIGENVALUES"):
        read_eigenvalues("no tables here")
    text = modal_text()
    start = text.index("R E A L   E I G E N V A L U E S")
    with pytest.raises(F06TableError, match="appears twice"):
        read_eigenvalues(text + "\n" + text[start : start + 2000])


def test_eigenvalue_block_reads_h5_or_f06_with_the_same_units(tmp_path: Path):
    for name in ("modal.bdf", "modal.f06", "modal.h5", "modal.apb-run.json"):
        shutil.copy(FEM_DIR / name, tmp_path / name)
    p = PipelineModel()
    p.add_block("nastran.run", block_id="run", input_file="modal.bdf", run_mode="never")
    p.add_block("nastran.hdf5_eigenvalues", block_id="from_f06", unit_system="SI")
    p.add_block("nastran.hdf5_eigenvalues", block_id="typed", file="modal.f06", unit_system="SI")
    p.add_block("nastran.hdf5_eigenvalues", block_id="from_h5", file="modal.h5", unit_system="SI")
    p.connect("run.f06", "from_f06.file")
    result = Executor(default_registry()).run(p, tmp_path)
    assert result.ok, {k: r.error for k, r in result.results.items() if r.error}
    f06 = result.output("from_f06", "table")
    h5 = result.output("from_h5", "table")
    assert get_units(f06) == {k: v for k, v in get_units(h5).items() if k in f06}
    assert get_units(f06)["FREQ"] == "Hz"
    assert result.output("typed", "table")["MODE"].tolist() == f06["MODE"].tolist()


def test_wrong_file_kind_is_explained(tmp_path: Path):
    """An .f06 to an HDF5 block and an .h5 to an f06 block say what to use instead."""
    for name in ("modal.f06", "modal.h5"):
        shutil.copy(FEM_DIR / name, tmp_path / name)
    # An .f06 under the name .h5: the content counts, not the suffix.
    shutil.copy(FEM_DIR / "modal.f06", tmp_path / "renamed.h5")
    p = PipelineModel()
    p.add_block(
        "nastran.hdf5_results", block_id="res", file="modal.f06", result="NODAL/GRID_WEIGHT"
    )
    p.add_block("nastran.hdf5_eigenvalues", block_id="eig", file="renamed.h5")
    p.add_block("nastran.f06_mass_fraction", block_id="meff", file="modal.h5")
    result = Executor(default_registry()).run(p, tmp_path)
    assert "not an HDF5 file" in result.results["res"].error
    assert "Nastran f06" in result.results["res"].error
    assert "not an HDF5 file" in result.results["eig"].error
    assert "is an HDF5 file" in result.results["meff"].error
