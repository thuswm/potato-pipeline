"""Blocks reading tables from an MSC Nastran ``.f06`` (spec 02 §3.11).

The file comes from the *f06* output of an analysis block or a typed path. The parsing is in
:mod:`apb.blocks.nastran.f06tables`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from apb.blocks.nastran.f06 import read_text
from apb.blocks.nastran.f06tables import read_grid_weight, read_mass_fractions
from apb.blocks.nastran.mapping import RESULT_MAPPINGS
from apb.engine.block import Block, FilePath, RunContext, file_types
from apb.engine.types import Port, PortType
from apb.units import UnitSystem, set_units, unit_label

CATEGORY = "Input/Nastran"


class F06Params(BaseModel):
    """Parameters shared by the ``.f06`` table blocks."""

    model_config = ConfigDict(extra="forbid")

    file: FilePath = Field(
        default="",
        description="Nastran .f06 file; empty if the file comes from a connected analysis block",
        json_schema_extra=file_types(".f06"),
    )
    unit_system: UnitSystem | None = Field(
        default=None, description="Unit system of the Nastran model; empty = pipeline default"
    )


class _F06Block[P: F06Params](Block[P]):
    """Common behaviour: the file from the connector or the parameter, units, logging."""

    category = CATEGORY
    inputs = (Port("file", PortType.TEXT, label="F06 file", optional=True),)
    outputs = (Port("table", PortType.TABLE, label="Table"),)

    #: Result whose units and column kinds the table shares (``RESULT_MAPPINGS`` key).
    same_as: ClassVar[str] = ""

    def input_files(self, params: P, ctx: RunContext) -> list[Path]:
        """Return the typed ``.f06`` (a connected one is covered by the engine, spec 09 §3.7)."""
        return [ctx.resolve_path(params.file)] if params.file else []

    def _read(self, params: P, inputs: dict[str, Any], ctx: RunContext) -> tuple[Path, str]:
        """Return the path and the text of the ``.f06``."""
        name = inputs.get("file") or params.file
        if not name:
            raise ValueError("No .f06 file: type the path of the file or connect an analysis block")
        path = ctx.resolve_path(str(name))
        if not path.is_file():
            raise FileNotFoundError(f".f06 file not found: {path}")
        return path, read_text(path)

    def _finish(self, table: pd.DataFrame, params: P, path: Path, ctx: RunContext) -> pd.DataFrame:
        """Attach units (as the HDF5 result of the same data) and the source file."""
        system = ctx.unit_system(params.unit_system)
        kinds = RESULT_MAPPINGS[self.same_as].kinds
        units = {col: unit_label(kind, system) for col, kind in kinds.items() if col in table}
        set_units(table, system, units)
        table.attrs["source_file"] = str(path)
        return table


class F06MassFractionParams(F06Params):
    """Parameters of :class:`F06MassFraction`."""

    sums: bool = Field(
        title="Running sums",
        default=False,
        description="Also give the running sums T1_SUM ... R3_SUM",
    )


class F06MassFraction(_F06Block[F06MassFractionParams]):
    """Modal effective mass fractions from the ``.f06`` (translation and rotation in one)."""

    type_id = "nastran.f06_mass_fraction"
    title = "Nastran f06: modal effective mass fraction"
    description = (
        "Reads the MODAL EFFECTIVE MASS FRACTION tables (translational and rotational) of an "
        ".f06 into one table: MODE, FREQUENCY, T1_FRAC ... R3_FRAC. Needs MEFFMASS in the "
        "case control; no .h5 needed."
    )
    Params = F06MassFractionParams
    same_as: ClassVar[str] = "MODAL/MODAL_MASS_FRACTION"

    def run(
        self, params: F06MassFractionParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Read the two tables and combine them."""
        path, text = self._read(params, inputs, ctx)
        table = read_mass_fractions(text, sums=params.sums)
        ctx.log(f"Read {len(table)} modes from {path.name}")
        return {"table": self._finish(table, params, path, ctx)}


class F06GridWeight(_F06Block[F06Params]):
    """Mass and centre of gravity from the grid point weight generator output."""

    type_id = "nastran.f06_grid_weight"
    title = "Nastran f06: grid point weight (mass, C.G.)"
    description = (
        "Reads mass and centre of gravity per direction from the GRID POINT WEIGHT "
        "GENERATOR output of an .f06: DIRECTION, MASS, X_CG, Y_CG, Z_CG. Needs PARAM,GRDPNT."
    )
    Params = F06Params
    same_as: ClassVar[str] = "NODAL/GRID_WEIGHT"

    def run(self, params: F06Params, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Read the first grid point weight generator output."""
        path, text = self._read(params, inputs, ctx)
        table, count = read_grid_weight(text)
        reference = table.attrs.get("reference_point")
        ctx.log(f"Read mass and C.G. from {path.name} (reference point {reference})")
        if count > 1:
            ctx.log(f"The file has {count} grid point weight outputs; the first is used")
        return {"table": self._finish(table, params, path, ctx)}


BLOCKS: tuple[type[Block[Any]], ...] = (F06MassFraction, F06GridWeight)
