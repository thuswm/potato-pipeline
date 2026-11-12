"""Table manipulation blocks: select, filter, sort, compute, envelope, join, concat (spec 03).

Expressions use pandas ``query``/``eval`` syntax. Column names that are not valid Python
names are written in backticks, e.g. ```Max load` > 100``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from apb.blocks.table.common import (
    TableError,
    carry_units,
    common_unit_system_or_error,
    require_columns,
    unit_of,
)
from apb.engine.block import Block, RunContext
from apb.engine.types import Port, PortType
from apb.units import Quantity, get_units, set_units

CATEGORY = "Table"

_TABLE_IN = (Port("table", PortType.TABLE, label="Table"),)
_TABLE_OUT = (Port("table", PortType.TABLE, label="Table"),)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------
# Select / rename
# --------------------------------------------------------------------------------------


class SelectParams(_Params):
    """Parameters of :class:`Select`."""

    columns: list[str] = Field(default_factory=list, description="Columns to keep; empty = all")
    rename: dict[str, str] = Field(default_factory=dict, description="Old name -> new name")


class Select(Block[SelectParams]):
    """Keep and/or rename columns."""

    type_id = "table.select"
    category = CATEGORY
    title = "Select columns"
    description = "Keep a subset of the columns (in the given order) and rename columns."
    inputs = _TABLE_IN
    outputs = _TABLE_OUT
    Params = SelectParams

    def run(self, params: SelectParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Select and rename."""
        df: pd.DataFrame = inputs["table"]
        cols = params.columns or list(df.columns)
        require_columns(df, [*cols, *params.rename])
        out = df[cols].rename(columns=params.rename)
        return {"table": carry_units(df, out, rename=params.rename)}


# --------------------------------------------------------------------------------------
# Filter
# --------------------------------------------------------------------------------------


class FilterParams(_Params):
    """Parameters of :class:`Filter`."""

    expression: str = Field(description="Condition, e.g. SUBCASE in [1, 2] and EID > 1000")


class Filter(Block[FilterParams]):
    """Keep the rows for which a condition holds."""

    type_id = "table.filter"
    category = CATEGORY
    title = "Filter rows"
    description = "Keep rows matching a pandas query expression."
    inputs = _TABLE_IN
    outputs = _TABLE_OUT
    Params = FilterParams

    def run(self, params: FilterParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Apply the query."""
        df: pd.DataFrame = inputs["table"]
        try:
            out = df.query(params.expression).reset_index(drop=True)
        except Exception as exc:
            raise TableError(f"Invalid filter expression {params.expression!r}: {exc}") from exc
        ctx.log(f"{len(out)} of {len(df)} rows kept")
        return {"table": carry_units(df, out)}


# --------------------------------------------------------------------------------------
# Sort
# --------------------------------------------------------------------------------------


class SortParams(_Params):
    """Parameters of :class:`Sort`."""

    by: list[str] = Field(description="Columns to sort by (first = primary)")
    ascending: bool = Field(default=True, description="Ascending order")


class Sort(Block[SortParams]):
    """Sort rows."""

    type_id = "table.sort"
    category = CATEGORY
    title = "Sort"
    inputs = _TABLE_IN
    outputs = _TABLE_OUT
    Params = SortParams

    def run(self, params: SortParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Sort the table."""
        df: pd.DataFrame = inputs["table"]
        require_columns(df, params.by)
        out = df.sort_values(params.by, ascending=params.ascending, kind="stable")
        return {"table": carry_units(df, out.reset_index(drop=True))}


# --------------------------------------------------------------------------------------
# Compute
# --------------------------------------------------------------------------------------


class ComputeParams(_Params):
    """Parameters of :class:`Compute`."""

    new_column: str = Field(description="Name of the new (or replaced) column")
    expression: str = Field(description="Formula, e.g. sqrt(FY**2 + FZ**2)")
    unit: str = Field(default="", description="Unit label of the result, e.g. N; empty = none")


class Compute(Block[ComputeParams]):
    """Add a column computed from a formula."""

    type_id = "table.compute"
    category = CATEGORY
    title = "Computed column"
    description = "Add a column computed with a pandas eval formula (sqrt, abs, log ... allowed)."
    inputs = _TABLE_IN
    outputs = _TABLE_OUT
    Params = ComputeParams

    def run(self, params: ComputeParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Evaluate the formula."""
        df: pd.DataFrame = inputs["table"]
        try:
            values = df.eval(params.expression)
        except Exception as exc:
            raise TableError(f"Invalid formula {params.expression!r}: {exc}") from exc
        out = df.copy()
        out[params.new_column] = values
        extra = {params.new_column: params.unit} if params.unit else {}
        carry_units(df, out, extra=extra)
        if not params.unit:
            out.attrs["units"].pop(params.new_column, None)
        return {"table": out}


# --------------------------------------------------------------------------------------
# Envelope
# --------------------------------------------------------------------------------------

EnvelopeMode = Literal["max", "min", "abs_max"]


def governing_index(values: pd.Series, mode: EnvelopeMode) -> Any:
    """Return the index label of the governing value of a series.

    Args:
        values: Values to search.
        mode: ``max``, ``min`` or ``abs_max`` (largest magnitude, sign kept).

    Returns:
        Index label of the governing row.
    """
    if mode == "max":
        return values.idxmax()
    if mode == "min":
        return values.idxmin()
    return values.abs().idxmax()


class EnvelopeParams(_Params):
    """Parameters of :class:`Envelope`."""

    group_by: list[str] = Field(default_factory=list, description="e.g. EID; empty = whole table")
    columns: list[str] = Field(description="Value columns to envelope")
    mode: EnvelopeMode = Field(default="max", description="max, min or abs_max (sign kept)")
    governing_columns: list[str] = Field(
        default_factory=lambda: ["SUBCASE"],
        description="Columns of the governing row to report, e.g. SUBCASE, EID "
        "(adds <value>_<column>)",
    )
    keep_row: bool = Field(
        default=False, description="Return the full governing row (only with one value column)"
    )


class Envelope(Block[EnvelopeParams]):
    """Maximum / minimum / largest magnitude per group, with the governing case and entity."""

    type_id = "table.envelope"
    category = CATEGORY
    title = "Envelope"
    description = (
        "Per group (e.g. per element) the max, min or largest-magnitude value of each column, "
        "and the case where it occurs."
    )
    inputs = _TABLE_IN
    outputs = _TABLE_OUT
    Params = EnvelopeParams

    def run(
        self, params: EnvelopeParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Compute the envelope."""
        df: pd.DataFrame = inputs["table"]
        governing = [c for c in params.governing_columns if c in df.columns]
        for missing in sorted(set(params.governing_columns) - set(governing)):
            ctx.log(f"No column {missing!r}; not reported")
        require_columns(df, [*params.group_by, *params.columns])
        if df.empty:
            raise TableError("Envelope of an empty table")
        groups = df.groupby(params.group_by, sort=True) if params.group_by else [((), df)]

        if params.keep_row:
            if len(params.columns) != 1:
                raise TableError("keep_row needs exactly one value column")
            col = params.columns[0]
            rows = [governing_index(g[col], params.mode) for _, g in groups]
            return {"table": carry_units(df, df.loc[rows].reset_index(drop=True))}

        records: list[dict[str, Any]] = []
        for key, g in groups:
            keys = key if isinstance(key, tuple) else (key,)
            rec: dict[str, Any] = dict(zip(params.group_by, keys, strict=True))
            for col in params.columns:
                idx = governing_index(g[col], params.mode)
                rec[col] = g.at[idx, col]
                for gc in governing:
                    rec[f"{col}_{gc}"] = g.at[idx, gc]
            records.append(rec)
        out = pd.DataFrame.from_records(records)
        carry_units(df, out)
        return {"table": out}


# --------------------------------------------------------------------------------------
# Join / concat
# --------------------------------------------------------------------------------------


class JoinParams(_Params):
    """Parameters of :class:`Join`."""

    on: list[str] = Field(description="Key columns present in both tables, e.g. EID")
    how: Literal["inner", "left", "right", "outer"] = Field(
        default="left", description="Which rows to keep"
    )


class Join(Block[JoinParams]):
    """Join two tables on key columns (like VLOOKUP / database join)."""

    type_id = "table.join"
    category = CATEGORY
    title = "Join"
    description = "Combine the columns of two tables on matching key values."
    inputs = (
        Port("left", PortType.TABLE, label="Left"),
        Port("right", PortType.TABLE, label="Right"),
    )
    outputs = _TABLE_OUT
    Params = JoinParams

    def run(self, params: JoinParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Join the tables."""
        left: pd.DataFrame = inputs["left"]
        right: pd.DataFrame = inputs["right"]
        require_columns(left, params.on, "left table")
        require_columns(right, params.on, "right table")
        out = left.merge(right, on=params.on, how=params.how, suffixes=("", "_RIGHT"))
        clashes = {c for c in right.columns if c in left.columns and c not in params.on}
        carry_units(left, out)
        system = common_unit_system_or_error([left, right])
        units = get_units(left)
        units.update(
            {(f"{c}_RIGHT" if c in clashes else c): u for c, u in get_units(right).items()}
        )
        set_units(out, system, units)
        return {"table": out}


class ConcatParams(_Params):
    """Parameters of :class:`Concat`."""

    add_source_column: bool = Field(
        default=False, description="Add a SOURCE column naming the input each row came from"
    )


def _source_name(df: pd.DataFrame, index: int) -> str:
    """Return a short name for the origin of a table (file name, title or position)."""
    if df.attrs.get("source_file"):
        return Path(str(df.attrs["source_file"])).name
    return str(df.attrs.get("title") or f"input {index + 1}")


class Concat(Block[ConcatParams]):
    """Stack tables on top of each other (e.g. results of several runs)."""

    type_id = "table.concat"
    category = CATEGORY
    title = "Stack tables"
    description = "Append the rows of all connected tables; columns are matched by name."
    inputs = (Port("tables", PortType.TABLE, label="Tables", multiple=True),)
    outputs = _TABLE_OUT
    Params = ConcatParams

    def run(self, params: ConcatParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Concatenate."""
        tables: list[pd.DataFrame] = inputs["tables"]
        if not tables:
            raise TableError("No tables connected")
        parts = [
            t.assign(SOURCE=_source_name(t, i)) if params.add_source_column else t
            for i, t in enumerate(tables)
        ]
        out = pd.concat(parts, ignore_index=True)
        carry_units(tables, out)
        out.attrs.pop("source_file", None)
        return {"table": out}


# --------------------------------------------------------------------------------------
# Pick a scalar
# --------------------------------------------------------------------------------------


class PickParams(_Params):
    """Parameters of :class:`Pick`."""

    column: str = Field(description="Column to take the value from")
    row: Literal["max", "min", "abs_max", "first", "last"] = Field(
        default="max", description="Which value"
    )
    where: str = Field(default="", description="Optional filter expression applied first")


class Pick(Block[PickParams]):
    """Take a single value from a table, e.g. the maximum stress."""

    type_id = "table.pick"
    category = CATEGORY
    title = "Pick value"
    description = "Single value from a column: max, min, largest magnitude, first or last."
    inputs = _TABLE_IN
    outputs = (Port("value", PortType.SCALAR, label="Value"),)
    Params = PickParams

    def run(self, params: PickParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Pick the value."""
        df: pd.DataFrame = inputs["table"]
        require_columns(df, [params.column])
        if params.where:
            df = df.query(params.where)
        if df.empty:
            raise TableError("No rows to pick a value from")
        series = df[params.column]
        if params.row in ("max", "min", "abs_max"):
            value = series.loc[governing_index(series, params.row)]
        else:
            value = series.iloc[0 if params.row == "first" else -1]
        number = float(value) if isinstance(value, float | np.floating) else value
        if not isinstance(number, int | float | np.integer):
            raise TableError(f"Value {value!r} in column {params.column!r} is not a number")
        unit = unit_of(df, params.column)
        ctx.log(f"{params.column} ({params.row}) = {number} {unit}".rstrip())
        return {"value": Quantity(float(number), unit) if unit else float(number)}


BLOCKS: tuple[type[Block[Any]], ...] = (
    Select,
    Filter,
    Sort,
    Compute,
    Envelope,
    Join,
    Concat,
    Pick,
)
