from __future__ import annotations

from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from apb.blocks.table.excel import ExcelWriteError, sheet_names, write_workbook
from apb.engine import BlockState, Executor, PipelineModel, default_registry
from apb.units import UnitSystem, set_units
from tests.conftest import PLATE_H5


def table(title: str = "") -> pd.DataFrame:
    df = pd.DataFrame({"EID": [1, 2], "S": [1.5, 2.25]})
    set_units(df, UnitSystem.SI_MM, {"S": "MPa"})
    if title:
        df.attrs["title"] = title
    return df


def test_sheet_names():
    tables = [table("CQUAD4 stresses"), table("CQUAD4 stresses"), table(), table("a/b:c?")]
    assert sheet_names(tables, ["", "", "", ""]) == [
        "CQUAD4 stresses",
        "CQUAD4 stresses (2)",
        "Table3",
        "a_b_c_",
    ]
    assert sheet_names([table()], ["My sheet"]) == ["My sheet"]
    long = sheet_names([table(), table()], ["x" * 40, "x" * 40])
    assert [len(n) for n in long] == [31, 31] and long[0] != long[1]


def test_write_workbook(tmp_path: Path):
    path = tmp_path / "out" / "res.xlsx"
    names = write_workbook(path, [table("Stress"), table()], ["", "Second"])
    assert names == ["Stress", "Second"]
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Stress", "Second"]
    ws = wb["Stress"]
    assert [c.value for c in ws[1]] == ["EID", "S [MPa]"]
    assert [c.value for c in ws[3]] == [2, 2.25]
    assert ws.freeze_panes == "A2"
    assert not list(tmp_path.joinpath("out").glob(".apb-*"))  # temp file removed


def test_units_in_header_off(tmp_path: Path):
    path = tmp_path / "res.xlsx"
    write_workbook(path, [table()], units_in_header=False)
    assert [c.value for c in openpyxl.load_workbook(path).active[1]] == ["EID", "S"]


def test_locked_file_gives_clear_error(tmp_path: Path, monkeypatch):
    def locked(self, target):
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "replace", locked)
    with pytest.raises(ExcelWriteError, match="Close it in Excel"):
        write_workbook(tmp_path / "res.xlsx", [table()])
    assert list(tmp_path.iterdir()) == []


def test_multiindex_columns_refused(tmp_path: Path):
    df = pd.DataFrame([[1, 2]], columns=pd.MultiIndex.from_tuples([("T", "LC1"), ("T", "LC2")]))
    with pytest.raises(ExcelWriteError, match="multi-level"):
        write_workbook(tmp_path / "x.xlsx", [df])


def test_block_in_pipeline_always_rewrites(tmp_path: Path):
    p = PipelineModel()
    p.add_block(
        "nastran.hdf5_results",
        block_id="s",
        file=str(PLATE_H5),
        result="STRESS/QUAD4",
        subcases=[1],
    )
    p.add_block("table.write_excel", block_id="x", file="results/stress.xlsx")
    p.connect("s.table", "x.tables")
    executor = Executor(default_registry())
    first = executor.run(p, tmp_path)
    assert first.ok
    out = tmp_path / "results" / "stress.xlsx"
    assert first.output("x", "file") == str(out)
    assert openpyxl.load_workbook(out).sheetnames == ["CQUAD4 stresses"]

    out.unlink()
    second = executor.run(p, tmp_path)
    assert second.results["s"].cached
    assert not second.results["x"].cached
    assert out.exists()


def test_block_rejects_wrong_extension(tmp_path: Path):
    p = PipelineModel()
    p.add_block("nastran.hdf5_eigenvalues", block_id="e", file=str(PLATE_H5))
    p.add_block("table.write_excel", block_id="x", file="out.csv")
    p.connect("e.table", "x.tables")
    result = Executor(default_registry()).run(p, tmp_path)
    assert result.results["x"].state is BlockState.ERROR
    assert ".xlsx" in result.results["x"].error
