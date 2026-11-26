"""Report sections (generated mode, spec 05 §3.2) and number/text formatting.

Each section is a small Pydantic model with a ``kind`` discriminator, so a report is
stored in the pipeline file as an ordered JSON list, e.g.::

    [{"kind": "heading", "text": "1 Introduction"},
     {"kind": "table", "item": "margins", "formats": {"MS": "0.00"}, "highlight": "MS < 0"}]
"""

from __future__ import annotations

import math
import re
import string
from collections.abc import Mapping
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

_EXCEL_NUMBER = re.compile(r"^(#,##)?0(\.(0+))?(([Ee])\+0+)?(%)?$")


class FormatError(ValueError):
    """Raised for invalid number formats or text placeholders."""


def python_format(spec: str) -> str:
    """Convert a number format to a Python format spec.

    Accepts Excel-style formats (``0``, ``0.00``, ``#,##0.0``, ``0.0E+00``, ``0.0%``),
    Python format specs (``.2f``, ``.3e``, ``,.1f``) and ``auto`` (4 significant digits).

    Args:
        spec: Number format.

    Returns:
        Python format spec.

    Raises:
        FormatError: If the format is not valid.
    """
    spec = spec.strip()
    if spec in ("", "auto"):
        return ".4g"
    m = _EXCEL_NUMBER.match(spec)
    if m:
        decimals = len(m.group(3) or "")
        grouping = "," if m.group(1) else ""
        if m.group(4):
            return f"{grouping}.{decimals}{m.group(5)}"
        return f"{grouping}.{decimals}{'%' if m.group(6) else 'f'}"
    try:
        format(1.5, spec)
    except ValueError as exc:
        raise FormatError(f"Invalid number format {spec!r}") from exc
    return spec


def format_value(value: Any, spec: str = "auto") -> str:
    """Format one table cell for the report.

    Floats use ``spec``; integers are printed as they are (unless ``spec`` is given
    explicitly); missing values become an empty string.

    Args:
        value: Cell value.
        spec: Number format (see :func:`python_format`).

    Returns:
        Text for the cell.
    """
    if value is None or (isinstance(value, float | np.floating) and math.isnan(value)):
        return ""
    if isinstance(value, bool | np.bool_):
        return "yes" if value else "no"
    if isinstance(value, int | np.integer):
        return str(value) if spec in ("", "auto") else format(int(value), python_format(spec))
    if isinstance(value, float | np.floating):
        return format(float(value), python_format(spec))
    return str(value)


class _SafeFormatter(string.Formatter):
    """``str.format`` restricted to plain names (no attribute access or indexing)."""

    def get_field(self, field_name: str, args: Any, kwargs: Any) -> Any:
        if not field_name.isidentifier():
            raise FormatError(
                f"Placeholder {{{field_name}}} is not allowed: use plain names like {{min_ms}}"
            )
        if field_name not in kwargs:
            available = ", ".join(sorted(kwargs)) or "none"
            raise FormatError(f"Unknown placeholder {{{field_name}}}. Available: {available}")
        return kwargs[field_name], field_name


_FORMATTER = _SafeFormatter()


def fill_text(text: str, values: Mapping[str, Any]) -> str:
    """Fill ``{name}`` / ``{name:spec}`` placeholders (Python format syntax).

    Scalars with unit print their unit (``{allowable:.1f}`` -> ``410.0 MPa``). ``{{`` and
    ``}}`` produce literal braces.

    Raises:
        FormatError: For unknown names, attribute access/indexing or bad format specs.
    """
    try:
        return _FORMATTER.vformat(text, (), dict(values))
    except FormatError:
        raise
    except (ValueError, IndexError, KeyError) as exc:
        raise FormatError(f"Cannot fill text {text!r}: {exc}") from exc


# --------------------------------------------------------------------------------------
# Section models
# --------------------------------------------------------------------------------------


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TitleSection(_Section):
    """Title block: title, document number, revision, author, date (from metadata)."""

    kind: Literal["title"] = "title"


class HeadingSection(_Section):
    """A heading (Word styles Heading 1-3)."""

    kind: Literal["heading"] = "heading"
    text: str
    level: int = Field(default=1, ge=1, le=3)


class ParagraphSection(_Section):
    """A paragraph of text with ``{placeholders}``."""

    kind: Literal["paragraph"] = "paragraph"
    text: str


class ValueSection(_Section):
    """One value on its own line: ``<label>: <value with unit>``."""

    kind: Literal["value"] = "value"
    item: str
    label: str = ""
    format: str = "auto"

    @field_validator("format")
    @classmethod
    def _valid_format(cls, value: str) -> str:
        python_format(value)
        return value


class TableSection(_Section):
    """A table from a connected table item."""

    kind: Literal["table"] = "table"
    item: str
    caption: str = ""
    columns: list[str] = Field(default_factory=list, description="Empty = all")
    formats: dict[str, str] = Field(default_factory=dict, description="Column -> format")
    default_format: str = "auto"
    max_rows: int | None = Field(default=None, ge=1)
    highlight: str = Field(default="", description="Rows to highlight, e.g. MS < 0")
    units_in_header: bool = True
    mass_in_kg: bool = Field(default=True, description="Mass in kg instead of t (spec 00 §5.1)")

    @field_validator("default_format")
    @classmethod
    def _valid_default(cls, value: str) -> str:
        python_format(value)
        return value

    @field_validator("formats")
    @classmethod
    def _valid_formats(cls, value: dict[str, str]) -> dict[str, str]:
        for spec in value.values():
            python_format(spec)
        return value


class FigureSection(_Section):
    """A figure from a connected figure item."""

    kind: Literal["figure"] = "figure"
    item: str
    caption: str = ""
    width_cm: float = Field(default=15.0, gt=0, le=30)
    dpi: int = Field(default=200, ge=50, le=600)


class PageBreakSection(_Section):
    """Start a new page."""

    kind: Literal["page_break"] = "page_break"


class UnitsSection(_Section):
    """Statement of the unit system(s) used (REP-13)."""

    kind: Literal["units"] = "units"


class TraceabilitySection(_Section):
    """List of data sources, Nastran version and tool version (REP-12)."""

    kind: Literal["traceability"] = "traceability"
    heading: str = "Traceability"


Section = Annotated[
    TitleSection
    | HeadingSection
    | ParagraphSection
    | ValueSection
    | TableSection
    | FigureSection
    | PageBreakSection
    | UnitsSection
    | TraceabilitySection,
    Field(discriminator="kind"),
]
