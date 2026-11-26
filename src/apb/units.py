"""Unit systems, unit labels and conversion between ``SI`` and ``SI_mm`` (spec 00 §5.1).

Only two consistent unit systems are supported:

* ``SI``    - m, N, Pa, kg, s
* ``SI_mm`` - mm, N, MPa, t (tonne), s

Every quantity kind has one unit label per system. A unit label identifies its kind and the
factor to SI, which is all that is needed to convert values. Tables carry their units in
``DataFrame.attrs``:

* ``attrs["unit_system"]`` - ``"SI"`` or ``"SI_mm"``
* ``attrs["units"]``       - mapping column name -> unit label, e.g. ``{"SX_Z1": "MPa"}``
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pandas as pd


class UnitSystem(StrEnum):
    """Supported consistent unit systems."""

    SI = "SI"
    SI_MM = "SI_mm"


class QuantityKind(StrEnum):
    """Physical kind of a value, used to derive its unit label."""

    LENGTH = "length"
    AREA = "area"
    FORCE = "force"
    MOMENT = "moment"
    FORCE_PER_LENGTH = "force_per_length"
    MOMENT_PER_LENGTH = "moment_per_length"
    STRESS = "stress"
    MASS = "mass"
    MASS_INERTIA = "mass_inertia"
    DENSITY = "density"
    ENERGY = "energy"
    ENERGY_DENSITY = "energy_density"
    VELOCITY = "velocity"
    ACCELERATION = "acceleration"
    ROTATION = "rotation"
    STRAIN = "strain"
    FREQUENCY = "frequency"
    ANGULAR_FREQUENCY = "angular_frequency"
    EIGENVALUE = "eigenvalue"
    DIMENSIONLESS = "dimensionless"


@dataclass(frozen=True)
class _UnitDef:
    """Unit labels of one quantity kind.

    Attributes:
        si: Label in the SI system.
        si_mm: Label in the SI_mm system.
        factor: Factor from SI to SI_mm (value_mm = value_si * factor).
    """

    si: str
    si_mm: str
    factor: float


_UNITS: dict[QuantityKind, _UnitDef] = {
    QuantityKind.LENGTH: _UnitDef("m", "mm", 1e3),
    QuantityKind.AREA: _UnitDef("m²", "mm²", 1e6),
    QuantityKind.FORCE: _UnitDef("N", "N", 1.0),
    QuantityKind.MOMENT: _UnitDef("N·m", "N·mm", 1e3),
    QuantityKind.FORCE_PER_LENGTH: _UnitDef("N/m", "N/mm", 1e-3),
    QuantityKind.MOMENT_PER_LENGTH: _UnitDef("N·m/m", "N·mm/mm", 1.0),
    QuantityKind.STRESS: _UnitDef("Pa", "MPa", 1e-6),
    QuantityKind.MASS: _UnitDef("kg", "t", 1e-3),
    QuantityKind.MASS_INERTIA: _UnitDef("kg·m²", "t·mm²", 1e3),
    QuantityKind.DENSITY: _UnitDef("kg/m³", "t/mm³", 1e-12),
    QuantityKind.ENERGY: _UnitDef("J", "mJ", 1e3),
    QuantityKind.ENERGY_DENSITY: _UnitDef("J/m³", "mJ/mm³", 1e-6),
    QuantityKind.VELOCITY: _UnitDef("m/s", "mm/s", 1e3),
    QuantityKind.ACCELERATION: _UnitDef("m/s²", "mm/s²", 1e3),
    QuantityKind.ROTATION: _UnitDef("rad", "rad", 1.0),
    QuantityKind.STRAIN: _UnitDef("-", "-", 1.0),
    QuantityKind.FREQUENCY: _UnitDef("Hz", "Hz", 1.0),
    QuantityKind.ANGULAR_FREQUENCY: _UnitDef("rad/s", "rad/s", 1.0),
    QuantityKind.EIGENVALUE: _UnitDef("(rad/s)²", "(rad/s)²", 1.0),
    QuantityKind.DIMENSIONLESS: _UnitDef("-", "-", 1.0),
}

#: Unit label -> (kind, factor from SI). Labels shared by both systems have factor 1.
_LABEL_TO_SI: dict[str, tuple[QuantityKind, float]] = {}
for _kind, _u in _UNITS.items():
    _LABEL_TO_SI.setdefault(_u.si, (_kind, 1.0))
    _LABEL_TO_SI.setdefault(_u.si_mm, (_kind, 1.0 / _u.factor))

ATTR_UNIT_SYSTEM = "unit_system"
ATTR_UNITS = "units"


class UnitError(ValueError):
    """Raised for unknown units or mixed unit systems."""


def unit_label(kind: QuantityKind, system: UnitSystem) -> str:
    """Return the unit label of a quantity kind in a unit system.

    Args:
        kind: Quantity kind.
        system: Unit system.

    Returns:
        Unit label, e.g. ``"MPa"``.
    """
    unit = _UNITS[kind]
    return unit.si if system is UnitSystem.SI else unit.si_mm


def known_units() -> list[str]:
    """Return all unit labels known to the conversion table."""
    return sorted(_LABEL_TO_SI)


def conversion_factor(unit: str, target: UnitSystem) -> tuple[float, str]:
    """Return the factor and new label to express a value given in ``unit`` in ``target``.

    Args:
        unit: Unit label of the value, e.g. ``"Pa"``.
        target: Target unit system.

    Returns:
        ``(factor, new_label)`` such that ``value_target = value * factor``.

    Raises:
        UnitError: If the unit label is unknown.
    """
    try:
        kind, to_si = _LABEL_TO_SI[unit]
    except KeyError:
        raise UnitError(f"Unknown unit {unit!r}; known units: {', '.join(known_units())}") from None
    from_si = 1.0 if target is UnitSystem.SI else _UNITS[kind].factor
    return to_si * from_si, unit_label(kind, target)


@dataclass(frozen=True)
class Quantity:
    """A scalar value with a unit label.

    Attributes:
        value: Numerical value.
        unit: Unit label (empty string = unitless).
    """

    value: float
    unit: str = ""

    def __format__(self, spec: str) -> str:
        """Format the value with ``spec`` and append the unit, e.g. ``410.0 MPa``."""
        text = format(self.value, spec)
        return f"{text} {self.unit}" if self.unit and self.unit != "-" else text

    def __float__(self) -> float:
        return float(self.value)

    def to(self, target: UnitSystem) -> Quantity:
        """Convert to another unit system.

        Args:
            target: Target unit system.

        Returns:
            New quantity; unitless quantities are returned unchanged.
        """
        if not self.unit:
            return self
        factor, label = conversion_factor(self.unit, target)
        return Quantity(self.value * factor, label)


# --------------------------------------------------------------------------------------
# DataFrame helpers
# --------------------------------------------------------------------------------------


def set_units(df: pd.DataFrame, system: UnitSystem | None, units: Mapping[str, str]) -> None:
    """Store unit system and column units in ``df.attrs`` (in place).

    Args:
        df: Table to annotate.
        system: Unit system of the table, or ``None`` if unknown.
        units: Column name -> unit label; columns not present in ``df`` are ignored.
    """
    if system is not None:
        df.attrs[ATTR_UNIT_SYSTEM] = str(system)
    df.attrs[ATTR_UNITS] = {col: u for col, u in units.items() if col in df.columns}


def get_unit_system(df: pd.DataFrame) -> UnitSystem | None:
    """Return the unit system stored on a table, or ``None`` if not set."""
    value = df.attrs.get(ATTR_UNIT_SYSTEM)
    return UnitSystem(value) if value else None


def get_units(df: pd.DataFrame) -> dict[str, str]:
    """Return the column units stored on a table (column -> unit label)."""
    units: Any = df.attrs.get(ATTR_UNITS, {})
    return dict(units)


def common_unit_system(tables: Iterable[pd.DataFrame]) -> UnitSystem | None:
    """Return the unit system shared by all tables that declare one.

    Args:
        tables: Tables to check. Tables without a unit system are ignored.

    Returns:
        The common unit system, or ``None`` if no table declares one.

    Raises:
        UnitError: If tables declare different unit systems.
    """
    systems = {s for s in (get_unit_system(t) for t in tables) if s is not None}
    if len(systems) > 1:
        names = " and ".join(sorted(str(s) for s in systems))
        raise UnitError(
            f"Tables use different unit systems ({names}). Add a 'Convert units' block first."
        )
    return systems.pop() if systems else None


def convert_table(df: pd.DataFrame, target: UnitSystem) -> pd.DataFrame:
    """Convert all columns with a known unit to another unit system.

    Columns without unit are unchanged. The input table is not modified. For two-level
    headers the units apply to all sub-columns of a top-level column except ``*_CASE``.

    Args:
        df: Table with units metadata.
        target: Target unit system.

    Returns:
        Converted copy with updated ``attrs``.

    Raises:
        UnitError: If a column has an unknown unit label.
    """
    out = df.copy()
    new_units: dict[str, str] = {}
    multi = isinstance(df.columns, pd.MultiIndex)
    for col, unit in get_units(df).items():
        if col not in out.columns:
            continue
        factor, label = conversion_factor(unit, target)
        new_units[col] = label
        if factor == 1.0:
            continue
        if multi:  # units are keyed by the top level; *_CASE columns hold case names
            for full in df.columns:
                if full[0] == col and not str(full[-1]).endswith("_CASE"):
                    out[full] = out[full] * factor
        else:
            out[col] = out[col] * factor
    out.attrs = dict(df.attrs)
    set_units(out, target, new_units)
    return out


def header_with_unit(column: str, units: Mapping[str, str]) -> str:
    """Return a column header with unit, e.g. ``"SX_Z1 [MPa]"`` (used by reports)."""
    unit = units.get(column, "")
    return f"{column} [{unit}]" if unit and unit != "-" else column


#: Output labels for mass in kg (spec 00 §5.1): unit in the table -> (label, factor).
MASS_IN_KG: dict[str, tuple[str, float]] = {
    "t": ("kg", 1e3),
    "t·mm²": ("kg·mm²", 1e3),
}

#: Tooltip for the tonne in previews (the pipeline keeps the consistent ``SI_mm`` unit).
TONNE_HINT = "t = tonne = 1000 kg (consistent with mm, N, s)"


def mass_in_kg(df: pd.DataFrame) -> pd.DataFrame:
    """Return the table with mass columns in kg instead of tonne, for output only.

    The ``SI_mm`` system needs the tonne to stay consistent, so tables keep it inside the
    pipeline; the writers (Word, Excel, CSV) show kg (spec 00 §5.1, 2026-11-26). Columns
    in ``t`` become ``kg`` and ``t·mm²`` become ``kg·mm²`` (values x 1000). The result is
    not a consistent table and must not be fed back into computations.

    Args:
        df: Table with units metadata.

    Returns:
        A converted copy, or ``df`` itself if it has no mass columns.
    """
    units = get_units(df)
    changes = {c: MASS_IN_KG[u] for c, u in units.items() if u in MASS_IN_KG and c in df.columns}
    if not changes:
        return df
    out = df.copy()
    multi = isinstance(df.columns, pd.MultiIndex)
    for col, (label, factor) in changes.items():
        if multi:  # units are keyed by the top level; *_CASE columns hold case names
            for full in df.columns:
                if full[0] == col and not str(full[-1]).endswith("_CASE"):
                    out[full] = out[full] * factor
        else:
            out[col] = out[col] * factor
        units[col] = label
    out.attrs = dict(df.attrs)
    out.attrs[ATTR_UNITS] = units
    return out
