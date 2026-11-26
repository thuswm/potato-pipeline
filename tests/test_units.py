from __future__ import annotations

import pandas as pd
import pytest

from apb.units import (
    Quantity,
    QuantityKind,
    UnitError,
    UnitSystem,
    common_unit_system,
    conversion_factor,
    convert_table,
    get_units,
    header_with_unit,
    mass_in_kg,
    set_units,
    unit_label,
)


def test_labels():
    assert unit_label(QuantityKind.STRESS, UnitSystem.SI_MM) == "MPa"
    assert unit_label(QuantityKind.STRESS, UnitSystem.SI) == "Pa"
    assert unit_label(QuantityKind.MASS, UnitSystem.SI_MM) == "t"


@pytest.mark.parametrize(
    ("unit", "target", "factor", "label"),
    [
        ("Pa", UnitSystem.SI_MM, 1e-6, "MPa"),
        ("MPa", UnitSystem.SI, 1e6, "Pa"),
        ("m", UnitSystem.SI_MM, 1e3, "mm"),
        ("N·mm", UnitSystem.SI, 1e-3, "N·m"),
        ("kg", UnitSystem.SI_MM, 1e-3, "t"),
        ("N", UnitSystem.SI_MM, 1.0, "N"),
        ("MPa", UnitSystem.SI_MM, 1.0, "MPa"),
    ],
)
def test_conversion_factor(unit, target, factor, label):
    f, new = conversion_factor(unit, target)
    assert f == pytest.approx(factor)
    assert new == label


def test_unknown_unit():
    with pytest.raises(UnitError, match="Unknown unit"):
        conversion_factor("psi", UnitSystem.SI)


def test_quantity_format_and_convert():
    q = Quantity(410.0, "MPa")
    assert f"{q:.1f}" == "410.0 MPa"
    assert q.to(UnitSystem.SI) == Quantity(410e6, "Pa")
    assert f"{Quantity(0.25):.2f}" == "0.25"


def test_convert_table_roundtrip():
    df = pd.DataFrame({"EID": [1, 2], "DX": [0.001, 0.002], "S": [1e6, 2e6]})
    set_units(df, UnitSystem.SI, {"DX": "m", "S": "Pa"})
    mm = convert_table(df, UnitSystem.SI_MM)
    assert mm["DX"].tolist() == pytest.approx([1.0, 2.0])
    assert mm["S"].tolist() == pytest.approx([1.0, 2.0])
    assert get_units(mm) == {"DX": "mm", "S": "MPa"}
    assert mm.attrs["unit_system"] == "SI_mm"
    assert df["DX"].tolist() == [0.001, 0.002]  # input unchanged
    back = convert_table(mm, UnitSystem.SI)
    assert back["S"].tolist() == pytest.approx([1e6, 2e6])


def test_mixed_unit_systems_refused():
    a = pd.DataFrame({"x": [1]})
    b = pd.DataFrame({"x": [1]})
    set_units(a, UnitSystem.SI, {})
    set_units(b, UnitSystem.SI_MM, {})
    with pytest.raises(UnitError, match="Convert units"):
        common_unit_system([a, b])
    assert common_unit_system([a, pd.DataFrame()]) is UnitSystem.SI


def test_header_with_unit():
    assert header_with_unit("S", {"S": "MPa"}) == "S [MPa]"
    assert header_with_unit("EID", {}) == "EID"


def test_mass_in_kg_for_output_only():
    df = pd.DataFrame({"DIRECTION": [1, 2], "MASS": [0.0125, 0.0125], "X_CG": [10.0, 11.0]})
    set_units(df, UnitSystem.SI_MM, {"MASS": "t", "X_CG": "mm"})
    df.attrs["title"] = "GRID_WEIGHT"
    out = mass_in_kg(df)
    assert out["MASS"].tolist() == [12.5, 12.5]
    assert get_units(out) == {"MASS": "kg", "X_CG": "mm"}
    assert out.attrs["title"] == "GRID_WEIGHT"
    assert get_units(df)["MASS"] == "t" and df["MASS"].iloc[0] == 0.0125  # input unchanged
    plain = pd.DataFrame({"S": [1.0]})
    assert mass_in_kg(plain) is plain


def test_mass_in_kg_two_level_and_inertia():
    cols = pd.MultiIndex.from_tuples([("I", "MAX"), ("I", "MAX_CASE"), ("EID", "")])
    df = pd.DataFrame([[2.0, "LC1", 7]], columns=cols)
    set_units(df, UnitSystem.SI_MM, {"I": "t·mm²"})
    out = mass_in_kg(df)
    assert out[("I", "MAX")].iloc[0] == 2000.0
    assert out[("I", "MAX_CASE")].iloc[0] == "LC1"
    assert get_units(out)["I"] == "kg·mm²"
