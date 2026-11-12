"""Port types and ports: the typed values that flow on connectors (spec 00 §5)."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pandas as pd

from apb.units import Quantity


class PortType(StrEnum):
    """Type of the value carried by a port."""

    TABLE = "Table"
    SCALAR = "Scalar"
    TEXT = "Text"
    FIGURE = "Figure"
    ANY = "Any"


@dataclass(frozen=True)
class Port:
    """An input or output port of a block.

    Attributes:
        name: Identifier, unique among the block's inputs (or outputs).
        type: Type of the carried value.
        label: Display name in the GUI (defaults to ``name``).
        optional: Input may stay unconnected (the block then receives ``None``).
        multiple: Input accepts several connections and receives a list.
    """

    name: str
    type: PortType
    label: str = ""
    optional: bool = False
    multiple: bool = False

    @property
    def display_name(self) -> str:
        """Label shown in the GUI."""
        return self.label or self.name


def types_compatible(source: PortType, target: PortType) -> bool:
    """Return whether an output of type ``source`` may connect to an input of type ``target``."""
    return source is PortType.ANY or target is PortType.ANY or source is target


def value_matches(value: Any, port_type: PortType) -> bool:
    """Check a runtime value against a port type.

    Args:
        value: Value produced by a block.
        port_type: Declared type of the output port.

    Returns:
        ``True`` if the value is acceptable for the port type.
    """
    match port_type:
        case PortType.ANY:
            return True
        case PortType.TABLE:
            return isinstance(value, pd.DataFrame)
        case PortType.SCALAR:
            return isinstance(value, int | float | Quantity) and not isinstance(value, bool)
        case PortType.TEXT:
            return isinstance(value, str)
        case PortType.FIGURE:
            # Duck-typed so that matplotlib is not imported by the engine.
            return hasattr(value, "savefig")


def copy_value(value: Any) -> Any:
    """Return a copy of a value so that a block cannot modify another block's output.

    DataFrames are copied together with their ``attrs``; figures are passed as they are
    (they cannot be deep-copied reliably and blocks only read them).

    Args:
        value: Any port value.

    Returns:
        Independent copy (or the value itself for immutable values and figures).
    """
    if isinstance(value, pd.DataFrame):
        out = value.copy(deep=True)
        out.attrs = copy.deepcopy(value.attrs)
        return out
    if isinstance(value, list):
        return [copy_value(v) for v in value]
    if isinstance(value, str | int | float | bool | Quantity) or value is None:
        return value
    if hasattr(value, "savefig"):
        return value
    return copy.deepcopy(value)
