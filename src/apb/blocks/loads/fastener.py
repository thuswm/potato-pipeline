"""Fastener loads from CBUSH forces or RBE2 loads (spec 03 §3.2).

This block is the seed of the fastener library; detailed fastener strength calculations
will live in a separate block package.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from apb.blocks.table.common import carry_units, require_columns, unit_of
from apb.engine.block import Block, RunContext
from apb.engine.types import Port, PortType

Axis = Literal["X", "Y", "Z"]
_KEYS = ("EID", "GRID", "SUBCASE", "MODE", "SOURCE")


def fastener_loads(
    forces: pd.DataFrame,
    *,
    axial_axis: Axis = "X",
    tension_mode: Literal["signed", "tension_only"] = "signed",
    include_moments: bool = False,
    keep_forces: bool = False,
) -> pd.DataFrame:
    """Compute tension and shear per fastener and load case.

    Args:
        forces: Table with ``FX, FY, FZ`` (and ``MX, MY, MZ`` for moments), e.g. CBUSH
            element forces or RBE2 loads.
        axial_axis: Component along the fastener axis.
        tension_mode: ``signed`` keeps compression negative; ``tension_only`` sets it to 0.
        include_moments: Add ``BENDING`` (resultant of the two other moments) and
            ``TORSION`` (moment about the axis).
        keep_forces: Keep the original force/moment columns.

    Returns:
        Key columns (``EID``, ``GRID``, ``SUBCASE`` ... as present) followed by ``TENSION``,
        ``SHEAR`` (and ``BENDING``, ``TORSION``).
    """
    others = [a for a in "XYZ" if a != axial_axis]
    force_cols = [f"F{a}" for a in "XYZ"]
    moment_cols = [f"M{a}" for a in "XYZ"]
    require_columns(forces, force_cols + (moment_cols if include_moments else []), "forces table")

    keys = [c for c in _KEYS if c in forces.columns]
    out = forces[keys].copy()
    tension = forces[f"F{axial_axis}"]
    out["TENSION"] = tension.clip(lower=0.0) if tension_mode == "tension_only" else tension
    out["SHEAR"] = np.hypot(forces[f"F{others[0]}"], forces[f"F{others[1]}"])
    extra = {c: unit_of(forces, f"F{axial_axis}") for c in ("TENSION", "SHEAR")}
    if include_moments:
        out["BENDING"] = np.hypot(forces[f"M{others[0]}"], forces[f"M{others[1]}"])
        out["TORSION"] = forces[f"M{axial_axis}"]
        extra |= {c: unit_of(forces, f"M{axial_axis}") for c in ("BENDING", "TORSION")}
    if keep_forces:
        kept = [c for c in force_cols + moment_cols if c in forces.columns]
        out = pd.concat([out, forces[kept]], axis=1)
    carry_units(forces, out, extra={k: v for k, v in extra.items() if v})
    out.attrs["title"] = "Fastener loads"
    return out


class FastenerLoadsParams(BaseModel):
    """Parameters of :class:`FastenerLoads`."""

    model_config = ConfigDict(extra="forbid")

    axial_axis: Axis = Field(default="X", description="Fastener axis (component of the forces)")
    tension_mode: Literal["signed", "tension_only"] = Field(
        default="signed", description="signed = compression negative; tension_only = set to 0"
    )
    include_moments: bool = Field(default=False, description="Add BENDING and TORSION")
    keep_forces: bool = Field(default=False, description="Keep the original force columns")


class FastenerLoads(Block[FastenerLoadsParams]):
    """Tension and shear per fastener from CBUSH forces or RBE2 loads."""

    type_id = "loads.fastener_loads"
    category = "Loads/Fasteners"
    title = "Fastener loads"
    description = (
        "TENSION = force along the fastener axis, SHEAR = resultant of the two other "
        "components; per fastener and load case."
    )
    inputs = (Port("forces", PortType.TABLE, label="Forces"),)
    outputs = (Port("loads", PortType.TABLE, label="Loads"),)
    Params = FastenerLoadsParams

    def run(
        self, params: FastenerLoadsParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Compute the fastener loads."""
        out = fastener_loads(
            inputs["forces"],
            axial_axis=params.axial_axis,
            tension_mode=params.tension_mode,
            include_moments=params.include_moments,
            keep_forces=params.keep_forces,
        )
        return {"loads": out}


BLOCKS: tuple[type[Block[Any]], ...] = (FastenerLoads,)
