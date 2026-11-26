"""Write a table to a CSV file (spec 03 §3.12, round 6).

CSV holds one table per file, so the block takes one table; use one block per file. The
separator and the decimal sign can be chosen: Excel with Swedish (and most European)
regional settings expects ``;`` and ``,``.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from apb.engine.block import Block, OutputPath, RunContext, file_types
from apb.engine.types import Port, PortType
from apb.fileutil import OutputFileError, atomic_output
from apb.units import get_units, header_with_unit, mass_in_kg

#: Line ending (RFC 4180, the same on every platform).
_EOL = "\r\n"

#: Separators offered (the tab is written as ``\t``).
Separator = Literal[",", ";", "\t"]


class CsvWriteError(ValueError):
    """The CSV file cannot be written; the message is for the user."""


def flat_headers(table: pd.DataFrame) -> list[str]:
    """Return one header per column; two-level headers are joined (``TENSION_LC1``)."""
    if isinstance(table.columns, pd.MultiIndex):
        return ["_".join(str(p) for p in col if str(p)) for col in table.columns]
    return [str(c) for c in table.columns]


def write_csv(
    path: Path,
    table: pd.DataFrame,
    *,
    separator: str = ",",
    decimal: str = ".",
    units: Literal["header", "row", "none"] = "header",
    encoding: str = "utf-8-sig",
    kg: bool = True,
) -> None:
    """Write one table to a CSV file (atomically; parent folders are created).

    Args:
        path: Target file.
        table: The table.
        separator: Column separator.
        decimal: Decimal sign of the numbers.
        units: ``header``: ``SX [MPa]``; ``row``: a second header row with the units;
            ``none``: plain column names.
        encoding: Text encoding; ``utf-8-sig`` (with a byte order mark) makes Excel read
            units such as ``°C`` and ``µm`` correctly.
        kg: Write mass columns in kg instead of tonne (:func:`apb.units.mass_in_kg`).

    Raises:
        CsvWriteError: If separator and decimal sign are the same, or the file cannot be
            written (e.g. open in Excel).
    """
    if separator == decimal:
        raise CsvWriteError(f"Separator and decimal sign are both {separator!r}")
    if kg:
        table = mass_in_kg(table)
    column_units = get_units(table) if units != "none" else {}
    names = flat_headers(table)
    header_rows = [
        [header_with_unit(n, column_units) for n in names] if units == "header" else names
    ]
    if units == "row":
        header_rows.append([column_units.get(n, "") for n in names])
    data = table.copy()
    data.columns = pd.Index(names)
    try:
        with atomic_output(path) as tmp, tmp.open("w", encoding=encoding, newline="") as fh:
            csv.writer(fh, delimiter=separator, lineterminator=_EOL).writerows(header_rows)
            data.to_csv(
                fh, sep=separator, decimal=decimal, index=False, header=False, lineterminator=_EOL
            )
    except OutputFileError as exc:
        raise CsvWriteError(str(exc)) from exc


class WriteCsvParams(BaseModel):
    """Parameters of :class:`WriteCsv`."""

    model_config = ConfigDict(extra="forbid")

    file: OutputPath = Field(
        description="CSV file to write, relative to the output folder",
        json_schema_extra=file_types(".csv", ".txt"),
    )
    separator: Separator = Field(
        title="Column separator",
        default=",",
        description="Column separator: , or ; (Excel with European settings) or a tab",
        json_schema_extra={"apb_labels": {",": ", (comma)", ";": "; (semicolon)", "\t": "tab"}},
    )
    decimal: Literal[".", ","] = Field(
        title="Decimal sign", default=".", description="Decimal sign of the numbers"
    )
    units: Literal["header", "row", "none"] = Field(
        default="header",
        description="header: SX [MPa]; row: a second row with the units; none: no units",
    )
    mass_in_kg: bool = Field(
        title="Mass in kg",
        default=True,
        description="Write mass in kg (x 1000) instead of t (tonne, the mm unit system)",
    )
    encoding: Literal["utf-8-sig", "utf-8", "cp1252"] = Field(
        default="utf-8-sig",
        description="utf-8-sig: UTF-8 that Excel recognises; cp1252: old Windows programs",
    )

    @model_validator(mode="after")
    def _distinct(self) -> WriteCsvParams:
        if self.separator == self.decimal:
            raise ValueError("the column separator and the decimal sign must differ")
        return self


class WriteCsv(Block[WriteCsvParams]):
    """Write the connected table to a CSV file."""

    type_id = "table.write_csv"
    category = "Output"
    title = "Write CSV"
    description = (
        "Write a table to a .csv file (one table per file). Separator and decimal sign can be "
        "chosen, e.g. ; and , for Excel with European settings."
    )
    inputs = (Port("table", PortType.TABLE, label="Table"),)
    outputs = (Port("file", PortType.TEXT, label="File"),)
    Params = WriteCsvParams
    advanced_params = ("units", "mass_in_kg", "encoding")
    always_run = True

    @staticmethod
    def _target(params: WriteCsvParams, ctx: RunContext) -> Path:
        path = Path(params.file)
        return path if path.is_absolute() else ctx.output_dir / path

    def output_files(
        self, params: WriteCsvParams, outputs: Mapping[str, Any], ctx: RunContext
    ) -> list[Path]:
        """The CSV file (the block counts as done only while it exists)."""
        return [self._target(params, ctx)]

    def run(
        self, params: WriteCsvParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Write the file and return its path."""
        if Path(params.file).suffix.lower() not in {".csv", ".txt"}:
            raise CsvWriteError(f"CSV file must end with .csv or .txt, got {params.file!r}")
        target = self._target(params, ctx)
        table: pd.DataFrame = inputs["table"]
        write_csv(
            target,
            table,
            separator=params.separator,
            decimal=params.decimal,
            units=params.units,
            encoding=params.encoding,
            kg=params.mass_in_kg,
        )
        ctx.log(f"Wrote {target} ({len(table)} rows)")
        return {"file": str(target)}


BLOCKS: tuple[type[Block[Any]], ...] = (WriteCsv,)
