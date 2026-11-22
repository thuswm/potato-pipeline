"""The ``check.requirement`` block: one value against a requirement (spec 03 §3.4).

Typical use: "the first eigenfrequency must be higher than 20 Hz". The block does not stop
the pipeline when the requirement is not met: the report must still be produced and show
the failure. A failure is logged with the prefix ``WARNING:``.
"""

from __future__ import annotations

import math
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apb.engine.block import Block, RunContext
from apb.engine.types import Port, PortType
from apb.units import Quantity, known_units, set_units

Comparison = Literal["greater_than", "greater_equal", "less_than", "less_equal"]

#: Comparison -> symbol shown in the result table.
SYMBOLS: dict[str, str] = {
    "greater_than": ">",
    "greater_equal": "≥",
    "less_than": "<",
    "less_equal": "≤",
}

#: Comparison -> label in the property panel.
COMPARISON_LABELS: dict[str, Any] = {
    "greater_than": "> greater than",
    "greater_equal": "≥ greater or equal",
    "less_than": "< less than",
    "less_equal": "≤ less or equal",
}

PASS = "PASS"
FAIL = "FAIL"


class CheckError(ValueError):
    """Raised when a check cannot be evaluated (missing limit, NaN value, unit clash)."""


def evaluate(value: float, limit: float, comparison: Comparison) -> tuple[bool, float, str]:
    """Decide a requirement and compute its margin of safety (CHK-01, CHK-02).

    Args:
        value: The value to check.
        limit: The limit of the requirement.
        comparison: How value and limit must relate.

    Returns:
        ``(passed, margin, note)``. The margin is ``value / limit - 1`` for ``greater_*``
        and ``limit / value - 1`` for ``less_*``; it is NaN, with the reason in ``note``,
        when it is not defined (limit <= 0, or value <= 0 respectively).
    """
    passed = {
        "greater_than": value > limit,
        "greater_equal": value >= limit,
        "less_than": value < limit,
        "less_equal": value <= limit,
    }[comparison]
    margin = math.nan
    note = ""
    if comparison.startswith("greater"):
        if limit > 0:
            margin = value / limit - 1.0
        else:
            note = "margin undefined: the limit is not positive"
    elif value > 0:
        margin = limit / value - 1.0
    else:
        note = "margin undefined: the value is not positive"
    return passed, margin, note


def _split(value: Any, what: str) -> tuple[float, str]:
    """Return number and unit label of a scalar input."""
    if isinstance(value, Quantity):
        return float(value.value), value.unit
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value), ""
    raise CheckError(f"The {what} must be a number, got {type(value).__name__}")


class RequirementParams(BaseModel):
    """Parameters of :class:`RequirementCheck`."""

    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(
        title="Requirement (ID and text)",
        default="",
        description="Id and description, e.g. 'REQ-DYN-001 First eigenfrequency'",
    )
    comparison: Comparison = Field(
        default="greater_than",
        description="How the value must relate to the limit",
        json_schema_extra={"apb_labels": COMPARISON_LABELS},
    )
    limit: float | None = Field(
        default=None, description="Limit (number or ${variable}); empty if the limit input is used"
    )
    unit: str = Field(
        default="",
        description="Unit label shown when neither value nor limit carries one",
        json_schema_extra={"apb_widget": "unit"},
    )

    @field_validator("unit")
    @classmethod
    def _valid_unit(cls, value: str) -> str:
        if value and value not in known_units():
            raise ValueError(f"unknown unit {value!r}; known: {', '.join(known_units())}")
        return value


class RequirementCheck(Block[RequirementParams]):
    """Compare a value with a requirement: PASS or FAIL, with the margin of safety."""

    type_id = "check.requirement"
    category = "Checks"
    title = "Requirement check"
    description = (
        "Checks one value against a limit (e.g. first eigenfrequency higher than 20 Hz). "
        "Gives a one-row table, PASS/FAIL and the margin. A failure does not stop the pipeline."
    )
    inputs = (
        Port("value", PortType.SCALAR, label="Value"),
        Port("limit", PortType.SCALAR, label="Limit", optional=True),
    )
    outputs = (
        Port("result", PortType.TABLE, label="Result"),
        Port("status", PortType.TEXT, label="Status"),
        Port("margin", PortType.SCALAR, label="Margin"),
    )
    Params = RequirementParams
    advanced_params = ("unit",)

    def run(
        self, params: RequirementParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Evaluate the requirement."""
        value, value_unit = _split(inputs["value"], "value")
        if math.isnan(value):
            raise CheckError("The value is not a number (NaN): nothing to check")
        if inputs.get("limit") is not None:
            limit, limit_unit = _split(inputs["limit"], "limit")
        elif params.limit is not None:
            limit, limit_unit = params.limit, ""
        else:
            raise CheckError("No limit: set the 'limit' parameter or connect the 'limit' input")
        if math.isnan(limit):
            raise CheckError("The limit is not a number (NaN)")
        if value_unit and limit_unit and value_unit != limit_unit:
            raise CheckError(
                f"Value is in {value_unit} but the limit in {limit_unit}: "
                "convert one of them first (block 'Convert units')"
            )
        if value_unit and not limit_unit and inputs.get("limit") is None:
            ctx.log(f"limit {limit:g} interpreted as {value_unit}")
        unit = value_unit or limit_unit or params.unit

        passed, margin, note = evaluate(value, limit, params.comparison)
        status = PASS if passed else FAIL
        symbol = SYMBOLS[params.comparison]
        suffix = f" {unit}" if unit else ""
        name = f"{params.requirement}: " if params.requirement else ""
        line = f"{name}{value:.6g}{suffix} {symbol} {limit:.6g}{suffix} -> {status}"
        if not math.isnan(margin):
            line += f" (margin {margin:.4g})"
        if not passed:
            line = f"WARNING: requirement not met. {line}"
        ctx.log(line)
        if note:
            ctx.log(note)

        table = pd.DataFrame(
            {
                "REQUIREMENT": [params.requirement],
                "VALUE": [value],
                "CONDITION": [symbol],
                "LIMIT": [limit],
                "MARGIN": [margin],
                "STATUS": [status],
            }
        )
        set_units(
            table, None, {"VALUE": unit, "LIMIT": unit, "MARGIN": "-"} if unit else {"MARGIN": "-"}
        )
        table.attrs["title"] = "Requirement check"
        return {"result": table, "status": status, "margin": margin}


BLOCKS: tuple[type[Block[Any]], ...] = (RequirementCheck,)
