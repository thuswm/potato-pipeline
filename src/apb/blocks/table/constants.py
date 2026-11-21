"""Constants block ``table.constants``: the project constants in one table (spec 03 §3.5).

Each constant has a name, a numeric value, an optional unit and a description. The block
outputs them as one table (for the report) and as one Scalar port per constant. Because it
has no inputs, its values are known before the run: every constant can be used as
``${name}`` in any block parameter (CON-07, :meth:`Block.provided_variables`).
"""

from __future__ import annotations

import json
import keyword
import math
import re
from collections.abc import Mapping
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apb.engine.block import Block, RunContext
from apb.engine.types import Port, PortType
from apb.units import Quantity, known_units

#: Name of the table output; not allowed as a constant name (it would clash with the port).
TABLE_PORT = "table"

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Column names of the output table (CON-05).
COLUMNS = ("NAME", "VALUE", "UNIT", "DESCRIPTION")


def name_problem(name: str) -> str:
    """Return why a constant name is not allowed, or ``""`` if it is fine (CON-01).

    Args:
        name: Proposed name.

    Returns:
        A message for the user, or an empty string.
    """
    if not name:
        return "Enter a name"
    if not _NAME.fullmatch(name):
        return "Use letters, digits and _ only, starting with a letter or _ (no spaces)"
    if keyword.iskeyword(name):
        return f"{name!r} is a Python keyword"
    if name == TABLE_PORT:
        return f"{TABLE_PORT!r} is the name of the table output"
    return ""


def unit_problem(unit: str) -> str:
    """Return why a unit label is not allowed, or ``""`` (CON-03)."""
    if unit and unit not in known_units():
        return f"Unknown unit {unit!r}"
    return ""


class ConstantRow(BaseModel):
    """One constant (one row of the table)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Name, used as ${name} and as port name")
    value: float = Field(description="Value")
    unit: str = Field(default="", description="Unit label; empty = dimensionless")
    description: str = Field(default="", description="What the constant is, where it comes from")

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        problem = name_problem(value)
        if problem:
            raise ValueError(problem)
        return value

    @field_validator("value")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("the value must be a finite number")
        return value

    @field_validator("unit")
    @classmethod
    def _valid_unit(cls, value: str) -> str:
        problem = unit_problem(value)
        if problem:
            raise ValueError(f"{problem}; known: {', '.join(known_units())}")
        return value

    @property
    def quantity(self) -> Quantity | float:
        """The value with its unit (plain number when dimensionless)."""
        return Quantity(self.value, self.unit) if self.unit else self.value


class ConstantsParams(BaseModel):
    """Parameters of :class:`Constants`."""

    model_config = ConfigDict(extra="forbid")

    constants: list[ConstantRow] = Field(
        default_factory=list, description="Constants: name, value, unit, description"
    )

    @field_validator("constants")
    @classmethod
    def _unique(cls, rows: list[ConstantRow]) -> list[ConstantRow]:
        seen: set[str] = set()
        for row in rows:
            if row.name in seen:
                raise ValueError(f"the name {row.name!r} is used twice")
            seen.add(row.name)
        return rows


class Constants(Block[ConstantsParams]):
    """Project constants (safety factors, allowables, limits) in one table."""

    type_id = "table.constants"
    category = "Input/Values"
    title = "Constants"
    description = (
        "Project constants (safety factors, allowables, requirement limits) in one table. "
        "Each constant has its own output port and can be used as ${name} in any block."
    )
    outputs = (Port(TABLE_PORT, PortType.TABLE, label="Table"),)
    Params = ConstantsParams
    provides_variables = True

    @classmethod
    def output_ports(cls, params: BaseModel | None) -> tuple[Port, ...]:
        """The table plus one Scalar port per constant (CON-06)."""
        if not isinstance(params, ConstantsParams):
            return cls.outputs
        return cls.outputs + tuple(Port(row.name, PortType.SCALAR) for row in params.constants)

    @classmethod
    def provided_variables(cls, params: BaseModel) -> dict[str, Any]:
        """Every constant as ``${name}`` (CON-07); the plain number, without unit."""
        assert isinstance(params, ConstantsParams)
        return {row.name: row.value for row in params.constants}

    @classmethod
    def port_fingerprint(cls, params: BaseModel, port: str) -> str | None:
        """One constant's port depends on that row only (exact staleness, §3.5.1)."""
        assert isinstance(params, ConstantsParams)
        row = next((r for r in params.constants if r.name == port), None)
        if row is None:
            return None  # the table: depends on all rows, use the block's key
        return json.dumps([cls.type_id, cls.version, row.name, row.value, row.unit])

    @classmethod
    def apply_overrides(
        cls, params: dict[str, Any], overrides: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Use ``--set name=value`` for the block's own outputs too (CON-09)."""
        rows = params.get("constants")
        if not isinstance(rows, list):
            return params
        changed = [
            {**row, "value": overrides[row["name"]]}
            if isinstance(row, dict) and row.get("name") in overrides
            else row
            for row in rows
        ]
        return {**params, "constants": changed}

    def run(
        self, params: ConstantsParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Return the table and one value per constant."""
        table = pd.DataFrame(
            [[r.name, r.value, r.unit, r.description] for r in params.constants],
            columns=list(COLUMNS),
        )
        table["VALUE"] = table["VALUE"].astype(float)
        table.attrs["title"] = "Constants"
        outputs: dict[str, Any] = {TABLE_PORT: table}
        for row in params.constants:
            outputs[row.name] = row.quantity
            ctx.log(f"{row.name} = {row.value:g}{' ' + row.unit if row.unit else ''}")
        return outputs


BLOCKS: tuple[type[Block[Any]], ...] = (Constants,)
