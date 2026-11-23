"""Write CSV block (spec 03 §3.12, round 6)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from apb.blocks.table.csvwriter import CsvWriteError, WriteCsvParams, write_csv
from apb.engine import Executor, PipelineModel, default_registry
from apb.units import UnitSystem, set_units
from tests.conftest import PLATE_H5


def table() -> pd.DataFrame:
    df = pd.DataFrame({"EID": [1, 2], "S": [1.5, 2.25], "T": [-3.0, 40.0]})
    set_units(df, UnitSystem.SI_MM, {"S": "MPa", "T": "°C"})
    return df


def lines(path: Path, encoding: str = "utf-8-sig") -> list[str]:
    return path.read_text(encoding=encoding).splitlines()


def test_units_in_the_header_by_default(tmp_path: Path):
    out = tmp_path / "a" / "t.csv"
    write_csv(out, table())
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")  # Excel recognises UTF-8
    assert out.read_bytes().count(b"\r\n") == 3
    assert lines(out) == ["EID,S [MPa],T [°C]", "1,1.5,-3.0", "2,2.25,40.0"]


def test_european_excel_settings_and_unit_row(tmp_path: Path):
    out = tmp_path / "t.csv"
    write_csv(out, table(), separator=";", decimal=",", units="row")
    assert lines(out) == ["EID;S;T", ";MPa;°C", "1;1,5;-3,0", "2;2,25;40,0"]
    write_csv(out, table(), separator="\t", units="none", encoding="cp1252")
    assert lines(out, "cp1252")[0] == "EID\tS\tT"


def test_two_level_headers_are_joined(tmp_path: Path):
    df = pd.DataFrame([[1, 2.0, 3.0]])
    df.columns = pd.MultiIndex.from_tuples([("EID", ""), ("TENSION", "LC1"), ("TENSION", "LC2")])
    out = tmp_path / "t.csv"
    write_csv(out, df)
    assert lines(out)[0] == "EID,TENSION_LC1,TENSION_LC2"


def test_same_separator_and_decimal_is_refused(tmp_path: Path):
    with pytest.raises(CsvWriteError, match="both ','"):
        write_csv(tmp_path / "t.csv", table(), separator=",", decimal=",")
    with pytest.raises(ValueError, match="must differ"):
        WriteCsvParams.model_validate({"file": "t.csv", "decimal": ","})


def test_block_in_pipeline(tmp_path: Path):
    p = PipelineModel()
    p.add_block("nastran.hdf5_eigenvalues", block_id="e", file=str(PLATE_H5))
    p.add_block("table.write_csv", block_id="c", file="out/eig.csv", separator=";", decimal=",")
    p.connect("e.table", "c.table")
    result = Executor(default_registry()).run(p, tmp_path)
    assert result.ok, result.results["c"].error
    out = tmp_path / "out" / "eig.csv"
    assert result.output("c", "file") == str(out)
    assert lines(out)[0].startswith("MODE;")
    q = PipelineModel()
    q.add_block("nastran.hdf5_eigenvalues", block_id="e", file=str(PLATE_H5))
    q.add_block("table.write_csv", block_id="c", file="out.xlsx")
    q.connect("e.table", "c.table")
    bad = Executor(default_registry()).run(q, tmp_path)
    assert "must end with .csv" in (bad.results["c"].error or "")
