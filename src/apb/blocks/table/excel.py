"""Excel output: the ``table.write_excel`` block (spec 03 §3.3).

The library function :func:`write_workbook` does the work and can be used without the
pipeline engine.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from pydantic import BaseModel, ConfigDict, Field

from apb.engine.block import Block, FilePath, RunContext
from apb.engine.types import Port, PortType
from apb.units import get_units, header_with_unit

#: Excel's limit on sheet name length.
MAX_SHEET_NAME = 31
_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
#: Rows inspected when estimating column widths.
_WIDTH_SAMPLE_ROWS = 500
_MAX_COLUMN_WIDTH = 50


class ExcelWriteError(Exception):
    """Raised when the workbook cannot be written (e.g. the file is open in Excel)."""


def sheet_names(tables: Sequence[pd.DataFrame], requested: Sequence[str]) -> list[str]:
    """Return valid, unique sheet names for the tables.

    The name of table *i* is ``requested[i]`` if given and not empty, else the table's
    ``attrs["title"]``, else ``Table<i+1>``. Characters Excel forbids are replaced by ``_``,
    names are cut to 31 characters and duplicates get a ``(2)``, ``(3)`` ... suffix.

    Args:
        tables: Tables to write.
        requested: Sheet names chosen by the user (may be shorter than ``tables``).

    Returns:
        One sheet name per table.
    """
    names: list[str] = []
    used: set[str] = set()
    for i, table in enumerate(tables):
        raw = requested[i] if i < len(requested) and requested[i].strip() else ""
        raw = raw or str(table.attrs.get("title", "")) or f"Table{i + 1}"
        base = _INVALID_SHEET_CHARS.sub("_", raw).strip().strip("'")[:MAX_SHEET_NAME] or "Sheet"
        name, n = base, 1
        while name.lower() in used:
            n += 1
            suffix = f" ({n})"
            name = base[: MAX_SHEET_NAME - len(suffix)] + suffix
        used.add(name.lower())
        names.append(name)
    return names


def _format_sheet(ws: Worksheet, table: pd.DataFrame, headers: list[str]) -> None:
    """Freeze the header row and size the columns to their content."""
    ws.freeze_panes = "A2"
    sample = table.head(_WIDTH_SAMPLE_ROWS)
    for idx, (col, header) in enumerate(zip(table.columns, headers, strict=True), start=1):
        values = sample[col].map(lambda v: f"{v:.6g}" if isinstance(v, float) else str(v))
        longest = max([len(header), *values.str.len().tolist()])
        ws.column_dimensions[get_column_letter(idx)].width = min(longest + 2, _MAX_COLUMN_WIDTH)


def write_workbook(
    path: Path,
    tables: Sequence[pd.DataFrame],
    names: Sequence[str] = (),
    *,
    units_in_header: bool = True,
) -> list[str]:
    """Write tables to a new ``.xlsx`` workbook, one sheet per table.

    The workbook is first written to a temporary file next to the target and then moved
    into place, so an existing workbook is never left half-written.

    Args:
        path: Target ``.xlsx`` file; parent folders are created.
        tables: Tables to write (in sheet order).
        names: Sheet names (see :func:`sheet_names`).
        units_in_header: Write column headers with units, e.g. ``SX_Z1 [MPa]``.

    Returns:
        The sheet names used.

    Raises:
        ExcelWriteError: If there are no tables, a table has multi-level columns, or the
            file cannot be replaced (typically because it is open in Excel).
    """
    if not tables:
        raise ExcelWriteError("No tables connected")
    for table in tables:
        if isinstance(table.columns, pd.MultiIndex):
            raise ExcelWriteError("Tables with multi-level column headers are not supported yet")
    path.parent.mkdir(parents=True, exist_ok=True)
    used_names = sheet_names(tables, names)

    fd, tmp_name = tempfile.mkstemp(suffix=".xlsx", prefix=".apb-", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
            for table, name in zip(tables, used_names, strict=True):
                units = get_units(table) if units_in_header else {}
                headers = [header_with_unit(str(c), units) for c in table.columns]
                table.to_excel(writer, sheet_name=name, index=False, header=headers)
                _format_sheet(writer.sheets[name], table, headers)
        try:
            tmp.replace(path)
        except PermissionError as exc:
            raise ExcelWriteError(
                f"Cannot write {path.name}: the file is in use. Close it in Excel and run again."
            ) from exc
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
    return used_names


class WriteExcelParams(BaseModel):
    """Parameters of :class:`WriteExcel`."""

    model_config = ConfigDict(extra="forbid")

    file: FilePath = Field(description="Workbook to write (.xlsx), relative to the output folder")
    sheet_names: list[str] = Field(
        default_factory=list,
        description="Sheet name per connected table, in connection order; empty = table title",
    )
    units_in_header: bool = Field(default=True, description="Show units in the column headers")


class WriteExcel(Block[WriteExcelParams]):
    """Write the connected tables to an Excel workbook, one sheet per table."""

    type_id = "table.write_excel"
    category = "Output"
    title = "Write Excel"
    description = "Write tables to a new .xlsx workbook (one sheet per connected table)."
    inputs = (Port("tables", PortType.TABLE, label="Tables", multiple=True),)
    outputs = (Port("file", PortType.TEXT, label="File"),)
    Params = WriteExcelParams
    always_run = True

    def run(
        self, params: WriteExcelParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Write the workbook and return its path."""
        path = Path(params.file)
        if path.suffix.lower() != ".xlsx":
            raise ExcelWriteError(f"Excel file must end with .xlsx, got {params.file!r}")
        target = path if path.is_absolute() else ctx.output_dir / path
        names = write_workbook(
            target, inputs["tables"], params.sheet_names, units_in_header=params.units_in_header
        )
        ctx.log(f"Wrote {target} (sheets: {', '.join(names)})")
        return {"file": str(target)}


BLOCKS: tuple[type[Block[Any]], ...] = (WriteExcel,)
