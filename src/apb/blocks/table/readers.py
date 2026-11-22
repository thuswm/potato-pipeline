"""Input blocks for tables from Excel and CSV files (spec 03)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import openpyxl
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from apb.blocks.table.common import TableError
from apb.engine.block import Block, FilePath, RunContext
from apb.engine.types import Port, PortType

CATEGORY = "Input/Files"


class _FileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: FilePath = Field(description="File to read, relative to the pipeline")


class _FileBlock[P: _FileParams](Block[P]):
    """Common behaviour: the file is an input file for the cache."""

    category = CATEGORY
    outputs = (Port("table", PortType.TABLE, label="Table"),)

    def input_files(self, params: P, ctx: RunContext) -> list[Path]:
        """Return the file read by the block."""
        return [ctx.resolve_path(params.file)]


def sheet_list(path: Path) -> list[str] | None:
    """Return the sheet names of a workbook, or ``None`` if it cannot be read.

    The workbook is opened read-only and closed again (for the GUI's sheet drop-down).
    """
    if not path.is_file():
        return None
    try:
        wb = openpyxl.load_workbook(path, read_only=True)
    except Exception:  # not a workbook, locked, damaged: the GUI keeps the text field
        return None
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def read_excel_range(path: Path, sheet: str, cell_range: str) -> pd.DataFrame:
    """Read a cell range (first row = headers) from a workbook.

    Args:
        path: ``.xlsx`` file.
        sheet: Sheet name; empty = first sheet.
        cell_range: Range such as ``A1:F200``.

    Returns:
        Table with the range's first row as column names.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        rows = [[cell.value for cell in row] for row in ws[cell_range]]
    finally:
        wb.close()
    if not rows:
        raise TableError(f"Range {cell_range} is empty")
    header = [str(h) if h is not None else f"COLUMN_{i + 1}" for i, h in enumerate(rows[0])]
    return pd.DataFrame(rows[1:], columns=header)


class ReadExcelParams(_FileParams):
    """Parameters of :class:`ReadExcel`."""

    sheet: str = Field(
        default="",
        description="Sheet name; empty = first sheet",
        json_schema_extra={"apb_empty": "(first sheet)"},
    )
    header_row: int = Field(default=1, ge=1, description="Row with the column names (1 = first)")
    range: str = Field(
        title="Cell range",
        default="",
        description="Optional cell range, e.g. A1:F200 (overrides header_row; first row = names)",
    )


class ReadExcel(_FileBlock[ReadExcelParams]):
    """Read a table from an Excel workbook."""

    type_id = "table.read_excel"
    title = "Read Excel"
    description = "Read a sheet (or a cell range) of an .xlsx workbook into a table."
    Params = ReadExcelParams
    advanced_params = ("header_row", "range")

    def run(
        self, params: ReadExcelParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Read the workbook."""
        path = ctx.resolve_path(params.file)
        if not path.is_file():
            raise TableError(f"File not found: {path}")
        if params.range:
            df = read_excel_range(path, params.sheet, params.range)
        else:
            sheet: str | int = params.sheet or 0
            df = pd.read_excel(path, sheet_name=sheet, header=params.header_row - 1)
        df.attrs.update({"source_file": str(path), "title": params.sheet or path.stem})
        ctx.log(f"Read {len(df)} rows x {len(df.columns)} columns from {path.name}")
        return {"table": df}


class ReadCsvParams(_FileParams):
    """Parameters of :class:`ReadCsv`."""

    delimiter: str = Field(
        title="Column separator", default=",", description="Column separator, e.g. , ; or \\t"
    )
    decimal: Literal[".", ","] = Field(
        title="Decimal separator", default=".", description="Decimal separator"
    )
    skip_rows: int = Field(
        title="Lines to skip", default=0, ge=0, description="Lines to skip before the header"
    )
    encoding: str = Field(default="utf-8-sig", description="Text encoding, e.g. cp1252")


class ReadCsv(_FileBlock[ReadCsvParams]):
    """Read a table from a CSV / text file."""

    type_id = "table.read_csv"
    title = "Read CSV"
    description = "Read a delimited text file (first line after skipped lines = names)."
    Params = ReadCsvParams
    advanced_params = ("delimiter", "decimal", "skip_rows", "encoding")

    def run(self, params: ReadCsvParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Read the file."""
        path = ctx.resolve_path(params.file)
        if not path.is_file():
            raise TableError(f"File not found: {path}")
        sep = "\t" if params.delimiter in ("\\t", "tab") else params.delimiter
        df = pd.read_csv(
            path,
            sep=sep,
            decimal=params.decimal,
            skiprows=params.skip_rows,
            encoding=params.encoding,
        )
        df.attrs.update({"source_file": str(path), "title": path.stem})
        ctx.log(f"Read {len(df)} rows x {len(df.columns)} columns from {path.name}")
        return {"table": df}


BLOCKS: tuple[type[Block[Any]], ...] = (ReadExcel, ReadCsv)
