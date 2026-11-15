"""Property panel of the Word report block: the section editor (spec 05 §3.4).

Replaces the JSON field for ``sections`` with a list of sections that can be added, moved,
duplicated, deleted and edited, next to a list of the connected items whose names can be
set. The stored format is unchanged. All decisions (what fits, what is wrong) are made by
the pure functions in :mod:`apb.blocks.report.editing`.

Note for changes: a handler that rebuilds a container deletes the widget it runs in, after
which no further GUI update can be made from that handler. Handlers therefore store the
change first and rebuild last.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd
from nicegui import ui

from apb.blocks.report import editing as ed
from apb.blocks.report.block import WordReportParams
from apb.blocks.report.sections import FormatError, python_format
from apb.engine.types import PortType
from apb.gui.editor import EditError
from apb.gui.forms import ParamForm
from apb.units import get_units

KIND_ICONS = {
    "title": "title",
    "heading": "format_size",
    "paragraph": "notes",
    "value": "pin",
    "table": "table_chart",
    "figure": "image",
    "page_break": "insert_page_break",
    "units": "straighten",
    "traceability": "history",
}

_PLACEHOLDER_MESSAGES = ("Unknown placeholder", "Placeholder", "Cannot read the placeholders")


@dataclass
class _Row:
    """The widgets of one section row that are updated without rebuilding the list."""

    summary: ui.label
    warning: ui.icon
    body: ui.column


class ReportPanel:
    """Builds the panel of one Word report block.

    Args:
        params: Stored parameters of the block (kept up to date by ``on_change``).
        on_change: Called with ``(name, value)`` to store a parameter.
        pick_file: Async callback for the file fields (see :class:`ParamForm`).
        resolve: Replaces ``${variable}`` references in a value.
        items: Returns the connected items, with the columns and values of the last run.
        rename_item: Names the connector of an item; returns old -> new names of all items
            that changed, or raises :class:`EditError`.
        confirm: Asks a yes/no question (question, button label).
        rebuild: Rebuilds the whole panel.
    """

    def __init__(
        self,
        params: dict[str, Any],
        *,
        on_change: Callable[[str, Any], None],
        pick_file: Callable[[str], Any] | None,
        resolve: Callable[[Any], Any],
        items: Callable[[], list[ed.ItemInfo]],
        rename_item: Callable[[ed.ItemInfo, str], dict[str, str]],
        confirm: Callable[[str, str], Awaitable[bool]],
        rebuild: Callable[[], None],
    ) -> None:
        self.params = params
        self.on_change = on_change
        self.resolve = resolve
        self.get_items = items
        self.rename_item = rename_item
        self.confirm = confirm
        self.rebuild = rebuild
        self.form = ParamForm(WordReportParams, params, on_change, pick_file)
        stored = params.get("sections", [])
        self.sections: list[dict[str, Any]] = (
            [dict(s) for s in stored] if isinstance(stored, list) else []
        )
        self.open_index: int | None = None
        self._renaming = False
        self.infos: dict[str, ed.ItemInfo] = {}
        self._rows: list[_Row] = []

    # ---- store ----------------------------------------------------------------------------

    def _commit(self, sections: list[dict[str, Any]]) -> None:
        """Store the sections as the block's parameter."""
        self.sections = sections
        self.on_change("sections", copy.deepcopy(sections))

    def _set_field(self, index: int, name: str, value: Any, *, keep_empty: bool = False) -> None:
        """Change one field of a section; empty optional values are removed from the file."""
        section = dict(self.sections[index])
        if value in (None, "", [], {}) and not keep_empty:
            section.pop(name, None)
        else:
            section[name] = value
        sections = list(self.sections)
        sections[index] = section
        self._commit(sections)
        self._refresh_header()

    def _metadata(self) -> dict[str, str]:
        """Return the report metadata with variables replaced, for the live preview."""
        raw = self.params.get("metadata")
        values = dict(raw) if isinstance(raw, dict) else {}
        out: dict[str, str] = {}
        for name in ed.METADATA_NAMES:
            try:
                out[name] = str(self.resolve(values.get(name, "")))
            except Exception:  # an undefined variable: shown as written
                out[name] = str(values.get(name, ""))
        return out

    def _problems(self) -> list[ed.Problem]:
        return ed.check_sections(self.sections, self.infos)

    # ---- build ----------------------------------------------------------------------------

    def build(self) -> None:
        """Create the widgets in the current NiceGUI context."""
        self.form.field("output_file")
        self.form.field("base_document")
        self.form.field("metadata")
        ui.label("Items (connected to this block)").classes(
            "text-xs font-medium text-slate-600 mt-2"
        )
        self.items_box = ui.column().classes("w-full gap-1 apb-items")
        with ui.row().classes("w-full items-center mt-2"):
            ui.label("Sections").classes("text-xs font-medium text-slate-600")
            ui.space()
            with ui.button("Add section", icon="add").props("dense outline"), ui.menu():
                for kind, label in ed.SECTION_KINDS:
                    ui.menu_item(label, on_click=lambda k=kind: self._add(k))
        if not isinstance(self.params.get("sections", []), list):
            self.form.field("sections")  # a ${variable}: keep the plain field
            self.list_box = ui.column()
            return
        self.list_box = ui.column().classes("w-full gap-1 apb-sections")
        self._refresh_items()
        self._render_list()

    def _refresh_items(self) -> None:
        """Read the items again and rebuild the items list."""
        self.infos = {i.name: i for i in self.get_items()}
        self._render_items()

    # ---- items ----------------------------------------------------------------------------

    def _render_items(self) -> None:
        self.items_box.clear()
        used = ed.referenced_names(self.sections)
        with self.items_box:
            for problem in self._problems():
                if problem.index < 0:
                    ui.label(problem.message).classes("text-xs text-red-700")
            if not self.infos:
                ui.label("Nothing is connected to the 'Items' input yet.").classes(
                    "text-xs text-slate-500"
                )
            for info in self.infos.values():
                self._item_row(info, info.name in used)

    def _item_row(self, info: ed.ItemInfo, used: bool) -> None:
        with ui.column().classes("w-full gap-0 apb-item"):
            box = ui.input(value=info.name).props("dense outlined").classes("w-full apb-item-name")
            box.tooltip("Name used by the sections and {placeholders}; edit to rename")
            box.on("blur", lambda _e, i=info, b=box: self._rename(i, str(b.value or "")))
            box.on("keydown.enter", lambda _e, i=info, b=box: self._rename(i, str(b.value or "")))
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(f"{info.type.value} · {info.source}").classes(
                    "text-[10px] text-slate-500 truncate"
                )
                if not used:
                    ui.label("used nowhere").classes("text-[10px] text-amber-700 apb-unused")

    async def _rename(self, info: ed.ItemInfo, name: str) -> None:
        """Name the connector of an item and update the references to it (REP-15)."""
        if self._renaming:  # Enter and the blur that follows both arrive here
            return
        name = name.strip()
        if name == info.name or (not name and not info.explicit):
            self._render_items()
            return
        self._renaming = True
        try:
            if name and not ed.valid_item_name(name):
                ui.notify(
                    "Names consist of letters, digits and _ and start with a letter",
                    type="warning",
                )
            elif name in self.infos:
                ui.notify(f"The name {name!r} is already used by another item", type="warning")
            else:
                refs = ed.count_references(self.sections, info.name)
                if not refs or await self.confirm(
                    f"{refs} reference(s) in the sections will be changed to the new name.",
                    "Rename",
                ):
                    try:
                        changes = self.rename_item(info, name)
                    except EditError as exc:
                        ui.notify(str(exc), type="warning")
                    else:
                        if changes:
                            self._commit(ed.rename_items(self.sections, changes))
                        self.rebuild()
                        return
            self._render_items()
        finally:
            self._renaming = False

    # ---- the list of sections -------------------------------------------------------------

    def _render_list(self) -> None:
        self.list_box.clear()
        self._rows = []
        problems = self._problems()
        with self.list_box:
            if not self.sections:
                ui.label("The report is empty. Add the sections you want in it.").classes(
                    "text-xs text-slate-500"
                )
            for index in range(len(self.sections)):
                self._row(index, [p.message for p in problems if p.index == index])

    def _row(self, index: int, messages: list[str]) -> None:
        section = self.sections[index]
        is_open = index == self.open_index
        with ui.card().props("flat bordered").classes("w-full p-1 gap-0 apb-section"):
            with ui.row().classes("w-full items-center no-wrap gap-1"):
                ui.label(str(index + 1)).classes("text-[10px] text-slate-400 w-4 text-right")
                ui.icon(KIND_ICONS.get(str(section.get("kind")), "notes"), size="xs").classes(
                    "text-slate-600"
                )
                summary = ui.label(ed.describe(section)).classes(
                    "text-xs grow cursor-pointer truncate apb-section-summary"
                    + (" font-medium" if is_open else "")
                )
                summary.on("click", lambda _e, i=index: self._toggle(i))
                warning = ui.icon("warning", color="amber-8", size="xs").classes(
                    "apb-section-warning"
                )
                self._set_warning(warning, messages)
                for icon, tip, action in (
                    ("arrow_upward", "Move up", lambda i=index: self._move(i, -1)),
                    ("arrow_downward", "Move down", lambda i=index: self._move(i, 1)),
                    ("content_copy", "Duplicate", lambda i=index: self._duplicate(i)),
                    ("delete", "Delete", lambda i=index: self._delete(i)),
                ):
                    ui.button(icon=icon, on_click=action).props(
                        f'flat dense size=xs aria-label="{tip}"'
                    ).tooltip(tip)
            body = ui.column().classes("w-full gap-1 p-1 apb-section-body")
            body.set_visibility(is_open)
        self._rows.append(_Row(summary, warning, body))
        if is_open:
            self._render_body(index)

    @staticmethod
    def _set_warning(icon: ui.icon, messages: list[str]) -> None:
        icon.set_visibility(bool(messages))
        icon._props["title"] = "\n".join(messages)
        icon.update()

    def _refresh_header(self) -> None:
        """Update summaries and warnings of all rows (no widget is rebuilt)."""
        self.infos = {i.name: i for i in self.get_items()}
        problems = self._problems()
        for index, row in enumerate(self._rows):
            if index < len(self.sections):
                row.summary.text = ed.describe(self.sections[index])
            self._set_warning(row.warning, [p.message for p in problems if p.index == index])

    def _toggle(self, index: int) -> None:
        self.open_index = None if self.open_index == index else index
        self._render_list()

    def _add(self, kind: str) -> None:
        index = len(self.sections) if self.open_index is None else self.open_index + 1
        self.open_index = index
        self._commit(ed.insert(self.sections, index, ed.new_section(kind)))
        self._render_list()

    def _move(self, index: int, delta: int) -> None:
        if self.open_index == index:
            self.open_index = max(0, min(index + delta, len(self.sections) - 1))
        self._commit(ed.move(self.sections, index, delta))
        self._render_list()

    def _duplicate(self, index: int) -> None:
        self.open_index = index + 1
        self._commit(ed.duplicate(self.sections, index))
        self._render_list()

    def _delete(self, index: int) -> None:
        self.open_index = None
        self._commit(ed.delete(self.sections, index))
        self._refresh_items_after_delete()

    def _refresh_items_after_delete(self) -> None:
        self._render_items()
        self._render_list()

    # ---- the fields of one section --------------------------------------------------------

    def _render_body(self, index: int) -> None:
        body = self._rows[index].body
        body.clear()
        section = self.sections[index]
        kind = str(section.get("kind"))
        with body:
            match kind:
                case "heading":
                    self._text(index, "text", "Heading text")
                    levels = ui.select({1: "1", 2: "2", 3: "3"}, value=section.get("level", 1))
                    levels.props("dense outlined label=Level").classes("w-24")
                    levels.on_value_change(
                        lambda e, i=index: self._set_field(i, "level", e.value, keep_empty=True)
                    )
                case "paragraph":
                    self._text(index, "text", "Text", multiline=True)
                case "value":
                    self._item_select(index, kind)
                    self._text(index, "label", "Label (optional)")
                    self._format_input(index, "format", "Format", section.get("format", ""), "auto")
                case "table":
                    self._table_fields(index)
                case "figure":
                    self._item_select(index, kind)
                    self._text(index, "caption", "Caption")
                    self._number(index, "width_cm", "Width [cm]", section.get("width_cm", 15.0))
                    self._number(index, "dpi", "DPI", section.get("dpi", 200), integer=True)
                case "traceability":
                    self._text(index, "heading", "Heading", placeholders=False)
                case _:
                    ui.label("This section has no settings.").classes("text-xs text-slate-500")

    def _item_select(self, index: int, kind: str) -> None:
        """Drop-down of the connected items that fit the section kind (REP-16)."""
        section = self.sections[index]
        accepted = ed.ITEM_TYPES.get(kind, ())
        options = {
            n: f"{n} ({i.type.value})"
            for n, i in self.infos.items()
            if i.type is PortType.ANY or i.type in accepted
        }
        current = str(section.get("item", ""))
        if current and current not in options:
            options[current] = f"{current} (not available here)"
        select = ui.select(options, value=current or None, label="Item")
        select.props("dense outlined").classes("w-full apb-item-select")

        def changed(e: Any, i: int = index) -> None:
            self._set_field(i, "item", e.value or "", keep_empty=True)
            self._render_body(i)

        select.on_value_change(changed)

    def _text(
        self,
        index: int,
        name: str,
        label: str,
        *,
        multiline: bool = False,
        placeholders: bool = True,
    ) -> None:
        """A text field; with *Insert value*, placeholder check and live preview (REP-17)."""
        value = str(self.sections[index].get(name, ""))
        with ui.row().classes("w-full items-start no-wrap gap-1"):
            widget = ui.textarea(label, value=value) if multiline else ui.input(label, value=value)
            widget.props("dense outlined" + (" autogrow" if multiline else ""))
            widget.classes("grow apb-text-" + name)
            if placeholders:

                async def insert(
                    item: str, kind: PortType, w: Any = widget, i: int = index, n: str = name
                ) -> None:
                    await self._insert(w, i, n, item, kind)

                with (
                    ui.button(icon="data_object")
                    .props('flat dense size=sm aria-label="Insert value"')
                    .tooltip("Insert value"),
                    ui.menu(),
                ):
                    for item_name, info in self.infos.items():
                        if info.type in (PortType.SCALAR, PortType.TEXT, PortType.ANY):
                            ui.menu_item(
                                item_name,
                                on_click=lambda _e, a=item_name, b=info.type: insert(a, b),
                            )
                    for meta in ed.METADATA_NAMES:
                        ui.menu_item(
                            f"{meta} (report)",
                            on_click=lambda _e, a=meta: insert(a, PortType.TEXT),
                        )
        status = ui.label().classes("text-[10px] apb-text-status -mt-1")

        def show_status(text: str) -> None:
            probe = {**self.sections[index], name: text}
            messages = [
                p.message
                for p in ed.check_sections([probe], self.infos)
                if p.message.startswith(_PLACEHOLDER_MESSAGES)
            ]
            if not placeholders or (not ed.placeholder_names(text) and not messages):
                status.text = ""
            elif messages:
                status.text = "\n".join(messages)
                status.classes(
                    replace="text-[10px] text-red-700 whitespace-pre-wrap apb-text-status"
                )
            else:
                filled = ed.fill_preview(text, self.infos, self._metadata())
                status.text = f"→ {filled}" if filled is not None else "run to see the values"
                status.classes(replace="text-[10px] text-slate-500 apb-text-status")

        widget.on_value_change(lambda e: show_status(str(e.value or "")))
        widget.on("blur", lambda _e, i=index: self._commit_text(i, name, str(widget.value or "")))
        show_status(value)

    def _commit_text(self, index: int, name: str, text: str) -> None:
        required = self.sections[index].get("kind") in ("heading", "paragraph")
        self._set_field(index, name, text, keep_empty=required and name == "text")
        self._render_items()

    async def _insert(self, widget: Any, index: int, name: str, item: str, kind: PortType) -> None:
        """Insert ``{item}`` (``{item:.2f}`` for a scalar) at the cursor of a text field."""
        token = f"{{{item}:.2f}}" if kind is PortType.SCALAR else f"{{{item}}}"
        text = str(widget.value or "")
        position = len(text)
        try:
            found = await ui.run_javascript(
                f"(() => {{ const root = getHtmlElement({widget.id});"
                " const el = root.querySelector('textarea, input');"
                " return el ? el.selectionStart : null; })()"
            )
            if isinstance(found, int):
                position = max(0, min(found, len(text)))
        except TimeoutError:
            pass
        widget.value = text[:position] + token + text[position:]
        self._commit_text(index, name, str(widget.value))

    def _format_input(
        self, index: int, name: str, label: str, value: str, placeholder: str
    ) -> None:
        box = ui.input(label, value=value, placeholder=placeholder).props("dense outlined")
        box.classes("w-full apb-format-" + name)

        def commit(i: int = index) -> None:
            text = str(box.value or "").strip()
            try:
                python_format(text)
            except FormatError as exc:
                box.props(f"error error-message={_quote(str(exc))}")
                return
            box.props(remove="error")
            self._set_field(i, name, text)

        box.on("blur", commit)
        box.on("keydown.enter", commit)

    def _number(
        self, index: int, name: str, label: str, value: Any, *, integer: bool = False
    ) -> None:
        box = ui.number(label, value=value, format="%.0f" if integer else None).props(
            "dense outlined"
        )
        box.classes("w-full")

        def commit(i: int = index) -> None:
            if box.value is None:
                self._set_field(i, name, None)
                return
            self._set_field(i, name, int(box.value) if integer else float(box.value))

        box.on("blur", commit)

    # ---- table section --------------------------------------------------------------------

    def _table_fields(self, index: int) -> None:
        section = self.sections[index]
        info = self.infos.get(str(section.get("item", "")))
        self._item_select(index, "table")
        self._text(index, "caption", "Caption")
        self._columns_field(index, info)
        ui.label("Formats").classes("text-[10px] font-medium text-slate-600")
        formats = ui.column().classes("w-full gap-1 apb-formats")
        self._format_rows(index, info, formats)
        self._format_input(
            index,
            "default_format",
            "Default format (decimal columns)",
            section.get("default_format", ""),
            "auto",
        )
        with ui.row().classes("w-full no-wrap gap-1"):
            self._number(index, "max_rows", "Max. rows", section.get("max_rows"), integer=True)
        highlight = (
            ui.input(
                "Highlight rows where",
                value=str(section.get("highlight", "")),
                placeholder="MS < 0",
            )
            .props("dense outlined")
            .classes("w-full")
        )
        highlight.on(
            "blur",
            lambda _e, i=index: self._set_field(i, "highlight", str(highlight.value or "").strip()),
        )
        switch = ui.switch(
            "Units in header", value=bool(section.get("units_in_header", True))
        ).props("dense")
        switch.on_value_change(
            lambda e, i=index: self._set_field(i, "units_in_header", bool(e.value), keep_empty=True)
        )

    def _columns_field(self, index: int, info: ed.ItemInfo | None) -> None:
        chosen = list(self.sections[index].get("columns", []))
        if info is not None and info.columns:
            options = {c: c for c in info.columns}
            options.update({c: f"{c} (not in table)" for c in chosen if c not in options})
            select = ui.select(options, value=chosen, label="Columns (empty = all)", multiple=True)
            select.props("dense outlined use-chips").classes("w-full apb-columns")

            def changed(e: Any, i: int = index) -> None:
                self._set_field(i, "columns", list(e.value or []))
                self._render_body(i)

            select.on_value_change(changed)
            return
        box = (
            ui.input("Columns (comma-separated, empty = all)", value=", ".join(chosen))
            .props("dense outlined")
            .classes("w-full apb-columns")
        )
        box.tooltip("The column names are offered after the connected block has run")

        def commit(i: int = index) -> None:
            names = [c.strip() for c in str(box.value or "").split(",") if c.strip()]
            self._set_field(i, "columns", names)

        box.on("blur", commit)

    def _format_rows(self, index: int, info: ed.ItemInfo | None, container: ui.column) -> None:
        section = self.sections[index]
        formats: dict[str, str] = dict(section.get("formats", {}))
        columns = list(section.get("columns") or (info.columns if info and info.columns else ()))
        units: Mapping[str, str] = (
            get_units(info.value)
            if info is not None and isinstance(info.value, pd.DataFrame)
            else {}
        )
        with container:
            rows = columns + [c for c in formats if c not in columns]
            if not rows:
                ui.label("Run the connected block, or choose columns, to set formats.").classes(
                    "text-[10px] text-slate-500"
                )
            for column in rows:
                known = info is None or info.columns is None or column in info.columns
                label = column + (f" [{units[column]}]" if units.get(column, "-") != "-" else "")
                with ui.row().classes("w-full items-center no-wrap gap-1"):
                    ui.label(label + ("" if known else " (not in table)")).classes(
                        "text-xs w-2/5 truncate" + ("" if known else " text-amber-800")
                    )
                    box = (
                        ui.input(value=formats.get(column, ""), placeholder="default")
                        .props("dense outlined")
                        .classes("grow apb-format-column")
                    )
                    box.on(
                        "blur",
                        lambda _e, c=column, b=box, i=index: self._set_column_format(i, c, b),
                    )
                    box.on(
                        "keydown.enter",
                        lambda _e, c=column, b=box, i=index: self._set_column_format(i, c, b),
                    )

    def _set_column_format(self, index: int, column: str, box: ui.input) -> None:
        text = str(box.value or "").strip()
        try:
            if text:
                python_format(text)
        except FormatError as exc:
            box.props(f"error error-message={_quote(str(exc))}")
            return
        box.props(remove="error")
        formats = dict(self.sections[index].get("formats", {}))
        if text:
            formats[column] = text
        else:
            formats.pop(column, None)
        self._set_field(index, "formats", formats)


def _quote(text: str) -> str:
    """Quote a message for a Quasar prop value."""
    return '"' + text.replace('"', "'") + '"'
