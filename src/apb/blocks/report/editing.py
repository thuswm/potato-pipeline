"""Editing support for the report sections (spec 05 §3.4): pure functions, no GUI.

The sections are handled as plain dicts, exactly as stored in the pipeline file. The
functions return new lists and never modify their input. :func:`check_sections` is the one
place that decides whether sections and connected items fit together; the GUI shows its
messages before a run and the block raises them when the run starts.
"""

from __future__ import annotations

import copy
import re
import string
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from pydantic import TypeAdapter, ValidationError

from apb.blocks.report.sections import (
    FormatError,
    Section,
    TableSection,
    fill_text,
    format_value,
)
from apb.engine.types import PortType
from apb.units import Quantity

#: Section kinds with their display names, in the order of the "Add section" menu.
SECTION_KINDS: tuple[tuple[str, str], ...] = (
    ("title", "Title"),
    ("heading", "Heading"),
    ("paragraph", "Paragraph"),
    ("value", "Value"),
    ("table", "Table"),
    ("figure", "Figure"),
    ("page_break", "Page break"),
    ("units", "Units statement"),
    ("traceability", "Traceability"),
)

#: Fields of each kind that may contain ``{placeholders}``.
TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "heading": ("text",),
    "paragraph": ("text",),
    "value": ("label",),
    "table": ("caption",),
    "figure": ("caption",),
}

#: Item types a section kind accepts (``Any`` fits everywhere).
ITEM_TYPES: dict[str, tuple[PortType, ...]] = {
    "table": (PortType.TABLE,),
    "figure": (PortType.FIGURE,),
    "value": (PortType.SCALAR, PortType.TEXT),
}

#: Names a placeholder may refer to besides the connected items.
METADATA_NAMES = ("title", "document_number", "revision", "author", "date")

_DEFAULTS: dict[str, dict[str, Any]] = {
    "heading": {"text": "New heading", "level": 1},
    "paragraph": {"text": ""},
    "value": {"item": ""},
    "table": {"item": ""},
    "figure": {"item": ""},
}

_ADAPTER: TypeAdapter[Section] = TypeAdapter(Section)


@dataclass(frozen=True)
class ItemInfo:
    """A value connected to the report's ``items`` input.

    Attributes:
        name: Name used by sections and ``{placeholders}``.
        type: Type of the value.
        source: Description of where it comes from, e.g. ``"Global loads (margins)"``.
        connection: ``(source block, source port, target block, target port)`` of the
            connector, or ``None`` if the item does not come from a connector.
        explicit: The name was set on the connector (not derived).
        columns: Column names of a table item, if the table is known (last run).
        value: The value of the last run, if there is one (live preview of texts).
    """

    name: str
    type: PortType
    source: str = ""
    connection: tuple[str, str, str, str] | None = None
    explicit: bool = False
    columns: tuple[str, ...] | None = None
    value: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Problem:
    """A problem found in the sections.

    Attributes:
        index: Position of the section (0-based), or ``-1`` for a problem of the items.
        message: What is wrong, in words for the user.
    """

    index: int
    message: str


# --------------------------------------------------------------------------------------
# Editing the list
# --------------------------------------------------------------------------------------


def new_section(kind: str) -> dict[str, Any]:
    """Return a new section of a kind, with the fields the user must still fill in.

    Raises:
        ValueError: For an unknown kind.
    """
    if kind not in dict(SECTION_KINDS):
        raise ValueError(f"Unknown section kind {kind!r}")
    return {"kind": kind, **copy.deepcopy(_DEFAULTS.get(kind, {}))}


def insert(
    sections: Sequence[Mapping[str, Any]], index: int, section: Mapping[str, Any]
) -> list[Any]:
    """Return the sections with ``section`` inserted at ``index`` (clamped to the list)."""
    out = [copy.deepcopy(dict(s)) for s in sections]
    out.insert(max(0, min(index, len(out))), copy.deepcopy(dict(section)))
    return out


def move(sections: Sequence[Mapping[str, Any]], index: int, delta: int) -> list[Any]:
    """Return the sections with the one at ``index`` moved by ``delta`` places.

    A move beyond either end of the list leaves the section at that end.
    """
    out = [copy.deepcopy(dict(s)) for s in sections]
    if not 0 <= index < len(out):
        raise IndexError(f"No section {index}")
    target = max(0, min(index + delta, len(out) - 1))
    out.insert(target, out.pop(index))
    return out


def duplicate(sections: Sequence[Mapping[str, Any]], index: int) -> list[Any]:
    """Return the sections with a copy of the one at ``index`` inserted right after it."""
    if not 0 <= index < len(sections):
        raise IndexError(f"No section {index}")
    return insert(sections, index + 1, sections[index])


def delete(sections: Sequence[Mapping[str, Any]], index: int) -> list[Any]:
    """Return the sections without the one at ``index``."""
    if not 0 <= index < len(sections):
        raise IndexError(f"No section {index}")
    return [copy.deepcopy(dict(s)) for i, s in enumerate(sections) if i != index]


def describe(section: Mapping[str, Any], limit: int = 70) -> str:
    """Return a one-line summary of a section for the list in the editor."""
    kind = str(section.get("kind", "?"))
    label = dict(SECTION_KINDS).get(kind, kind)
    item = str(section.get("item", ""))
    match kind:
        case "heading":
            text = f"H{section.get('level', 1)} · {section.get('text', '')}"
        case "paragraph":
            text = str(section.get("text", "")).replace("\n", " ") or "(empty)"
        case "value":
            text = " · ".join(
                p for p in (label, item or "(no item)", str(section.get("label", ""))) if p
            )
        case "table" | "figure":
            caption = f"“{section['caption']}”" if section.get("caption") else ""
            text = " · ".join(p for p in (label, item or "(no item)", caption) if p)
        case "traceability":
            text = str(section.get("heading", "")) or label
        case _:
            text = label
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------------------
# Placeholders and item references
# --------------------------------------------------------------------------------------


_ITEM_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def valid_item_name(name: str) -> bool:
    """Return whether ``name`` can name an item (letters, digits, ``_``; starts with a letter)."""
    return _ITEM_NAME.fullmatch(name) is not None


def placeholder_names(text: str) -> list[str]:
    """Return the names of the ``{placeholders}`` in a text (``[]`` for invalid syntax)."""
    try:
        return [name for _, name, _, _ in string.Formatter().parse(text) if name]
    except ValueError:
        return []


def _text_fields(section: Mapping[str, Any]) -> list[str]:
    return [
        str(section[f]) for f in TEXT_FIELDS.get(str(section.get("kind")), ()) if section.get(f)
    ]


def referenced_names(sections: Sequence[Mapping[str, Any]]) -> set[str]:
    """Return the names used by the sections, as ``item`` fields or ``{placeholders}``."""
    names: set[str] = set()
    for s in sections:
        if s.get("item"):
            names.add(str(s["item"]))
        for text in _text_fields(s):
            names.update(placeholder_names(text))
    return names


def count_references(sections: Sequence[Mapping[str, Any]], name: str) -> int:
    """Return how many ``item`` fields and placeholders refer to ``name``."""
    total = 0
    for s in sections:
        total += 1 if s.get("item") == name else 0
        total += sum(placeholder_names(text).count(name) for text in _text_fields(s))
    return total


def rename_items(sections: Sequence[Mapping[str, Any]], renames: Mapping[str, str]) -> list[Any]:
    """Return the sections with every reference to renamed items changed.

    All renames are applied at once, so items may swap names. References are the ``item``
    fields and ``{name}`` / ``{name:format}`` in texts; braces escaped as ``{{`` are left
    alone.

    Args:
        sections: The sections.
        renames: Old item name -> new item name.
    """
    out = [copy.deepcopy(dict(s)) for s in sections]
    if not renames:
        return out
    alternatives = "|".join(re.escape(n) for n in sorted(renames, key=len, reverse=True))
    pattern = re.compile(r"(?<!\{)\{(" + alternatives + r")(?=[:!}])")
    for s in out:
        if s.get("item") in renames:
            s["item"] = renames[s["item"]]
        for f in TEXT_FIELDS.get(str(s.get("kind")), ()):
            if isinstance(s.get(f), str):
                s[f] = pattern.sub(lambda m: "{" + renames[m.group(1)], s[f])
    return out


def rename_item_references(sections: Sequence[Mapping[str, Any]], old: str, new: str) -> list[Any]:
    """Return the sections with every reference to item ``old`` changed to ``new``."""
    return rename_items(sections, {old: new})


def fill_preview(
    text: str, items: Mapping[str, ItemInfo], metadata: Mapping[str, Any]
) -> str | None:
    """Fill a text with the values of the last run for the live preview.

    Returns:
        The filled text, or ``None`` if a value is not known yet or the text is invalid.
    """
    values: dict[str, Any] = dict(metadata)
    for name, info in items.items():
        if info.value is not None and isinstance(info.value, int | float | str | Quantity):
            values[name] = info.value
    try:
        return fill_text(text, values)
    except FormatError:
        return None


# --------------------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------------------


def item_info_from_value(name: str, value: Any, source: str = "") -> ItemInfo:
    """Describe a value that is already known (used when the report runs).

    Args:
        name: Item name.
        value: Table, scalar, text, figure or anything else.
        source: Optional description of the origin.
    """
    columns: tuple[str, ...] | None = None
    if isinstance(value, pd.DataFrame):
        kind = PortType.TABLE
        columns = table_columns(value)
    elif hasattr(value, "savefig"):
        kind = PortType.FIGURE
    elif isinstance(value, str):
        kind = PortType.TEXT
    elif isinstance(value, int | float | np.integer | np.floating | Quantity) and not isinstance(
        value, bool
    ):
        kind = PortType.SCALAR
    else:
        kind = PortType.ANY
    return ItemInfo(name, kind, source, columns=columns, value=value)


def table_columns(df: pd.DataFrame) -> tuple[str, ...]:
    """Return the column names of a table; the top level for two-level headers."""
    names = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return tuple(dict.fromkeys(str(n) for n in names))


def _validation_messages(exc: ValidationError) -> list[str]:
    out: list[str] = []
    for err in exc.errors():
        field_name = ".".join(str(p) for p in err["loc"][1:] if not isinstance(p, int))
        msg = str(err["msg"]).removeprefix("Value error, ")
        out.append(f"{field_name}: {msg}" if field_name else msg)
    return out


def check_sections(
    sections: Sequence[Any],
    items: Mapping[str, ItemInfo],
    metadata_names: Collection[str] = METADATA_NAMES,
) -> list[Problem]:
    """Check the sections against the connected items (REP-18).

    Args:
        sections: The sections, as dicts or section models.
        items: The connected items by name.
        metadata_names: Names besides the items that placeholders may use.

    Returns:
        All problems found, in section order; empty if everything fits. Problems of the
        items themselves (a name that clashes with the metadata) have index ``-1``.
    """
    problems = [
        Problem(-1, f"Item name {name!r} clashes with a report metadata name; rename the item")
        for name in items
        if name in metadata_names
    ]
    for index, raw in enumerate(sections):
        data = raw if isinstance(raw, Mapping) else raw.model_dump()
        try:
            section = _ADAPTER.validate_python(data)
        except ValidationError as exc:
            problems += [Problem(index, m) for m in _validation_messages(exc)]
            continue
        kind = section.kind
        name = getattr(section, "item", None)
        if name is not None:
            problems += [Problem(index, m) for m in _item_problems(kind, name, items)]
        if isinstance(section, TableSection) and section.item in items:
            problems += [Problem(index, m) for m in _column_problems(section, items[section.item])]
        for text in _text_fields(data):
            problems += [
                Problem(index, m) for m in _placeholder_problems(text, items, metadata_names)
            ]
    return problems


def _item_problems(kind: str, name: str, items: Mapping[str, ItemInfo]) -> list[str]:
    if not name:
        return ["Choose an item"]
    if name not in items:
        available = ", ".join(sorted(items)) or "none"
        return [f"No connected item {name!r}. Available: {available}"]
    accepted = ITEM_TYPES.get(kind, ())
    actual = items[name].type
    if actual is not PortType.ANY and actual not in accepted:
        wanted = " or ".join(t.value for t in accepted)
        return [f"Item {name!r} is a {actual.value}, not a {wanted}"]
    return []


def _column_problems(section: TableSection, info: ItemInfo) -> list[str]:
    if info.columns is None:
        return []
    known = set(info.columns)
    out = []
    missing = [c for c in section.columns if c not in known]
    if missing:
        out.append(
            f"Column(s) {missing} not in item {section.item!r}. "
            f"Available: {', '.join(info.columns)}"
        )
    stray = [c for c in section.formats if c not in known]
    if stray:
        out.append(f"Format given for column(s) {stray} that are not in item {section.item!r}")
    return out


def _placeholder_problems(
    text: str, items: Mapping[str, ItemInfo], metadata_names: Collection[str]
) -> list[str]:
    try:
        names = [n for _, n, _, _ in string.Formatter().parse(text) if n]
    except ValueError as exc:
        return [f"Cannot read the placeholders in {text!r}: {exc}"]
    out = []
    for name in dict.fromkeys(names):
        if name in items:
            if items[name].type not in (PortType.SCALAR, PortType.TEXT, PortType.ANY):
                out.append(f"Placeholder {{{name}}} is a {items[name].type.value}, not a value")
        elif name not in metadata_names:
            available = ", ".join(
                sorted(
                    [
                        n
                        for n, i in items.items()
                        if i.type is not PortType.TABLE and i.type is not PortType.FIGURE
                    ]
                    + list(metadata_names)
                )
            )
            out.append(f"Unknown placeholder {{{name}}}. Available: {available}")
    return out


# --------------------------------------------------------------------------------------
# Number formats per column (spec 05 §3.4, 2026-11-26)
# --------------------------------------------------------------------------------------

#: Help line shown above the per-column number formats.
FORMAT_HELP = (
    "How the numbers of each column are written. Excel style: 0.00 two decimals, "
    "0.0E+00 exponent, #,##0 thousands separator, 0.0% percent. "
    "Empty: the default format below."
)


def empty_format_text(column: pd.Series | None, default_format: str) -> str:
    """Return what applies to a column whose format field is empty (the placeholder).

    The writer uses the default format for decimal columns only; integer and text columns
    are written as they are.

    Args:
        column: The column of the last run, or ``None`` if not known.
        default_format: The section's default format (empty = ``auto``).

    Returns:
        ``"default: 0.00"``-style text, or ``"as is"``.
    """
    default = default_format.strip() or "auto"
    if column is not None and not pd.api.types.is_float_dtype(column):
        return "as is"
    return f"default: {default}"


def format_example(column: pd.Series | None, spec: str, default_format: str) -> str:
    """Return the first value of a column as the report will write it.

    Uses the same rule as the writer: the column's format if given, else the default
    format for decimal columns and ``auto`` for the others.

    Args:
        column: The column of the last run, or ``None`` if not known.
        spec: The column's format (empty = not set).
        default_format: The section's default format.

    Returns:
        ``"0.123456 → 0.12"``, or ``""`` if the column is unknown, empty, has no value,
        or the format is invalid.
    """
    if column is None:
        return ""
    values = column.dropna()
    if values.empty:
        return ""
    value = values.iloc[0]
    if isinstance(value, str):
        return ""
    if not spec.strip():
        spec = (default_format.strip() or "auto") if pd.api.types.is_float_dtype(column) else ""
    try:
        written = format_value(value, spec or "auto")
    except FormatError:
        return ""
    raw = repr(float(value)) if isinstance(value, float | np.floating) else str(value)
    return f"{raw} → {written}"
