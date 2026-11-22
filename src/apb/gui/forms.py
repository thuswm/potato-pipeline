"""Parameter forms generated from a block's Pydantic ``Params`` model (GUI-06).

The form edits the parameters *as stored* in the pipeline file, so ``${variable}``
references are kept. Validation happens in the engine (the panel shows its messages).

Field type -> widget:

* ``str``                      text (``apb_widget: file`` -> text + file picker,
                               ``apb_widget: code`` -> code editor,
                               ``apb_widget: unit`` -> unit drop-down)
* ``apb_columns: <port>``      column names of the table on that input: drop-down
                               (``str``) or multi-select (``list[str]``) when known
* ``int`` / ``float``          text accepting a number or ``${variable}``
* ``bool``                     switch
* ``Literal`` / ``Enum``       drop-down (optional fields add "(default)";
                               ``apb_labels`` gives display labels)
* ``list[str | int | float]``  comma-separated text
* ``list[Literal]``            multi-select drop-down
* ``dict[str | int, str]``     key/value rows
* ``list[PortSpec]``           port rows (Python block)
* nested ``BaseModel``         group of sub-fields
* anything else                JSON editor (e.g. report sections until their own editor)
"""

from __future__ import annotations

import contextlib
import json
import re
import types
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from nicegui import ui
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from apb.blocks.python.scripting import PortSpec
from apb.engine.types import PortType
from apb.gui.editor import UNSET
from apb.units import known_units

_VARIABLE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
_EXAMPLE = re.compile(r"\be\.g\.\s*(.+?)\s*(?:; | — |$)")

#: Writes a stored value (or ``UNSET`` to fall back to the default).
type Setter = Callable[[Any], None]

#: Hint added to a column field whose upstream columns are not known (PNL-11).
RUN_UPSTREAM = "Run the upstream block to choose from its columns"


def unit_options(current: str = "", other: bool = False) -> dict[str, str]:
    """Return the choices of a unit drop-down: ``""`` = "(none)", then the known units.

    Args:
        current: The stored value; kept as a choice when it is not a known unit.
        other: The field accepts labels outside the list (shown without a remark).
    """
    options = {"": "(none)", **{u: u for u in known_units() if u != "-"}}
    if current and current not in options:
        options[current] = current if other else f"{current} (unknown unit)"
    return options


def humanize(name: str) -> str:
    """``default_unit_system`` -> ``Default unit system``."""
    return name.replace("_", " ").capitalize()


def short_path(path: str | Path, base: Path | None = None, limit: int = 44) -> str:
    """Return a path for display: relative to ``base`` if inside it, shortened in the middle.

    Args:
        path: The path.
        base: Folder of the pipeline (paths inside it are shown relative to it).
        limit: Longest text returned; longer paths keep their start and end (PNL-08).
    """
    p = Path(path)
    text = str(p)
    if base is not None:
        with contextlib.suppress(ValueError, OSError):  # outside the pipeline folder
            text = p.resolve().relative_to(base.resolve()).as_posix()
    if len(text) <= limit:
        return text
    name = p.name
    if len(name) + 2 >= limit:
        return "…" + text[-(limit - 1) :]
    return text[: limit - len(name) - 2] + "…/" + name


def field_label(name: str, field: FieldInfo) -> str:
    """Return the label of a parameter: its ``title``, else the humanized name (PNL-09)."""
    return field.title or humanize(name)


def example_of(hint: str) -> str:
    """Return the example part of a help text (``"e.g. EID"``), or ``""`` (PNL-04)."""
    m = _EXAMPLE.search(hint)
    if not m:
        return ""
    text = m.group(1)
    while text.endswith(")") and text.count(")") > text.count("("):
        text = text[:-1]  # the example sat inside brackets: "(e.g. 9058)"
    return f"e.g. {text.strip(chr(39))}"


def describe(widget: Any, hint: str, placeholder: bool = True) -> None:
    """Attach a help text to a field: an ⓘ icon with tooltip, the example as placeholder.

    Args:
        widget: A ``ui.input`` or ``ui.select`` (anything with an ``append`` slot).
        hint: Help text (nothing happens if empty).
        placeholder: Also show the example of the text in the empty field.
    """
    if not hint:
        return
    with widget.add_slot("append"):
        ui.icon("info_outline", size="xs").classes("text-slate-400 cursor-help apb-info").tooltip(
            hint
        )
    example = example_of(hint) if placeholder else ""
    if example:
        widget.props(f"placeholder={json.dumps(example)}")


def connected_line(label: str, block: str, detail: str = "", tip: str = "") -> None:
    """Show that a connector delivers a parameter's value, instead of its field (PNL-07).

    Args:
        label: The parameter, e.g. ``"Result file"``.
        block: Name of the block the connector comes from.
        detail: The value if known (e.g. a short path), else "not run yet" is shown.
        tip: Tooltip, e.g. the full path.
    """
    with ui.row().classes("w-full items-start no-wrap gap-1 apb-connected"):
        ui.icon("link", size="xs").classes("text-blue-600 mt-0.5")
        with ui.column().classes("gap-0 min-w-0"):
            ui.label(f"{label} from block '{block}'").classes("text-xs text-slate-700")
            shown = ui.label(detail or "(that block has not run yet)").classes(
                "text-[11px] " + ("text-slate-500" if detail else "text-amber-700")
            )
            if tip:
                shown.tooltip(tip)


def default_of(field: FieldInfo) -> Any:
    """Return a field's default in stored (JSON) form, or ``None`` if it is required."""
    if field.is_required():
        return None
    value = field.get_default(call_default_factory=True)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [v.model_dump(mode="json") if isinstance(v, BaseModel) else v for v in value]
    return value


def classify(annotation: Any) -> tuple[str, Any, bool]:
    """Map a type annotation to ``(widget kind, detail, optional)``."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType) and type(None) in args:
        inner = [a for a in args if a is not type(None)]
        if len(inner) == 1:
            kind, detail, _ = classify(inner[0])
            return kind, detail, True
        return "json", None, True
    if annotation is bool:
        return "bool", None, False
    if annotation in (int, float):
        return "number", annotation, False
    if annotation is str:
        return "text", None, False
    if origin is Literal:
        return "choice", list(args), False
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return "choice", [m.value for m in annotation], False
    if origin is list and args:
        inner = args[0]
        if inner in (str, int, float):
            return "list", inner, False
        if get_origin(inner) is Literal:
            return "multichoice", list(get_args(inner)), False
        if inner is PortSpec:
            return "ports", None, False
        return "json", None, False
    if origin is dict and len(args) == 2 and args[0] in (str, int) and args[1] is str:
        return "mapping", args[0], False
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return "model", annotation, False
    return "json", None, False


class ParamForm:
    """Builds the widgets for one parameter model.

    Args:
        model: The block's ``Params`` class.
        values: Stored parameters of the block.
        on_change: Called with ``(name, value)``; value ``UNSET`` removes the parameter.
        pick_file: Async callback asking the user for a file; returns a stored path or None.
        choices: Returns the values a list parameter can take (e.g. the subcases in the
            file), or ``None``; a parameter with choices gets a multi-select (PNL-11).
        replaced: Parameter -> ``(block, value, tooltip)`` for parameters delivered by a
            connected input; they are shown as a "from block" line (PNL-07).
        columns: Returns the column names of the table on an input port (the
            ``apb_columns`` mark of a field, ``"left,right"`` = columns in both), or
            ``None`` when not known; known columns give a drop-down (PNL-11).
    """

    def __init__(
        self,
        model: type[BaseModel],
        values: dict[str, Any],
        on_change: Callable[[str, Any], None],
        pick_file: Callable[[str], Any] | None = None,
        *,
        choices: Callable[[str], list[Any] | None] | None = None,
        replaced: Mapping[str, tuple[str, str, str]] | None = None,
        columns: Callable[[str], list[str] | None] | None = None,
    ) -> None:
        self.model = model
        self.values = values
        self.on_change = on_change
        self.pick_file = pick_file
        self.choices = choices
        self.replaced = dict(replaced or {})
        self.columns = columns

    def build(self, advanced: Sequence[str] = ()) -> None:
        """Create the widgets in the current NiceGUI context.

        Args:
            advanced: Parameters shown in a folded *Advanced* section (PNL-03).
        """
        fields = self.model.model_fields
        for name, field in fields.items():
            if name not in advanced:
                self._field(
                    name, field, self.values.get(name, default_of(field)), self._setter(name)
                )
        rare = [n for n in advanced if n in fields]
        if rare:
            self.advanced(rare)

    def changed(self, names: Sequence[str]) -> int:
        """Return how many of the parameters differ from their defaults."""
        fields = self.model.model_fields
        return sum(1 for n in names if n in self.values and self.values[n] != default_of(fields[n]))

    def advanced(self, names: Sequence[str]) -> None:
        """Create a folded *Advanced* section with the given parameters (PNL-03)."""
        count = self.changed(names)
        title = "Advanced" + (f" · {count} changed" if count else "")
        with (
            ui.expansion(title, value=False)
            .classes("w-full apb-advanced")
            .props("dense header-class='apb-section-title px-0'")
        ):
            for name in names:
                self.field(name)

    def field(self, name: str) -> None:
        """Create the widget of one parameter (for panels that mix custom and standard fields).

        Args:
            name: Field name in the ``Params`` model.
        """
        info = self.model.model_fields[name]
        self._field(name, info, self.values.get(name, default_of(info)), self._setter(name))

    def _setter(self, name: str) -> Setter:
        def set_value(value: Any) -> None:
            self.on_change(name, value)

        return set_value

    # ---- one field ----------------------------------------------------------------------

    def _field(self, name: str, field: FieldInfo, value: Any, setter: Setter) -> None:
        kind, detail, optional = classify(field.annotation)
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        widget = extra.get("apb_widget")
        label = field_label(name, field) + ("" if not field.is_required() else " *")
        hint = field.description or ""
        if name in self.replaced:
            block, shown, tip = self.replaced[name]
            connected_line(field_label(name, field), block, shown, tip)
            return
        options = self.choices(name) if self.choices and kind == "list" else None
        if options is not None and not isinstance(value, str):
            self._choose_many(label, hint, value, setter, options)
            return
        port = extra.get("apb_columns")
        if isinstance(port, str) and kind in {"text", "list"}:
            known = self.columns(port) if self.columns else None
            variable = isinstance(value, str) and bool(_VARIABLE.match(value.strip()))
            if known is not None and not variable:
                self._choose_columns(label, hint, value, setter, known, many=kind == "list")
                return
            if known is None:
                hint = (hint + " — " if hint else "") + RUN_UPSTREAM
        if kind == "text" and widget == "unit":
            self._unit(label, hint, value, setter, other=bool(extra.get("apb_other")))
        elif kind == "text" and widget == "file":
            self._file(label, hint, value, setter)
        elif kind == "text" and widget == "code":
            self._code(label, hint, value, setter)
        elif kind == "text":
            self._text(label, hint, value, setter)
        elif kind == "number":
            self._number(label, hint, value, setter, detail, optional)
        elif kind == "bool":
            switch = ui.switch(label, value=bool(value)).props("dense")
            switch.on_value_change(lambda e: setter(bool(e.value)))
            if hint:
                switch.tooltip(hint)
        elif kind == "choice":
            labels = extra.get("apb_labels")
            self._choice(
                label,
                hint,
                value,
                setter,
                detail,
                optional,
                labels=labels if isinstance(labels, dict) else None,
            )
        elif kind == "multichoice":
            sel = (
                ui.select(detail, value=list(value or []), label=label, multiple=True)
                .props("dense outlined use-chips")
                .classes("w-full")
            )
            sel.on_value_change(lambda e: setter(list(e.value or [])))
            self._hint(sel, hint)
        elif kind == "list":
            self._list(label, hint, value, setter, detail)
        elif kind == "mapping":
            self._mapping(label, hint, value, setter, detail)
        elif kind == "ports":
            self._ports(label, value, setter)
        elif kind == "model":
            self._model(label, detail, value, setter)
        else:
            self._json(label, hint, value, setter)

    @staticmethod
    def _hint(widget: Any, hint: str) -> None:
        describe(widget, hint)

    def _text(self, label: str, hint: str, value: Any, setter: Setter) -> None:
        box = (
            ui.input(label, value="" if value is None else str(value))
            .props("dense outlined")
            .classes("w-full")
        )
        self._hint(box, hint)
        box.on("blur", lambda: setter(box.value))
        box.on("keydown.enter", lambda: setter(box.value))

    def _file(self, label: str, hint: str, value: Any, setter: Setter) -> None:
        with ui.row().classes("w-full no-wrap items-start gap-1"):
            box = (
                ui.input(label, value="" if value is None else str(value))
                .props("dense outlined")
                .classes("grow")
            )
            self._hint(box, hint)
            box.on("blur", lambda: setter(box.value))
            box.on("keydown.enter", lambda: setter(box.value))

            async def browse() -> None:
                if self.pick_file is None:
                    return
                chosen = await self.pick_file(f"{label} {hint}")
                if chosen:
                    box.value = chosen
                    setter(chosen)

            ui.button(icon="folder_open", on_click=browse).props("flat dense").tooltip(
                "Choose file"
            )

    def _code(self, label: str, hint: str, value: Any, setter: Setter) -> None:
        # Folded away when empty (e.g. inline code of a Python block that uses a script file).
        with (
            ui.expansion(label, value=bool(value))
            .classes("w-full")
            .props("dense header-class='text-xs text-slate-600'")
        ):
            editor = ui.codemirror(value or "", language="Python").classes(
                "w-full h-48 border rounded"
            )
            editor.on("blur", lambda: setter(editor.value))
            if hint:
                editor.tooltip(hint)

    def _number(
        self, label: str, hint: str, value: Any, setter: Setter, kind: type, optional: bool
    ) -> None:
        box = (
            ui.input(label, value="" if value is None else str(value))
            .props("dense outlined")
            .classes("w-full")
        )
        self._hint(box, (hint + " — " if hint else "") + "a number or ${variable}")

        def commit() -> None:
            text = (box.value or "").strip()
            if text == "":
                setter(None if optional else UNSET)
                box.props(remove="error")
                return
            if _VARIABLE.match(text):
                setter(text)
                box.props(remove="error")
                return
            try:
                number = int(text) if kind is int else float(text)
            except ValueError:
                box.props('error error-message="Enter a number or ${variable}"')
                return
            box.props(remove="error")
            setter(number)

        box.on("blur", commit)
        box.on("keydown.enter", commit)

    def _choice(
        self,
        label: str,
        hint: str,
        value: Any,
        setter: Setter,
        options: list[Any],
        optional: bool,
        labels: Mapping[Any, Any] | None = None,
    ) -> None:
        mapping: dict[Any, str] = {o: str((labels or {}).get(o, o)) for o in options}
        if optional:
            mapping = {"__none__": "(pipeline default)", **mapping}
        current = "__none__" if optional and value is None else value
        sel = (
            ui.select(mapping, value=current if current in mapping else None, label=label)
            .props("dense outlined")
            .classes("w-full")
        )
        sel.on_value_change(lambda e: setter(None if e.value == "__none__" else e.value))
        self._hint(sel, hint)

    def _choose_many(
        self, label: str, hint: str, value: Any, setter: Setter, options: list[Any]
    ) -> None:
        """Multi-select of the values present (e.g. subcases in the file), PNL-11."""
        current = list(value or [])
        mapping: dict[Any, str] = {o: str(o) for o in options}
        mapping.update({v: f"{v} (not in file)" for v in current if v not in mapping})
        sel = (
            ui.select(mapping, value=current, label=label, multiple=True)
            .props("dense outlined use-chips clearable")
            .classes("w-full apb-choices")
        )
        sel.on_value_change(lambda e: setter(list(e.value or [])))
        describe(sel, hint, placeholder=False)

    def _choose_columns(
        self, label: str, hint: str, value: Any, setter: Setter, known: list[str], many: bool
    ) -> None:
        """Drop-down / multi-select of the upstream columns; typed names allowed (PNL-11)."""
        stored = [str(v) for v in value] if isinstance(value, list) else []
        current: Any = stored if many else ("" if value is None else str(value))
        mapping: dict[str, str] = {c: c for c in known}
        for name in stored if many else [current]:
            if name and name not in mapping:
                mapping[name] = f"{name} (not in table)"
        sel = (
            ui.select(
                mapping,
                value=current if many or current else None,
                label=label,
                multiple=many,
                with_input=True,
                new_value_mode="add-unique",
            )
            .props("dense outlined" + (" use-chips clearable" if many else ""))
            .classes("w-full apb-column-choice" + (" apb-choices" if many else ""))
        )
        if many:
            sel.on_value_change(lambda e: setter([str(v) for v in e.value or []]))
        else:
            sel.on_value_change(lambda e: setter("" if e.value is None else str(e.value)))
        describe(sel, hint, placeholder=False)

    def _unit(self, label: str, hint: str, value: Any, setter: Setter, other: bool) -> None:
        """Unit drop-down with "(none)"; ``other`` also accepts a typed label (PNL-11)."""
        current = "" if value is None else str(value)
        options = unit_options(current, other)
        extra: dict[str, Any] = (
            {"with_input": True, "new_value_mode": "add-unique"} if other else {}
        )
        sel = (
            ui.select(options, value=current, label=label, **extra)
            .props("dense outlined")
            .classes("w-full apb-unit")
        )
        sel.on_value_change(lambda e: setter("" if e.value is None else str(e.value)))
        describe(sel, hint, placeholder=False)

    def _list(self, label: str, hint: str, value: Any, setter: Setter, item: type) -> None:
        text = ", ".join(str(v) for v in value) if isinstance(value, list) else (value or "")
        box = ui.input(label, value=text).props("dense outlined").classes("w-full")
        self._hint(box, (hint + " — " if hint else "") + "comma-separated")

        def commit() -> None:
            raw = (box.value or "").strip()
            if _VARIABLE.match(raw):
                setter(raw)
                return
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            try:
                values: list[Any] = [item(p) if item is not str else p for p in parts]
            except ValueError:
                box.props(f'error error-message="Comma-separated {item.__name__} values"')
                return
            box.props(remove="error")
            setter(values)

        box.on("blur", commit)
        box.on("keydown.enter", commit)

    def _mapping(self, label: str, hint: str, value: Any, setter: Setter, key_type: type) -> None:
        rows: list[list[str]] = [[str(k), str(v)] for k, v in (value or {}).items()]
        with ui.row().classes("items-center gap-1 mt-1"):
            ui.label(label).classes("text-xs text-slate-600")
            if hint:
                ui.icon("info_outline", size="xs").classes(
                    "text-slate-400 cursor-help apb-info"
                ).tooltip(hint)
        container = ui.column().classes("w-full gap-1")

        def commit() -> None:
            result: dict[Any, str] = {}
            for k, v in rows:
                if not k.strip():
                    continue
                try:
                    result[int(k) if key_type is int else k.strip()] = v
                except ValueError:
                    ui.notify(f"'{k}' is not a whole number", type="warning")
                    return
            setter(result)

        def update(row: list[str], index: int, widget: ui.input) -> None:
            row[index] = widget.value or ""
            commit()

        def remove(row: list[str]) -> None:
            rows.remove(row)
            commit()
            render()

        def add() -> None:
            rows.append(["", ""])
            render()

        def render() -> None:
            container.clear()
            with container:
                for row in rows:
                    with ui.row().classes("w-full no-wrap gap-1 items-center"):
                        key = ui.input(value=row[0]).props("dense outlined").classes("w-1/3")
                        val = ui.input(value=row[1]).props("dense outlined").classes("grow")
                        key.on("blur", lambda _e, r=row, w=key: update(r, 0, w))
                        val.on("blur", lambda _e, r=row, w=val: update(r, 1, w))
                        ui.button(icon="close", on_click=lambda r=row: remove(r)).props(
                            "flat dense size=sm"
                        )
                ui.button("Add", icon="add", on_click=add).props("flat dense")

        render()

    def _ports(self, label: str, value: Any, setter: Setter) -> None:
        rows: list[dict[str, Any]] = [dict(p) for p in (value or [])]
        ui.label(label).classes("text-xs font-medium text-slate-600 mt-2")
        container = ui.column().classes("w-full gap-1")
        types_ = [t.value for t in PortType]

        def commit() -> None:
            names = [r.get("name", "") for r in rows]
            bad = [n for n in names if not n.isidentifier()]
            if bad:
                ui.notify(f"Port names must be valid Python names: {bad}", type="warning")
                return
            setter(
                [{k: v for k, v in r.items() if not (k == "optional" and v is False)} for r in rows]
            )

        def update(row: dict[str, Any], key: str, value: Any) -> None:
            row[key] = value.strip() if isinstance(value, str) else value
            commit()

        def remove(row: dict[str, Any]) -> None:
            rows.remove(row)
            commit()
            render()

        def render() -> None:
            container.clear()
            with container:
                for row in rows:
                    with ui.row().classes("w-full no-wrap gap-1 items-center"):
                        name = (
                            ui.input(value=row.get("name", ""))
                            .props("dense outlined placeholder=name")
                            .classes("w-2/5")
                        )
                        name.on("blur", lambda _e, r=row, w=name: update(r, "name", w.value or ""))
                        typ = (
                            ui.select(types_, value=row.get("type", "Any"))
                            .props("dense outlined")
                            .classes("w-1/3")
                        )
                        typ.on_value_change(lambda e, r=row: update(r, "type", e.value))
                        ui.button(icon="close", on_click=lambda r=row: remove(r)).props(
                            "flat dense size=sm"
                        )

                def add() -> None:
                    n = len(rows) + 1
                    while f"value{n}" in {r.get("name") for r in rows}:
                        n += 1
                    rows.append({"name": f"value{n}", "type": "Table"})
                    commit()
                    render()

                ui.button("Add port", icon="add", on_click=add).props("flat dense")

        render()

    def _model(self, label: str, model: type[BaseModel], value: Any, setter: Setter) -> None:
        current: dict[str, Any] = dict(value or {})
        with ui.expansion(label, value=True).classes("w-full").props("dense"):
            for name, field in model.model_fields.items():

                def set_sub(v: Any, n: str = name) -> None:
                    if v is UNSET:
                        current.pop(n, None)
                    else:
                        current[n] = v
                    setter(dict(current))

                self._field(name, field, current.get(name, default_of(field)), set_sub)

    def _json(self, label: str, hint: str, value: Any, setter: Setter) -> None:
        ui.label(label + " (JSON)").classes("text-xs text-slate-600 mt-1")
        editor = ui.codemirror(
            json.dumps(value, indent=2, ensure_ascii=False), language="JSON"
        ).classes("w-full h-64 border rounded")
        status = ui.label().classes("text-xs")

        def commit() -> None:
            try:
                parsed = json.loads(editor.value or "null")
            except json.JSONDecodeError as exc:
                status.text = f"Invalid JSON, line {exc.lineno}: {exc.msg} (not applied)"
                status.classes(replace="text-xs text-red-600")
                return
            status.text = ""
            setter(parsed)

        editor.on("blur", commit)
        if hint:
            editor.tooltip(hint)


def suffix_hint(label: str) -> str:
    """Guess the file type for the file dialog from the field label (``.h5`` for Nastran)."""
    lower = label.lower()
    for word, suffix in (
        ("nastran", ".h5"),
        (".h5", ".h5"),
        ("excel", ".xlsx"),
        ("csv", ".csv"),
        ("python", ".py"),
        ("script", ".py"),
        ("word", ".docx"),
        ("base document", ".docx"),
    ):
        if word in lower:
            return suffix
    return ""
