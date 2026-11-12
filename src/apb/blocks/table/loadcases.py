"""Collecting results over load cases (spec 03 §3.1).

``table.load_case_table`` turns a long table (one row per entity and load case) into the
wide format used in reports and spreadsheets: one row per entity, one column per load case,
followed by envelope columns. With several value columns (e.g. ``TENSION`` and ``SHEAR``)
the groups are placed side by side under a two-level header::

    |      |   TENSION [N]                    |   SHEAR [N]                      |
    | EID  | LC1 | LC2 | LC3 | MAX | MAX_CASE | LC1 | LC2 | LC3 | MAX | MAX_CASE |
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from apb.blocks.table.common import TableError, require_columns
from apb.engine.block import Block, RunContext
from apb.engine.types import Port, PortType
from apb.units import get_unit_system, get_units, set_units

CATEGORY = "Table/Load cases"

EnvelopeMode = Literal["max", "min", "abs_max"]
Layout = Literal["side_by_side", "flat"]

ENVELOPE_NAMES: dict[EnvelopeMode, str] = {"max": "MAX", "min": "MIN", "abs_max": "ABS_MAX"}
LABEL_COLUMN = "LABEL"


def case_label(case: Any, labels: Mapping[Any, str]) -> str:
    """Return the display name of a load case: its label, or ``LC<id>``."""
    for key in (case, str(case)):
        if labels.get(key):
            return labels[key]
    try:
        return f"LC{int(case)}"
    except (TypeError, ValueError):
        return str(case)


def labels_from_table(table: pd.DataFrame | None) -> dict[Any, str]:
    """Read load case names from a table: first column = case ID, second = name."""
    if table is None or table.empty:
        return {}
    if len(table.columns) < 2:
        raise TableError("Load case names table needs two columns: case ID and name")
    ids, names = table.iloc[:, 0], table.iloc[:, 1]
    return {_normal_key(i): str(n) for i, n in zip(ids, names, strict=True) if pd.notna(n)}


def _normal_key(value: Any) -> Any:
    """Use plain ints for whole-number case IDs so that 1, 1.0 and '1' match."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return int(f) if f.is_integer() else value


def _envelope(part: pd.DataFrame, mode: EnvelopeMode) -> tuple[np.ndarray, list[Any]]:
    """Return the governing value and the governing column name per row (NaN-safe)."""
    arr = part.to_numpy(dtype=float)
    key = np.abs(arr) if mode == "abs_max" else (arr if mode == "max" else -arr)
    filled = np.where(np.isnan(key), -np.inf, key)
    idx = filled.argmax(axis=1)
    all_nan = np.isnan(arr).all(axis=1)
    values = arr[np.arange(len(arr)), idx]
    values[all_nan] = np.nan
    names = [None if empty else part.columns[i] for i, empty in zip(idx, all_nan, strict=True)]
    return values, names


def load_case_table(
    df: pd.DataFrame,
    *,
    id_columns: Sequence[str],
    case_column: str,
    value_columns: Sequence[str],
    labels: Mapping[Any, str] | None = None,
    envelope: Sequence[EnvelopeMode] = ("max",),
    layout: Layout = "side_by_side",
) -> pd.DataFrame:
    """Pivot a long table to one row per entity and one column per load case.

    Args:
        df: Long table.
        id_columns: Entity key columns, e.g. ``["EID"]`` (or ``["EID", "GRID"]``).
        case_column: Column with the load case, e.g. ``SUBCASE``.
        value_columns: Columns to tabulate, e.g. ``["TENSION", "SHEAR"]``.
        labels: Case ID -> name; unnamed cases are called ``LC<id>``.
        envelope: Envelope columns to add after the load cases (each also adds a
            ``<NAME>_CASE`` column with the governing case).
        layout: ``side_by_side`` = two-level header when there are several value
            columns; ``flat`` = single-level names such as ``TENSION_LC1``.

    Returns:
        The wide table, with units metadata.

    Raises:
        TableError: On missing columns, duplicate rows per entity and case, or duplicate
            load case names.
    """
    if not value_columns:
        raise TableError("Select at least one value column")
    require_columns(df, [*id_columns, case_column, *value_columns])
    dupes = df.duplicated([*id_columns, case_column])
    if dupes.any():
        example = df.loc[dupes, [*id_columns, case_column]].iloc[0].to_dict()
        raise TableError(
            f"Several rows per entity and load case (e.g. {example}). Add more ID columns "
            "or filter the table first (e.g. on STATION or GRID)."
        )
    labels = {_normal_key(k): v for k, v in (labels or {}).items()}
    cases = sorted(df[case_column].unique(), key=lambda c: (str(type(c)), c))
    names = [case_label(_normal_key(c), labels) for c in cases]
    if len(set(names)) != len(names):
        raise TableError(f"Load case names are not unique: {names}")

    wide = df.pivot(index=list(id_columns), columns=case_column, values=list(value_columns))
    units = get_units(df)
    parts: dict[str, pd.DataFrame] = {}
    for value in value_columns:
        part = pd.DataFrame(wide[value][cases]).copy()
        part.columns = pd.Index(names)
        for mode in envelope:
            name = ENVELOPE_NAMES[mode]
            values, governing = _envelope(part[names], mode)
            part[name] = values
            part[f"{name}_CASE"] = governing
        parts[value] = part

    out_units: dict[str, str] = {}
    if len(value_columns) > 1 and layout == "side_by_side":
        out = pd.concat(parts, axis=1)
        out = out.reset_index()
        out_units = {v: units[v] for v in value_columns if v in units}
    else:
        renamed: list[pd.DataFrame] = []
        single = len(value_columns) == 1
        for value, part in parts.items():
            cols = {c: (c if single else f"{value}_{c}") for c in part.columns}
            renamed.append(part.rename(columns=cols))
            if value in units:
                out_units.update(
                    {
                        new: units[value]
                        for old, new in cols.items()
                        if not str(old).endswith("_CASE")
                    }
                )
        out = pd.concat(renamed, axis=1).reset_index()
    out.attrs = {"title": "Load case table", "case_column": case_column}
    set_units(out, get_unit_system(df), out_units)
    return out


def _default_envelope() -> list[EnvelopeMode]:
    """Default envelope: maximum only."""
    return ["max"]


class CaseLabelsParams(BaseModel):
    """Parameters of :class:`CaseLabels`."""

    model_config = ConfigDict(extra="forbid")

    labels: dict[int, str] = Field(
        default_factory=dict, description="Subcase ID -> load case name, e.g. {1: 'Landing'}"
    )


class CaseLabels(Block[CaseLabelsParams]):
    """Table of load case names, to connect to load case tables (or join onto results)."""

    type_id = "table.case_labels"
    category = CATEGORY
    title = "Load case names"
    description = "Subcase ID -> load case name."
    outputs = (Port("table", PortType.TABLE, label="Names"),)
    Params = CaseLabelsParams

    def run(
        self, params: CaseLabelsParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Return the names as a table."""
        out = pd.DataFrame(
            {"SUBCASE": list(params.labels), LABEL_COLUMN: list(params.labels.values())}
        )
        out.attrs["title"] = "Load case names"
        return {"table": out}


class LoadCaseTableParams(BaseModel):
    """Parameters of :class:`LoadCaseTable`."""

    model_config = ConfigDict(extra="forbid")

    id_columns: list[str] = Field(default_factory=lambda: ["EID"], description="Entity columns")
    case_column: str = Field(default="SUBCASE", description="Load case column")
    value_columns: list[str] = Field(description="Columns to tabulate, e.g. TENSION, SHEAR")
    envelope: list[EnvelopeMode] = Field(
        default_factory=_default_envelope, description="Envelope columns: max, min, abs_max"
    )
    layout: Layout = Field(
        default="side_by_side", description="side_by_side (two header rows) or flat"
    )
    labels: dict[str, str] = Field(
        default_factory=dict, description="Case ID -> name (overrides the names input)"
    )


class LoadCaseTable(Block[LoadCaseTableParams]):
    """One row per entity, one column per load case, plus the envelope."""

    type_id = "table.load_case_table"
    category = CATEGORY
    title = "Load case table"
    description = (
        "Pivot results to one column per load case with max/min envelope and governing case; "
        "several value columns are placed side by side."
    )
    inputs = (
        Port("table", PortType.TABLE, label="Table"),
        Port("labels", PortType.TABLE, label="Case names", optional=True),
    )
    outputs = (Port("table", PortType.TABLE, label="Table"),)
    Params = LoadCaseTableParams

    def run(
        self, params: LoadCaseTableParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Build the table."""
        labels = labels_from_table(inputs["labels"])
        labels.update({_normal_key(k): v for k, v in params.labels.items()})
        out = load_case_table(
            inputs["table"],
            id_columns=params.id_columns,
            case_column=params.case_column,
            value_columns=params.value_columns,
            labels=labels,
            envelope=params.envelope,
            layout=params.layout,
        )
        ctx.log(f"{len(out)} rows x {len(out.columns)} columns")
        return {"table": out}


BLOCKS: tuple[type[Block[Any]], ...] = (CaseLabels, LoadCaseTable)
