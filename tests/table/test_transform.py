from __future__ import annotations

import pandas as pd
import pytest

from apb.blocks.table.common import TableError
from apb.blocks.table.transform import (
    Compute,
    Concat,
    Envelope,
    Filter,
    Join,
    Pick,
    Select,
    Sort,
)
from apb.units import Quantity, UnitSystem, get_units, set_units
from tests.conftest import run_block


def stresses() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "EID": [1, 1, 1, 2, 2, 2],
            "SUBCASE": [1, 2, 3, 1, 2, 3],
            "S": [10.0, -30.0, 20.0, 5.0, 8.0, -2.0],
            "F": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )
    df.attrs["title"] = "Stresses"
    set_units(df, UnitSystem.SI_MM, {"S": "MPa", "F": "N"})
    return df


def test_select_and_rename_keep_units():
    out = run_block(Select, {"table": stresses()}, columns=["EID", "S"], rename={"S": "STRESS"})
    t = out["table"]
    assert list(t.columns) == ["EID", "STRESS"]
    assert get_units(t) == {"STRESS": "MPa"}
    assert t.attrs["title"] == "Stresses"


def test_select_missing_column_lists_available():
    with pytest.raises(TableError, match=r"\['X'\] not found.*Available: EID"):
        run_block(Select, {"table": stresses()}, columns=["X"])


def test_filter():
    t = run_block(Filter, {"table": stresses()}, expression="SUBCASE in [1, 3] and S > 0")["table"]
    assert t["S"].tolist() == [10.0, 20.0, 5.0]
    assert get_units(t)["S"] == "MPa"
    with pytest.raises(TableError, match="Invalid filter"):
        run_block(Filter, {"table": stresses()}, expression="NOPE >")


def test_sort():
    t = run_block(Sort, {"table": stresses()}, by=["S"], ascending=False)["table"]
    assert t["S"].tolist()[:2] == [20.0, 10.0]


def test_compute_with_unit():
    t = run_block(Compute, {"table": stresses()}, new_column="R", expression="sqrt(F**2)", unit="N")
    assert t["table"]["R"].tolist() == [1, 2, 3, 4, 5, 6]
    assert get_units(t["table"])["R"] == "N"
    plain = run_block(Compute, {"table": stresses()}, new_column="F", expression="F * 2")["table"]
    assert "F" not in get_units(plain)  # replaced column without unit loses the old unit


@pytest.mark.parametrize(
    ("mode", "values", "cases"),
    [
        ("max", [20.0, 8.0], [3, 2]),
        ("min", [-30.0, -2.0], [2, 3]),
        ("abs_max", [-30.0, 8.0], [2, 2]),
    ],
)
def test_envelope_modes(mode, values, cases):
    t = run_block(Envelope, {"table": stresses()}, group_by=["EID"], columns=["S"], mode=mode)
    t = t["table"]
    assert t["S"].tolist() == values
    assert t["S_SUBCASE"].tolist() == cases
    assert get_units(t) == {"S": "MPa"}


def test_envelope_whole_table_reports_entity():
    t = run_block(
        Envelope, {"table": stresses()}, columns=["S", "F"], governing_columns=["EID", "SUBCASE"]
    )["table"]
    assert t.to_dict("records") == [
        {"S": 20.0, "S_EID": 1, "S_SUBCASE": 3, "F": 6.0, "F_EID": 2, "F_SUBCASE": 3}
    ]


def test_envelope_keep_row():
    t = run_block(
        Envelope,
        {"table": stresses()},
        group_by=["EID"],
        columns=["S"],
        mode="abs_max",
        keep_row=True,
    )["table"]
    assert t[["EID", "SUBCASE", "F"]].values.tolist() == [[1, 2, 2.0], [2, 2, 5.0]]
    with pytest.raises(TableError, match="exactly one"):
        run_block(Envelope, {"table": stresses()}, columns=["S", "F"], keep_row=True)


def test_join_with_names_and_clash():
    names = pd.DataFrame({"EID": [1, 2], "NAME": ["A", "B"], "F": [0.5, 0.6]})
    set_units(names, UnitSystem.SI_MM, {"F": "N"})
    t = run_block(Join, {"left": stresses(), "right": names}, on=["EID"])["table"]
    assert t.loc[0, "NAME"] == "A"
    assert "F_RIGHT" in t.columns
    assert get_units(t)["F_RIGHT"] == "N"


def test_join_refuses_mixed_unit_systems():
    right = pd.DataFrame({"EID": [1]})
    set_units(right, UnitSystem.SI, {})
    with pytest.raises(TableError, match="different unit systems"):
        run_block(Join, {"left": stresses(), "right": right}, on=["EID"])


def test_concat_with_source():
    a, b = stresses(), stresses()
    a.attrs["source_file"] = "/x/run1.h5"
    b.attrs["source_file"] = "/x/run2.h5"
    t = run_block(Concat, {"tables": [a, b]}, add_source_column=True)["table"]
    assert len(t) == 12
    assert t["SOURCE"].unique().tolist() == ["run1.h5", "run2.h5"]
    assert get_units(t)["S"] == "MPa"


def test_concat_refuses_conflicting_units():
    b = stresses()
    set_units(b, UnitSystem.SI_MM, {"S": "N"})
    with pytest.raises(TableError, match="different units"):
        run_block(Concat, {"tables": [stresses(), b]})


def test_pick():
    v = run_block(Pick, {"table": stresses()}, column="S", row="abs_max")["value"]
    assert v == Quantity(-30.0, "MPa")
    v = run_block(Pick, {"table": stresses()}, column="F", row="max", where="EID == 1")["value"]
    assert v == Quantity(3.0, "N")
    plain = pd.DataFrame({"X": [1, 2]})
    assert run_block(Pick, {"table": plain}, column="X", row="last")["value"] == 2.0
