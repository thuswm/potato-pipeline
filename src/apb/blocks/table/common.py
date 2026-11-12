"""Helpers shared by the table blocks: column checks and units propagation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import pandas as pd

from apb.units import (
    UnitError,
    UnitSystem,
    common_unit_system,
    get_unit_system,
    get_units,
    set_units,
)


class TableError(ValueError):
    """Raised for invalid table operations, e.g. a missing column (TAB-04)."""


def require_columns(df: pd.DataFrame, columns: Iterable[str], what: str = "table") -> None:
    """Check that columns exist, with a helpful message listing the available ones.

    Args:
        df: Table to check.
        columns: Required column names.
        what: Name of the table in the message, e.g. ``"left table"``.

    Raises:
        TableError: If a column is missing.
    """
    missing = [c for c in columns if c not in df.columns]
    if missing:
        available = ", ".join(str(c) for c in df.columns)
        raise TableError(f"Column(s) {missing} not found in {what}. Available: {available}")


def carry_units(
    sources: pd.DataFrame | Sequence[pd.DataFrame],
    out: pd.DataFrame,
    *,
    rename: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Copy unit system and column units from the source table(s) to a result (in place).

    Other ``attrs`` (title, source file ...) are taken from the first source.

    Args:
        sources: Input table(s) of the operation.
        out: Result table.
        rename: Old column name -> new column name, applied to the units.
        extra: Units of new columns, e.g. ``{"SHEAR": "N"}``.

    Returns:
        ``out`` (for chaining).

    Raises:
        TableError: If the sources use different unit systems or give the same column
            different units.
    """
    tables = [sources] if isinstance(sources, pd.DataFrame) else list(sources)
    system = common_unit_system_or_error(tables)
    units: dict[str, str] = {}
    for table in tables:
        for col, unit in get_units(table).items():
            col = (rename or {}).get(col, col)
            if units.get(col, unit) != unit:
                raise TableError(f"Column {col!r} has different units: {units[col]} and {unit}")
            units[col] = unit
    units.update(extra or {})
    attrs = dict(tables[0].attrs) if tables else {}
    out.attrs = attrs
    set_units(out, system, units)
    return out


def common_unit_system_or_error(tables: Sequence[pd.DataFrame]) -> UnitSystem | None:
    """Return the common unit system of tables, raising :class:`TableError` on a mix."""
    try:
        return common_unit_system(tables)
    except UnitError as exc:
        raise TableError(str(exc)) from exc


def unit_of(df: pd.DataFrame, column: str) -> str:
    """Return the unit label of a column, or ``""``."""
    return get_units(df).get(column, "")


__all__ = [
    "TableError",
    "carry_units",
    "common_unit_system_or_error",
    "get_unit_system",
    "require_columns",
    "unit_of",
]
