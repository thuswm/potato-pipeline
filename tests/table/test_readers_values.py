from __future__ import annotations

import openpyxl
import pandas as pd
import pytest
from pydantic import ValidationError

from apb.blocks.table.common import TableError
from apb.blocks.table.readers import ReadCsv, ReadExcel
from apb.blocks.table.values import Constant, ConvertUnits, SetUnits
from apb.units import Quantity, UnitSystem, get_units, set_units
from tests.conftest import run_block


def make_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Allowables"
    ws.append(["Fastener allowables"])
    ws.append(["TYPE", "TENSION", "SHEAR"])
    ws.append(["HL10", 5000, 8000])
    ws.append(["HL12", 7000, 11000])
    wb.save(path)


def test_read_excel_header_row(tmp_path):
    make_workbook(tmp_path / "a.xlsx")
    t = run_block(ReadExcel, tmp=tmp_path, file="a.xlsx", sheet="Allowables", header_row=2)
    t = t["table"]
    assert list(t.columns) == ["TYPE", "TENSION", "SHEAR"]
    assert t["SHEAR"].tolist() == [8000, 11000]
    assert t.attrs["title"] == "Allowables"


def test_read_excel_range(tmp_path):
    make_workbook(tmp_path / "a.xlsx")
    t = run_block(ReadExcel, tmp=tmp_path, file="a.xlsx", range="A2:B4")["table"]
    assert t.to_dict("records") == [
        {"TYPE": "HL10", "TENSION": 5000},
        {"TYPE": "HL12", "TENSION": 7000},
    ]


def test_read_excel_missing_file(tmp_path):
    with pytest.raises(TableError, match="File not found"):
        run_block(ReadExcel, tmp=tmp_path, file="missing.xlsx")


def test_read_csv_european(tmp_path):
    (tmp_path / "a.csv").write_text("# comment\nEID;LOAD\n1;2,5\n2;3,75\n", encoding="utf-8")
    t = run_block(ReadCsv, tmp=tmp_path, file="a.csv", delimiter=";", decimal=",", skip_rows=1)[
        "table"
    ]
    assert t["LOAD"].tolist() == [2.5, 3.75]


def test_constant():
    assert run_block(Constant, value=410, unit="MPa")["value"] == Quantity(410.0, "MPa")
    assert run_block(Constant, value=1.5)["value"] == 1.5
    with pytest.raises(ValidationError, match="unknown unit"):
        Constant.Params.model_validate({"value": 1, "unit": "psi"})


def test_set_units_then_convert():
    df = pd.DataFrame({"EID": [1], "S": [400.0], "L": [2.0]})
    t = run_block(SetUnits, {"table": df}, unit_system="SI_mm", units={"S": "MPa", "L": "mm"})
    t = t["table"]
    assert get_units(t) == {"S": "MPa", "L": "mm"}
    si = run_block(ConvertUnits, {"value": t}, to="SI")["value"]
    assert si["S"].iloc[0] == pytest.approx(400e6)
    assert si["L"].iloc[0] == pytest.approx(0.002)
    assert si.attrs["unit_system"] == "SI"
    with pytest.raises(ValidationError):
        SetUnits.Params.model_validate({"unit_system": "SI", "units": {"S": "ksi"}})


def test_convert_quantity_and_plain_number():
    assert run_block(ConvertUnits, {"value": Quantity(1.0, "m")}, to="SI_mm")["value"] == Quantity(
        1000.0, "mm"
    )
    with pytest.raises(TableError, match="only tables and values with units"):
        run_block(ConvertUnits, {"value": 3.0}, to="SI")


def test_set_units_replace():
    df = pd.DataFrame({"A": [1.0], "B": [2.0]})
    set_units(df, UnitSystem.SI_MM, {"A": "mm"})
    t = run_block(SetUnits, {"table": df}, unit_system="SI_mm", units={"B": "N"}, replace=True)
    assert get_units(t["table"]) == {"B": "N"}
