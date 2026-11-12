"""Pipeline blocks reading MSC Nastran HDF5 files (spec 02 §3.3, §3.6)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from apb.blocks.nastran import h5reader
from apb.engine.block import Block, FilePath, RunContext
from apb.engine.types import Port, PortType
from apb.units import UnitSystem

CATEGORY = "Input/Nastran"

_UNIT_HELP = "Unit system of the Nastran model; empty = pipeline default"


class _H5Params(BaseModel):
    """Parameters shared by all Nastran HDF5 blocks."""

    model_config = ConfigDict(extra="forbid")

    file: FilePath = Field(description="Nastran .h5 result file")
    unit_system: UnitSystem | None = Field(default=None, description=_UNIT_HELP)


class _H5Block[P: _H5Params](Block[P]):
    """Common behaviour: the .h5 file is an input file for the cache."""

    category = CATEGORY

    def input_files(self, params: P, ctx: RunContext) -> list[Path]:
        """Return the .h5 file (a changed file invalidates the cache, NAS-10)."""
        return [ctx.resolve_path(params.file)]

    @staticmethod
    def _log_source(ctx: RunContext, path: Path, rows: int) -> None:
        """Log what was read, and warn about synthetic test files."""
        ctx.log(f"Read {rows} rows from {path.name}")
        version = h5reader.nastran_version(path)
        if version:
            ctx.log(f"Nastran version: {version}")
        if h5reader.is_synthetic(path):
            ctx.log("WARNING: synthetic test file, not real Nastran output")


class Hdf5ResultsParams(_H5Params):
    """Parameters of :class:`Hdf5Results`."""

    result: str = Field(description="Result dataset, e.g. ELEMENTAL/STRESS/QUAD4")
    subcases: list[int] = Field(default_factory=list, description="Subcases; empty = all")
    ids: str = Field(
        default="", description="GRID/element IDs, e.g. '1001-1200, 3005'; empty = all"
    )
    columns: list[str] = Field(default_factory=list, description="Value columns; empty = all")


class Hdf5Results(_H5Block[Hdf5ResultsParams]):
    """Read one result dataset (displacements, forces, stresses ...) into a table."""

    type_id = "nastran.hdf5_results"
    title = "Nastran HDF5 results"
    description = "Read a result dataset from an MSC Nastran .h5 file into a long-format table."
    outputs = (Port("table", PortType.TABLE, label="Table"),)
    Params = Hdf5ResultsParams

    def run(
        self, params: Hdf5ResultsParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Read the selected dataset."""
        path = ctx.resolve_path(params.file)
        table = h5reader.read_result(
            path,
            params.result,
            subcases=params.subcases,
            ids=params.ids,
            columns=params.columns or None,
            unit_system=ctx.unit_system(params.unit_system),
        )
        self._log_source(ctx, path, len(table))
        return {"table": table}


class Hdf5Eigenvalues(_H5Block[_H5Params]):
    """Read the eigenvalue summary of a normal modes analysis."""

    type_id = "nastran.hdf5_eigenvalues"
    title = "Nastran eigenvalues"
    description = "Mode number, eigenvalue, frequency [Hz], generalised mass and stiffness."
    outputs = (Port("table", PortType.TABLE, label="Table"),)
    Params = _H5Params

    def run(self, params: _H5Params, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Read the eigenvalue table."""
        path = ctx.resolve_path(params.file)
        table = h5reader.read_eigenvalues(path, ctx.unit_system(params.unit_system))
        self._log_source(ctx, path, len(table))
        return {"table": table}


class Hdf5Rbe2LoadsParams(_H5Params):
    """Parameters of :class:`Hdf5Rbe2Loads`."""

    source: Literal["auto", "mpc_force", "gpforce"] = Field(
        default="auto", description="MPC forces or grid point forces; auto = what is present"
    )
    side: Literal["independent", "dependent"] = Field(
        default="dependent", description="Report forces at the independent or dependent grids"
    )
    eids: str = Field(default="", description="RBE2 element IDs; empty = all")
    subcases: list[int] = Field(default_factory=list, description="Subcases; empty = all")


class Hdf5Rbe2Loads(_H5Block[Hdf5Rbe2LoadsParams]):
    """Read the forces carried by RBE2 elements (e.g. fasteners modelled with RBE2)."""

    type_id = "nastran.hdf5_rbe2_loads"
    title = "Nastran RBE2 loads"
    description = (
        "Forces per RBE2 element from MPC forces or grid point forces, in the "
        "output coordinate system of the grids."
    )
    outputs = (Port("table", PortType.TABLE, label="Table"),)
    Params = Hdf5Rbe2LoadsParams

    def run(
        self, params: Hdf5Rbe2LoadsParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Read RBE2 loads."""
        path = ctx.resolve_path(params.file)
        table = h5reader.read_rbe2_loads(
            path,
            source=params.source,
            side=params.side,
            eids=params.eids,
            subcases=params.subcases,
            unit_system=ctx.unit_system(params.unit_system),
        )
        ctx.log(f"Source: {table.attrs['result']}")
        self._log_source(ctx, path, len(table))
        return {"table": table}


class Hdf5Masses(_H5Block[_H5Params]):
    """Read CONM2 point masses from the model part of the file."""

    type_id = "nastran.hdf5_masses"
    title = "Nastran point masses"
    description = "CONM2 elements: grid, mass, offsets and inertia."
    outputs = (Port("table", PortType.TABLE, label="Table"),)
    Params = _H5Params

    def run(self, params: _H5Params, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Read CONM2 data."""
        path = ctx.resolve_path(params.file)
        table = h5reader.read_masses(path, ctx.unit_system(params.unit_system))
        self._log_source(ctx, path, len(table))
        return {"table": table}


BLOCKS: tuple[type[Block[Any]], ...] = (Hdf5Results, Hdf5Eigenvalues, Hdf5Rbe2Loads, Hdf5Masses)
