"""Constants and unit blocks: ``table.constant``, ``units.convert``, ``table.set_units``."""

from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apb.blocks.table.common import TableError, require_columns
from apb.engine.block import Block, RunContext
from apb.engine.types import Port, PortType
from apb.units import Quantity, UnitSystem, convert_table, get_units, known_units, set_units


def _check_unit(unit: str) -> str:
    """Validate a unit label (empty allowed)."""
    if unit and unit not in known_units():
        raise ValueError(f"unknown unit {unit!r}; known: {', '.join(known_units())}")
    return unit


class ConstantParams(BaseModel):
    """Parameters of :class:`Constant`."""

    model_config = ConfigDict(extra="forbid")

    value: float = Field(description="Value")
    unit: str = Field(
        default="",
        description="Unit label, e.g. MPa; empty = none",
        json_schema_extra={"apb_widget": "unit"},
    )

    @field_validator("unit")
    @classmethod
    def _valid_unit(cls, value: str) -> str:
        return _check_unit(value)


class Constant(Block[ConstantParams]):
    """A fixed value, e.g. an allowable or a safety factor."""

    type_id = "table.constant"
    category = "Input/Values"
    title = "Constant"
    description = (
        "A number with optional unit, e.g. an allowable stress or a safety factor. "
        "For several values use Constants."
    )
    outputs = (Port("value", PortType.SCALAR, label="Value"),)
    Params = ConstantParams

    def run(
        self, params: ConstantParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Return the value."""
        return {"value": Quantity(params.value, params.unit) if params.unit else params.value}


class ConvertParams(BaseModel):
    """Parameters of :class:`ConvertUnits`."""

    model_config = ConfigDict(extra="forbid")

    to: UnitSystem = Field(title="Convert to", description="Target unit system")


class ConvertUnits(Block[ConvertParams]):
    """Convert a table or a value between SI (m) and SI_mm (mm)."""

    type_id = "units.convert"
    category = "Units"
    title = "Convert units"
    description = "Convert all columns with units (or a value with unit) to SI or SI_mm."
    inputs = (Port("value", PortType.ANY, label="Table or value"),)
    outputs = (Port("value", PortType.ANY, label="Converted"),)
    Params = ConvertParams

    def run(self, params: ConvertParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Convert the input."""
        value = inputs["value"]
        if isinstance(value, pd.DataFrame):
            if not get_units(value):
                ctx.log("WARNING: table has no units; nothing converted")
            return {"value": convert_table(value, params.to)}
        if isinstance(value, Quantity):
            return {"value": value.to(params.to)}
        raise TableError(
            f"Cannot convert {type(value).__name__}: only tables and values with units"
        )


class SetUnitsParams(BaseModel):
    """Parameters of :class:`SetUnits`."""

    model_config = ConfigDict(extra="forbid")

    unit_system: UnitSystem = Field(description="Unit system of the table")
    units: dict[str, str] = Field(
        default_factory=dict, description="Column -> unit label, e.g. {'LOAD': 'N'}"
    )
    replace: bool = Field(
        title="Replace existing units", default=False, description="Drop units already on the table"
    )

    @field_validator("units")
    @classmethod
    def _valid_units(cls, value: dict[str, str]) -> dict[str, str]:
        for unit in value.values():
            _check_unit(unit)
        return value


class SetUnits(Block[SetUnitsParams]):
    """Declare the unit system and column units of a table (e.g. read from Excel)."""

    type_id = "table.set_units"
    category = "Units"
    title = "Set units"
    description = "Attach units to columns of a table that has none (e.g. from Excel or CSV)."
    inputs = (Port("table", PortType.TABLE, label="Table"),)
    outputs = (Port("table", PortType.TABLE, label="Table"),)
    Params = SetUnitsParams
    advanced_params = ("replace",)

    def run(
        self, params: SetUnitsParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Attach the units."""
        df: pd.DataFrame = inputs["table"]
        require_columns(df, params.units)
        units = {} if params.replace else get_units(df)
        units.update(params.units)
        out = df.copy()
        out.attrs = dict(df.attrs)
        set_units(out, params.unit_system, units)
        return {"table": out}


BLOCKS: tuple[type[Block[Any]], ...] = (Constant, ConvertUnits, SetUnits)
