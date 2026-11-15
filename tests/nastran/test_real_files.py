"""Reader tests against real MSC Nastran 2023.3 files (NAS-17).

Expected values are taken from the ``.f06`` files next to the ``.h5`` files in
``examples/fem``.
"""

from __future__ import annotations

import pytest

from apb.blocks.nastran import h5reader as r
from apb.blocks.nastran.blocks import Hdf5Results
from apb.units import UnitSystem
from tests.conftest import GLOBAL_H5, LOCAL_H5, MODAL_H5, PLATE_H5, run_block


def test_file_info():
    info = r.file_info(MODAL_H5)
    assert (info.version, info.solution, info.input_deck) == ("msc20233", 103, "modal.bdf")
    assert info.version_label == "MSC Nastran 2023.3"
    assert info.label.startswith("MSC Nastran 2023.3 · SOL 103 · modal.bdf · Sun Nov 15")
    assert not info.synthetic
    assert r.file_info(GLOBAL_H5).solution == 101
    assert r.file_info(PLATE_H5).synthetic


def test_list_results_names_and_sizes():
    infos = {i.path: i for i in r.list_results(MODAL_H5)}
    assert infos["MODAL/MODAL_EFFECTIVE_MASS"].title == "Modal effective mass"
    assert infos["MODAL/MODAL_EFFECTIVE_MASS"].size == "10 rows"
    assert infos["MODAL/EFFECTIVE_MASS_MATRIX"].size == "6 x 6"
    assert infos["MODAL/TOTAL_MASS_FRACTION"].size == "1 row"
    assert infos["NODAL/EIGENVECTOR"].rows == 30080
    assert not infos["ELEMENTAL/ENERGY/IDENT"].known  # still listed, read raw


def test_cbush_forces_match_f06():
    df = r.read_result(GLOBAL_H5, "ELEMENT_FORCE/BUSH", unit_system=UnitSystem.SI)
    assert list(df.columns) == ["EID", "SUBCASE", "FX", "FY", "FZ", "MX", "MY", "MZ"]
    assert df["EID"].tolist() == [17, 18, 19]
    assert df["SUBCASE"].tolist() == [1, 1, 1]
    assert df["FZ"].tolist() == pytest.approx([2500.0, 1500.0, 500.0])
    assert df["MY"].tolist() == pytest.approx([-300.0, -300.0, -275.0])
    assert df.attrs["units"]["FZ"] == "N"
    assert df.attrs["units"]["MY"] == "N·m"
    assert df.attrs["nastran_version"] == "msc20233"
    assert df.attrs["nastran_solution"] == "101"
    assert df.attrs["nastran_input"] == "global_loads.bdf"


def test_static_nodal_results():
    spc = r.read_result(GLOBAL_H5, "NODAL/SPC_FORCE", ids="71")
    assert spc[["GRID", "SUBCASE"]].to_dict("records") == [{"GRID": 71, "SUBCASE": 1}]
    assert spc["Z"].iloc[0] == pytest.approx(-2750.0)
    load = r.read_result(GLOBAL_H5, "NODAL/APPLIED_LOAD", unit_system=UnitSystem.SI)
    assert load.columns[0] == "GRID"
    assert load["Z"].sum() == pytest.approx(2750.0)  # 550 kg x 5 m/s2
    assert load.attrs["units"]["Z"] == "N"


def test_local_loads_are_all_zero():
    # Massless model with a gravity load (f06 warning 9058), see spec 02 §3.9.
    df = r.read_result(LOCAL_H5, "ELEMENT_FORCE/BUSH")
    assert df["EID"].tolist() == [2, 3, 4, 5]
    assert (df[["FX", "FY", "FZ", "MX", "MY", "MZ"]] == 0).all().all()


def test_eigenvalue_summary_has_no_bogus_subcase():
    df = r.read_eigenvalues(MODAL_H5, UnitSystem.SI)
    assert "SUBCASE" not in df.columns
    assert df["MODE"].tolist() == list(range(1, 11))
    assert df["FREQ"].iloc[0] == pytest.approx(25.71186, rel=1e-6)
    assert df["EIGENVALUE"].iloc[0] == pytest.approx(26099.18, rel=1e-6)


def test_modal_effective_mass():
    df = r.read_result(MODAL_H5, "MODAL/MODAL_EFFECTIVE_MASS", unit_system=UnitSystem.SI)
    assert list(df.columns) == ["SUBCASE", "MODE", "FREQUENCY", "T1", "T2", "T3", "R1", "R2", "R3"]
    assert "EIGENVALUE" not in df.columns
    assert df["MODE"].tolist() == list(range(1, 11))
    row = df.iloc[2]  # mode 3
    assert row["FREQUENCY"] == pytest.approx(30.96907, rel=1e-6)
    assert row["T2"] == pytest.approx(71.39035, rel=1e-6)
    assert row["R1"] == pytest.approx(1011.542, rel=1e-6)
    assert df.attrs["units"] == {
        "FREQUENCY": "Hz",
        **dict.fromkeys(("T1", "T2", "T3"), "kg"),
        **dict.fromkeys(("R1", "R2", "R3"), "kg·m²"),
    }


def test_modal_mass_fractions_and_total():
    frac = r.read_result(MODAL_H5, "MODAL/MODAL_MASS_FRACTION")
    assert frac["T2_SUM"].iloc[-1] == pytest.approx(0.3054081, rel=1e-6)
    total = r.read_result(MODAL_H5, "MODAL/TOTAL_MASS_FRACTION")
    assert len(total) == 1
    assert total["T2"].iloc[0] == pytest.approx(0.3054081, rel=1e-6)
    assert "MODE" not in total.columns


def test_matrices_are_six_by_six():
    eff = r.read_result(MODAL_H5, "MODAL/EFFECTIVE_MASS_MATRIX")
    assert list(eff.columns) == ["SUBCASE", "DOF", "T1", "T2", "T3", "R1", "R2", "R3"]
    assert eff["DOF"].tolist() == ["T1", "T2", "T3", "R1", "R2", "R3"]
    by_dof = eff.set_index("DOF")
    assert by_dof.loc["T1", "T1"] == pytest.approx(288.3774, rel=1e-6)
    assert by_dof.loc["T2", "R1"] == pytest.approx(-1028.551, rel=1e-6)
    assert by_dof.loc["R1", "T2"] == pytest.approx(-1028.551, rel=1e-6)
    assert by_dof.loc["R3", "R3"] == pytest.approx(104.8185, rel=1e-6)
    rigid = r.read_result(MODAL_H5, "MODAL/RIGID_BODY_MASS_MATRIX", columns=["T1", "R2"])
    assert list(rigid.columns) == ["SUBCASE", "DOF", "T1", "R2"]  # row labels are kept
    assert rigid.set_index("DOF").loc["T1", "R2"] == pytest.approx(2129.5)


def test_grid_point_weight():
    df = r.read_result(MODAL_H5, "NODAL/GRID_WEIGHT", unit_system=UnitSystem.SI)
    assert list(df.columns) == ["DIRECTION", "MASS", "X_CG", "Y_CG", "Z_CG"]
    assert df["DIRECTION"].tolist() == ["X", "Y", "Z"]
    assert df["MASS"].tolist() == pytest.approx([1225.0] * 3)
    assert df["Z_CG"].tolist() == pytest.approx([1.738367, 1.738367, 0.0], rel=1e-6)
    assert df["X_CG"].iloc[1] == pytest.approx(0.08081633, rel=1e-6)
    assert df.attrs["units"] == {"MASS": "kg", "X_CG": "m", "Y_CG": "m", "Z_CG": "m"}


def test_eigenvectors_and_strain_energy_keep_mode_columns():
    vec = r.read_result(MODAL_H5, "NODAL/EIGENVECTOR", ids="1")
    assert list(vec.columns[:4]) == ["GRID", "SUBCASE", "MODE", "EIGENVALUE"]
    assert vec["MODE"].tolist() == list(range(1, 11))
    assert vec["EIGENVALUE"].iloc[0] == pytest.approx(26099.18, rel=1e-6)
    ese = r.read_result(
        MODAL_H5, "ELEMENTAL/ENERGY/STRAIN_ELEM", ids="1501", unit_system=UnitSystem.SI
    )
    assert list(ese.columns[:3]) == ["EID", "SUBCASE", "MODE"]
    assert ese.attrs["units"]["ENERGY"] == "J"
    assert ese.attrs["units"]["DENSITY"] == "J/m³"


def test_masses_from_real_file():
    df = r.read_masses(GLOBAL_H5, UnitSystem.SI)
    assert df["EID"].tolist() == [6, 9, 12, 16]
    assert df["MASS"].tolist() == pytest.approx([50.0, 200.0, 200.0, 100.0])


def test_max_rows():
    assert len(r.read_result(MODAL_H5, "NODAL/EIGENVECTOR", max_rows=200)) == 200
    df = r.read_result(MODAL_H5, "NODAL/EIGENVECTOR", ids="5-3564", max_rows=7)
    assert df["GRID"].min() == 5
    assert len(df) == 7
    assert len(r.read_result(GLOBAL_H5, "ELEMENT_FORCE/BUSH", max_rows=200)) == 3
    assert len(r.read_result(MODAL_H5, "MODAL/EFFECTIVE_MASS_MATRIX", max_rows=1)) == 6


def test_describe_result():
    d = r.describe_result(MODAL_H5, "NODAL/EIGENVECTOR", UnitSystem.SI)
    assert d.title == "Eigenvectors (mode shapes)"
    assert d.rows == 30080
    assert d.columns == ("X", "Y", "Z", "RX", "RY", "RZ")
    assert d.subcases == (1,)
    assert d.modes == tuple(range(1, 11))
    assert (d.id_column, d.id_min, d.id_max, d.id_count) == ("GRID", 1, 3564, 3008)

    d = r.describe_result(GLOBAL_H5, "ELEMENT_FORCE/BUSH", UnitSystem.SI)
    assert d.path == "ELEMENTAL/ELEMENT_FORCE/BUSH"
    assert (d.id_column, d.id_min, d.id_max, d.id_count) == ("EID", 17, 19, 3)
    assert d.units["FX"] == "N"

    d = r.describe_result(MODAL_H5, "MODAL/MODAL_EFFECTIVE_MASS")
    assert d.id_column == ""
    assert d.subcases == (1,)
    assert d.columns == ("FREQUENCY", "T1", "T2", "T3", "R1", "R2", "R3")
    assert r.describe_result(MODAL_H5, "SUMMARY/EIGENVALUE").subcases == ()


def test_describe_result_without_index_group():
    # The synthetic files have no /INDEX group: domains come from the DOMAIN_ID column.
    d = r.describe_result(PLATE_H5, "STRESS/QUAD4")
    assert d.subcases == (1, 2, 3)
    assert (d.id_min, d.id_max, d.id_count) == (101, 140, 40)
    assert "VON_MISES" in d.columns
    with pytest.raises(r.NastranH5Error, match="not found"):
        r.describe_result(PLATE_H5, "STRESS/TRIA3")


def test_block_reads_real_file():
    out = run_block(
        Hdf5Results,
        file=str(MODAL_H5),
        result="MODAL/MODAL_EFFECTIVE_MASS",
        columns=["FREQUENCY", "T1"],
        unit_system="SI",
    )
    assert list(out["table"].columns) == ["SUBCASE", "MODE", "FREQUENCY", "T1"]
    assert len(out["table"]) == 10
