from __future__ import annotations

import math

import h5py
import numpy as np
import pytest

from apb.blocks.nastran import h5reader as r
from apb.units import UnitSystem
from tests.conftest import JOINT_H5, PLATE_H5


def test_list_results():
    infos = {i.path: i for i in r.list_results(PLATE_H5)}
    assert infos["ELEMENTAL/STRESS/QUAD4"].rows == 120
    assert infos["ELEMENTAL/STRESS/QUAD4"].known
    assert "DOMAINS" not in infos


def test_subcases():
    assert r.subcases(PLATE_H5) == [1, 2, 3, 10]


@pytest.mark.parametrize(
    ("text", "ranges"),
    [
        ("", []),
        ("5", [(5, 5)]),
        ("1001-1200, 3005", [(1001, 1200), (3005, 3005)]),
        ("1 - 3;7 9", [(1, 3), (7, 7), (9, 9)]),
    ],
)
def test_parse_id_filter(text, ranges):
    assert r.parse_id_filter(text) == ranges


@pytest.mark.parametrize("bad", ["abc", "5-2", "1-2-3"])
def test_parse_id_filter_errors(bad):
    with pytest.raises(r.NastranH5Error):
        r.parse_id_filter(bad)


def test_quad4_stress_with_filters_units_and_von_mises():
    df = r.read_result(
        PLATE_H5, "STRESS/QUAD4", subcases=[1, 3], ids="101-102, 140", unit_system=UnitSystem.SI_MM
    )
    assert len(df) == 6
    assert list(df.columns[:2]) == ["EID", "SUBCASE"]
    assert "MODE" not in df.columns  # static subcases only
    assert set(df["EID"]) == {101, 102, 140}
    row = df.iloc[0]
    sx, sy, txy = row["SX_Z1"], row["SY_Z1"], row["TXY_Z1"]
    assert row["VON_MISES_Z1"] == pytest.approx(math.sqrt(sx**2 - sx * sy + sy**2 + 3 * txy**2))
    assert df.attrs["units"]["VON_MISES"] == "MPa"
    assert df.attrs["unit_system"] == "SI_mm"
    assert df.attrs["result"] == "ELEMENTAL/STRESS/QUAD4"


def test_units_follow_unit_system():
    df = r.read_result(PLATE_H5, "NODAL/DISPLACEMENT", ids="1", unit_system=UnitSystem.SI)
    assert df.attrs["units"]["Z"] == "m"
    assert df.attrs["units"]["RY"] == "rad"


def test_column_selection_keeps_keys():
    df = r.read_result(PLATE_H5, "NODAL/DISPLACEMENT", columns=["Z"], subcases=[1])
    assert list(df.columns) == ["GRID", "SUBCASE", "Z"]
    with pytest.raises(r.NastranH5Error, match="not in result"):
        r.read_result(PLATE_H5, "NODAL/DISPLACEMENT", columns=["NOPE"])


def test_modal_results_get_mode_columns():
    df = r.read_result(PLATE_H5, "NODAL/EIGENVECTOR", ids="55")
    assert list(df.columns[:4]) == ["GRID", "SUBCASE", "MODE", "EIGENVALUE"]
    assert df["MODE"].tolist() == [1, 2, 3, 4, 5, 6]


def test_eigenvalues():
    df = r.read_eigenvalues(PLATE_H5, UnitSystem.SI_MM)
    assert df["FREQ"].tolist()[0] == pytest.approx(18.4)
    assert df.columns.tolist().count("MODE") == 1
    assert df.attrs["units"]["FREQ"] == "Hz"


def test_beam_one_row_per_station():
    df = r.read_result(PLATE_H5, "ELEMENT_FORCE/BEAM", ids="201", subcases=[1])
    assert df["STATION"].tolist() == [1, 2]
    assert df["SD"].tolist() == [0.0, 1.0]
    assert df["GRID"].tolist() == [23, 24]


def test_cbush_forces():
    df = r.read_result(JOINT_H5, "ELEMENT_FORCE/BUSH", subcases=[3])
    assert df["EID"].tolist() == [1001, 1002, 1003, 1004]
    assert (df["FX"] > 0).all()  # tension along element x


def test_rbe2_loads_sources_agree():
    mpc = r.read_rbe2_loads(JOINT_H5, source="mpc_force", unit_system=UnitSystem.SI_MM)
    gpf = r.read_rbe2_loads(JOINT_H5, source="gpforce", unit_system=UnitSystem.SI_MM)
    assert list(mpc.columns) == ["EID", "GRID", "SUBCASE", "FX", "FY", "FZ", "MX", "MY", "MZ"]
    assert set(mpc["EID"]) == {2001, 2002}
    assert mpc[["FX", "FY", "FZ"]].to_numpy() == pytest.approx(gpf[["FX", "FY", "FZ"]].to_numpy())
    assert mpc.attrs["units"]["MY"] == "N·mm"


def test_rbe2_independent_side_and_filter():
    df = r.read_rbe2_loads(JOINT_H5, side="independent", eids="2002", subcases=[1])
    assert df["GRID"].tolist() == [16]
    assert df["FX"].iloc[0] < 0


def test_masses():
    df = r.read_masses(PLATE_H5, UnitSystem.SI_MM)
    assert df["MASS"].tolist() == [0.002, 0.002]
    assert df.attrs["units"]["MASS"] == "t"


def test_unknown_dataset_is_read_raw(tmp_path):
    path = tmp_path / "raw.h5"
    with h5py.File(path, "w") as h5:
        dom = np.zeros(1, dtype=[("ID", "<i8"), ("SUBCASE", "<i8")])
        dom[0] = (1, 7)
        h5["/NASTRAN/RESULT/DOMAINS"] = dom
        data = np.zeros(2, dtype=[("EID", "<i8"), ("V", "<f8", (2,)), ("DOMAIN_ID", "<i8")])
        data["EID"] = [5, 6]
        data["V"] = [[1, 2], [3, 4]]
        data["DOMAIN_ID"] = 1
        h5["/NASTRAN/RESULT/ELEMENTAL/STRESS/NEWTYPE"] = data
    info = r.list_results(path)[0]
    assert not info.known
    df = r.read_result(path, "STRESS/NEWTYPE", ids="6")
    assert df.to_dict("records") == [{"EID": 6, "SUBCASE": 7, "V_1": 3.0, "V_2": 4.0}]


def test_errors():
    with pytest.raises(r.NastranH5Error, match="File not found"):
        r.list_results("missing.h5")
    with pytest.raises(r.NastranH5Error, match="not found"):
        r.read_result(PLATE_H5, "STRESS/TRIA3")
    with pytest.raises(r.NastranH5Error, match="Subcase"):
        r.read_result(PLATE_H5, "STRESS/QUAD4", subcases=[99])
