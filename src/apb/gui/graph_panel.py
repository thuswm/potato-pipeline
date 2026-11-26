"""Property panel of the Graph block (spec 13 §3.5).

The plot type is chosen with icon buttons; only the fields that apply to it are shown
(the panel is rebuilt when the type changes). Reference lines are edited as rows. The
standard field widgets come from :class:`apb.gui.forms.ParamForm`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from apb.blocks.figures.graph import (
    BAR_KINDS,
    KIND_LABELS,
    NO_X_KINDS,
    STACK_KINDS,
    GraphParams,
)
from apb.gui.forms import ParamForm

#: Material icon of each plot type.
KIND_ICONS: dict[str, str] = {
    "line": "show_chart",
    "scatter": "scatter_plot",
    "bar": "bar_chart",
    "barh": "align_horizontal_left",
    "step": "stairs",
    "area": "area_chart",
    "histogram": "equalizer",
    "box": "candlestick_chart",
}

_STYLES = {"dashed": "dashed", "solid": "solid", "dotted": "dotted"}


def fields_for(kind: str) -> tuple[list[str], list[str]]:
    """Return the main and the *Advanced* fields that apply to a plot type.

    Args:
        kind: Plot type (``GraphParams.kind``).

    Returns:
        ``(main, advanced)`` parameter names in display order (without ``kind`` and
        ``reference_lines``, which have their own widgets).
    """
    main: list[str] = []
    if kind not in NO_X_KINDS:
        main.append("x")
    main.append("y")
    if kind != "histogram":
        main.append("series_by")
    if kind in STACK_KINDS or kind == "histogram":
        main.append("stacked")
    if kind == "histogram":
        main.append("bins")
    main += ["title", "x_label", "y_label"]
    if kind not in ("bar", "box"):
        main.append("x_scale")
    if kind != "barh":
        main.append("y_scale")
    if kind in BAR_KINDS:
        main += ["bar_labels", "highlight_below"]
    advanced: list[str] = []
    if kind in ("line", "step", "area"):
        advanced.append("sort_x")
    if kind in ("line", "step"):
        advanced.append("markers")
    advanced += ["legend", "x_min", "x_max", "y_min", "y_max", "grid"]
    advanced += ["width_cm", "height_cm", "dpi", "mass_in_kg"]
    return main, advanced


class GraphPanel:
    """Builds the panel of one Graph block.

    Args:
        params: Stored parameters of the block (kept up to date by ``on_change``).
        on_change: Called with ``(name, value)`` to store a parameter.
        columns: Column names of the table on an input port, if known.
        references: Names of the values connected to the ``reference`` input.
        rebuild: Rebuilds the whole panel (after the plot type changed).
    """

    def __init__(
        self,
        params: dict[str, Any],
        *,
        on_change: Callable[[str, Any], None],
        columns: Callable[[str], list[str] | None],
        references: Callable[[], list[str]],
        rebuild: Callable[[], None],
    ) -> None:
        self.params = params
        self.on_change = on_change
        self.references = references
        self.rebuild = rebuild
        self.form = ParamForm(GraphParams, params, on_change, columns=columns)

    @property
    def kind(self) -> str:
        """The chosen plot type."""
        return str(self.params.get("kind") or "line")

    def build(self) -> None:
        """Create the panel in the current NiceGUI context."""
        self._kind_buttons()
        main, advanced = fields_for(self.kind)
        for name in main:
            self.form.field(name)
        self._reference_lines()
        self.form.advanced(advanced)

    # ---- plot type ------------------------------------------------------------------------

    def _kind_buttons(self) -> None:
        ui.label("Plot type").classes("text-xs text-slate-600")
        with ui.row().classes("w-full gap-1 apb-graph-kinds"):
            for kind, label in KIND_LABELS.items():
                chosen = kind == self.kind
                button = ui.button(
                    icon=KIND_ICONS[kind], on_click=lambda _e, k=kind: self._set_kind(k)
                ).props(
                    f"dense {'unelevated' if chosen else 'flat'} "
                    f'color={"primary" if chosen else "grey-8"} aria-label="{label}"'
                )
                button.classes("apb-graph-kind" + (" apb-graph-kind-chosen" if chosen else ""))
                button.tooltip(label)
        ui.label(KIND_LABELS[self.kind]).classes("text-[10px] text-slate-500 -mt-1")

    def _set_kind(self, kind: str) -> None:
        if kind == self.kind:
            return
        self.on_change("kind", kind)
        self.rebuild()

    # ---- reference lines ------------------------------------------------------------------

    def _rows(self) -> list[dict[str, Any]]:
        raw = self.params.get("reference_lines")
        return [dict(r) for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []

    def _store(self, rows: list[dict[str, Any]]) -> None:
        self.on_change("reference_lines", rows)
        self._render_lines()

    def _reference_lines(self) -> None:
        ui.label("Reference lines").classes("text-xs text-slate-600 mt-1")
        self.lines_box = ui.column().classes("w-full gap-1 apb-reference-lines")
        self._render_lines()

    def _render_lines(self) -> None:
        self.lines_box.clear()
        rows = self._rows()
        names = self.references()
        with self.lines_box:
            for index, row in enumerate(rows):
                with ui.row().classes("w-full items-center no-wrap gap-1 apb-reference-line"):
                    axis = (
                        ui.select({"y": "— y", "x": "| x"}, value=row.get("axis", "y"))
                        .props("dense outlined options-dense")
                        .classes("w-16")
                    )
                    axis.tooltip("y: horizontal line at a y value; x: vertical line")
                    axis.on_value_change(lambda e, i=index: self._update(i, "axis", e.value))
                    value = (
                        ui.input(
                            value=str(row.get("value", "")),
                            placeholder="value",
                            autocomplete=names,
                        )
                        .props("dense outlined")
                        .classes("w-20 apb-reference-value")
                    )
                    value.tooltip(
                        "A number, or the name of a value connected to the Reference input"
                        + (f" ({', '.join(names)})" if names else "")
                    )
                    value.on("blur", lambda _e, i=index, b=value: self._set_value(i, b))
                    label = (
                        ui.input(value=str(row.get("label", "")), placeholder="label")
                        .props("dense outlined")
                        .classes("grow")
                    )
                    label.on("blur", lambda _e, i=index, b=label: self._update(i, "label", b.value))
                    style = (
                        ui.select(_STYLES, value=row.get("style", "dashed"))
                        .props("dense outlined options-dense")
                        .classes("w-24")
                    )
                    style.on_value_change(lambda e, i=index: self._update(i, "style", e.value))
                    ui.button(icon="close", on_click=lambda _e, i=index: self._remove(i)).props(
                        'flat dense round size=sm aria-label="Remove line"'
                    )
            ui.button("Add line", icon="add", on_click=self._add).props(
                "dense flat no-caps size=sm"
            ).classes("apb-reference-add")

    def _set_value(self, index: int, box: ui.input) -> None:
        text = str(box.value or "").strip()
        if not text:
            return
        try:
            value: float | str = float(text)
        except ValueError:
            value = text  # the name of a connected value
        self._update(index, "value", value)

    def _update(self, index: int, key: str, value: Any) -> None:
        rows = self._rows()
        if index >= len(rows):
            return
        if key == "label" and not value:
            rows[index].pop("label", None)
        else:
            rows[index][key] = value
        self.on_change("reference_lines", rows)

    def _add(self) -> None:
        names = self.references()
        self._store([*self._rows(), {"axis": "y", "value": names[0] if names else 0}])

    def _remove(self, index: int) -> None:
        rows = self._rows()
        del rows[index]
        self._store(rows)
