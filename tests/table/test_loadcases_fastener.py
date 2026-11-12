from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apb.blocks.loads.fastener import FastenerLoads, fastener_loads
from apb.blocks.table.common import TableError
from apb.blocks.table.loadcases import CaseLabels, LoadCaseTable, load_case_table
from apb.engine import Executor, default_registry, load_pipeline
from apb.units import UnitSystem, convert_table, get_units, set_units
from tests.conftest import DATA_DIR, run_block


def loads() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "EID": [1, 1, 1, 2, 2, 2],
            "SUBCASE": [10, 20, 30, 10, 20, 30],
            "TENSION": [100.0, -50.0, 20.0, 5.0, 80.0, -90.0],
            "SHEAR": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )
    set_units(df, UnitSystem.SI_MM, {"TENSION": "N", "SHEAR": "N"})
    return df


def test_single_value_column_is_flat():
    t = load_case_table(
        loads(),
        id_columns=["EID"],
        case_column="SUBCASE",
        value_columns=["TENSION"],
        envelope=["max", "abs_max"],
    )
    assert list(t.columns) == [
        "EID",
        "LC10",
        "LC20",
        "LC30",
        "MAX",
        "MAX_CASE",
        "ABS_MAX",
        "ABS_MAX_CASE",
    ]
    assert t["MAX"].tolist() == [100.0, 80.0]
    assert t["MAX_CASE"].tolist() == ["LC10", "LC20"]
    assert t["ABS_MAX"].tolist() == [100.0, -90.0]
    assert get_units(t)["LC20"] == "N" and "MAX_CASE" not in get_units(t)


def test_side_by_side_with_labels_and_conversion():
    t = load_case_table(
        loads(),
        id_columns=["EID"],
        case_column="SUBCASE",
        value_columns=["TENSION", "SHEAR"],
        labels={10: "Landing", "20": "Gust"},
    )
    assert isinstance(t.columns, pd.MultiIndex)
    assert t.columns[0] == ("EID", "")
    assert list(t["TENSION"].columns) == ["Landing", "Gust", "LC30", "MAX", "MAX_CASE"]
    assert t[("SHEAR", "MAX_CASE")].tolist() == ["LC30", "LC30"]
    assert get_units(t) == {"TENSION": "N", "SHEAR": "N"}
    # MultiIndex tables convert too (N stays N, so check a fake stress unit)
    set_units(t, UnitSystem.SI_MM, {"TENSION": "MPa"})
    si = convert_table(t, UnitSystem.SI)
    assert si[("TENSION", "Landing")].iloc[0] == pytest.approx(100e6)
    assert si[("TENSION", "MAX_CASE")].iloc[0] == "Landing"


def test_flat_layout_names():
    t = load_case_table(
        loads(),
        id_columns=["EID"],
        case_column="SUBCASE",
        value_columns=["TENSION", "SHEAR"],
        layout="flat",
        envelope=[],
    )
    assert list(t.columns) == [
        "EID",
        "TENSION_LC10",
        "TENSION_LC20",
        "TENSION_LC30",
        "SHEAR_LC10",
        "SHEAR_LC20",
        "SHEAR_LC30",
    ]


def test_missing_case_is_nan_and_envelope_ignores_it():
    df = loads().iloc[:-1]  # EID 2 has no SUBCASE 30
    t = load_case_table(
        df, id_columns=["EID"], case_column="SUBCASE", value_columns=["TENSION"], envelope=["min"]
    )
    assert np.isnan(t.loc[1, "LC30"])
    assert t.loc[1, "MIN"] == 5.0


def test_duplicates_and_bad_labels_are_reported():
    df = pd.concat([loads(), loads()])
    with pytest.raises(TableError, match="Several rows per entity"):
        load_case_table(df, id_columns=["EID"], case_column="SUBCASE", value_columns=["SHEAR"])
    with pytest.raises(TableError, match="not unique"):
        load_case_table(
            loads(),
            id_columns=["EID"],
            case_column="SUBCASE",
            value_columns=["SHEAR"],
            labels={10: "A", 20: "A"},
        )


def test_block_with_names_input():
    names = run_block(CaseLabels, labels={10: "Landing", 30: "Crash"})["table"]
    t = run_block(
        LoadCaseTable,
        {"table": loads(), "labels": names},
        value_columns=["SHEAR"],
        labels={"30": "Crash 9g"},
    )["table"]
    assert list(t.columns[1:4]) == ["Landing", "LC20", "Crash 9g"]


def forces() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "EID": [7, 7],
            "SUBCASE": [1, 2],
            "FX": [3.0, -4.0],
            "FY": [3.0, 0.0],
            "FZ": [4.0, 0.0],
            "MX": [1.0, 0.0],
            "MY": [3.0, 0.0],
            "MZ": [4.0, 0.0],
        }
    )
    set_units(
        df,
        UnitSystem.SI_MM,
        {"FX": "N", "FY": "N", "FZ": "N", "MX": "N·mm", "MY": "N·mm", "MZ": "N·mm"},
    )
    return df


def test_fastener_loads():
    t = fastener_loads(forces(), axial_axis="X", include_moments=True)
    assert list(t.columns) == ["EID", "SUBCASE", "TENSION", "SHEAR", "BENDING", "TORSION"]
    assert t["TENSION"].tolist() == [3.0, -4.0]
    assert t["SHEAR"].tolist() == [5.0, 0.0]
    assert t["BENDING"].tolist() == [5.0, 0.0]
    assert get_units(t) == {"TENSION": "N", "SHEAR": "N", "BENDING": "N·mm", "TORSION": "N·mm"}


def test_fastener_tension_only_and_other_axis():
    t = run_block(
        FastenerLoads,
        {"forces": forces()},
        axial_axis="Z",
        tension_mode="tension_only",
        keep_forces=True,
    )["loads"]
    assert t["TENSION"].tolist() == [4.0, 0.0]
    assert t["SHEAR"].tolist() == [pytest.approx(18**0.5), 4.0]
    assert "FX" in t.columns


def test_fastener_missing_columns():
    with pytest.raises(TableError, match="forces table"):
        fastener_loads(pd.DataFrame({"EID": [1], "FX": [1.0]}))


def test_fastener_example_pipeline(tmp_path):
    path = DATA_DIR.parent / "fastener_loads.apb.json"
    result = Executor(default_registry()).run(load_pipeline(path), path.parent, output_dir=tmp_path)
    assert result.ok
    table = result.output("lct", "table")
    assert len(table) == 6  # 4 CBUSH + 2 RBE2 fasteners
    assert table[("TENSION", "MAX_CASE")].iloc[0] == "Pull-off"
    crit = result.output("crit", "table")
    assert crit.loc[0, "SHEAR_EID"] == 1001
    assert (tmp_path / "output" / "fastener_loads.xlsx").exists()
