"""Property panel of the "Nastran HDF5 results" block: the result browser (spec 02 §3.7).

Lists the results in the chosen ``.h5`` file, lets the user pick one by clicking, fills the
subcase and column choices from the file and shows a preview straight from the file
("peek") without running the pipeline. The panel edits the ordinary block parameters, so
the pipeline file format is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from nicegui import ui
from pydantic import ValidationError

from apb.blocks.nastran import h5reader
from apb.blocks.nastran.blocks import Hdf5ResultsParams
from apb.blocks.nastran.h5reader import NastranH5Error, ResultDetail, ResultInfo
from apb.engine.variables import VariableError
from apb.gui import h5viewer
from apb.gui.forms import ParamForm, PickFile, connected_line, describe, short_path
from apb.units import UnitSystem

#: Dataset rows read for the preview from the file.
PEEK_ROWS = 200

#: Errors that mean "cannot read this file / these parameters" rather than a program fault.
_READ_ERRORS = (NastranH5Error, VariableError, ValidationError, OSError, ValueError)


def group_results(infos: Sequence[ResultInfo], search: str = "") -> dict[str, list[ResultInfo]]:
    """Group results by the first part of their path, keeping those matching the search.

    Args:
        infos: Results of a file (from :func:`h5reader.list_results`).
        search: Words that must all occur in the display name or path (case-insensitive).

    Returns:
        Group name (``NODAL``, ``ELEMENTAL`` ...) -> results, both in alphabetical order.
    """
    words = search.lower().split()
    groups: dict[str, list[ResultInfo]] = {}
    for info in sorted(infos, key=lambda i: i.path):
        text = f"{info.title} {info.path}".lower()
        if all(w in text for w in words):
            groups.setdefault(info.path.split("/", 1)[0], []).append(info)
    return groups


def prune_selection(params: dict[str, Any], detail: ResultDetail) -> dict[str, Any]:
    """Return the parameter changes needed after another result was selected.

    Subcases and columns that the new result does not have are dropped, and the ID filter
    is cleared if the result has no IDs. ``${variable}`` references are left alone.

    Args:
        params: Stored parameters of the block.
        detail: Description of the newly selected result.

    Returns:
        Parameter name -> new value, only for parameters that must change.
    """
    changes: dict[str, Any] = {}
    for name, available in (("subcases", detail.subcases), ("columns", detail.columns)):
        current = params.get(name)
        if isinstance(current, list) and current:
            kept = [v for v in current if v in available]
            if kept != current:
                changes[name] = kept
    ids = params.get("ids")
    if isinstance(ids, str) and ids.strip() and not ids.startswith("${") and not detail.id_column:
        changes["ids"] = ""
    return changes


def id_summary(detail: ResultDetail) -> str:
    """Return e.g. ``"EID present: 17 to 19 (3 IDs)"``, or ``""`` for results without IDs."""
    if not detail.id_column or detail.id_min is None:
        return ""
    return f"{detail.id_column} present: {detail.id_min} to {detail.id_max} ({detail.id_count} IDs)"


class Hdf5ResultsPanel:
    """Builds the result browser for one "Nastran HDF5 results" block.

    Args:
        params: Stored parameters of the block (updated by ``on_change``).
        on_change: Called with ``(name, value)`` to store a parameter.
        pick_file: Async callback asking the user for a file.
        resolve: Replaces ``${variable}`` references in a value.
        base_dir: Folder that relative paths refer to.
        default_units: Pipeline default unit system.
        show_peek: Called with ``(table, error)`` to show the preview from the file.
        log: Writes a line to the GUI log.
    """

    def __init__(
        self,
        params: dict[str, Any],
        *,
        on_change: Callable[[str, Any], None],
        pick_file: PickFile | None,
        resolve: Callable[[Any], Any],
        base_dir: Path,
        default_units: UnitSystem,
        show_peek: Callable[[pd.DataFrame | None, str], None],
        log: Callable[[str], None],
        connected: Callable[[], tuple[str, str | None] | None] = lambda: None,
    ) -> None:
        self.params = params
        self.on_change = on_change
        self.resolve = resolve
        self.base_dir = base_dir
        self.default_units = default_units
        self.show_peek = show_peek
        self.log = log
        self.connected = connected
        self.form = ParamForm(Hdf5ResultsParams, params, self._changed, pick_file)
        self._infos: list[ResultInfo] = []
        self._search = ""

    # ---- parameters -----------------------------------------------------------------------

    def _changed(self, name: str, value: Any) -> None:
        """Store a parameter, then refresh what depends on it."""
        self.on_change(name, value)
        if name == "file":
            self._build_body()
        elif name in {"subcases", "ids", "columns", "unit_system", "result"}:
            self.peek()

    def _resolved(self) -> Hdf5ResultsParams:
        """Return the parameters with variables replaced and validated."""
        return Hdf5ResultsParams.model_validate(self.resolve(dict(self.params)))

    def _path(self) -> Path | None:
        """Return the absolute path of the chosen file, or ``None`` if none is chosen.

        A file delivered by a connected analysis block wins over the typed path.
        """
        link = self.connected()
        value = (
            link[1] if link is not None and link[1] else self.resolve(self.params.get("file") or "")
        )
        if not value:
            return None
        path = Path(str(value)).expanduser()
        return path if path.is_absolute() else (self.base_dir / path).resolve()

    def _units(self) -> UnitSystem:
        """Return the unit system of the block (its own, else the pipeline default)."""
        value = self.resolve(self.params.get("unit_system"))
        return UnitSystem(value) if value else self.default_units

    def peek(self) -> None:
        """Read the first rows of the selected result from the file and show them (NAS-12)."""
        if not self.params.get("result"):
            return
        try:
            path = self._path()
            if path is None:
                return
            p = self._resolved()
            table = h5reader.read_result(
                path,
                p.result,
                subcases=p.subcases,
                ids=p.ids,
                columns=p.columns or None,
                unit_system=p.unit_system or self.default_units,
                max_rows=PEEK_ROWS,
            )
        except _READ_ERRORS as exc:
            self.show_peek(None, str(exc))
            return
        self.show_peek(table, "")

    def viewer_url(self, path: Path) -> str:
        """Return the HDF5 viewer URL for the file, with the block's result and filters."""
        subcases: list[int] = []
        ids = ""
        try:
            p = self._resolved()
            subcases, ids = list(p.subcases or []), p.ids
        except _READ_ERRORS:
            pass  # unresolved variables: open without filters
        result = str(self.resolve(self.params.get("result") or ""))
        return h5viewer.viewer_url(
            path, dataset=result, subcases=subcases, ids=ids, units=str(self._units())
        )

    def open_viewer(self, path: Path) -> None:
        """Open the file in the HDF5 viewer in a new browser tab (H5V-02)."""
        ui.navigate.to(self.viewer_url(path), new_tab=True)

    def _select(self, info: ResultInfo) -> None:
        """Make a result the block's output: store it, drop stale filters, peek (NAS-11)."""
        self.on_change("result", info.path)
        try:
            path = self._path()
            if path is not None:
                detail = h5reader.describe_result(path, info.path)
                for name, value in prune_selection(self.params, detail).items():
                    self.on_change(name, value)
                    self.log(f"{name} reset: not all values exist in {info.title}")
        except _READ_ERRORS:
            pass  # shown by the body below
        self._build_body()
        self.peek()

    # ---- widgets --------------------------------------------------------------------------

    def build(self) -> None:
        """Create the widgets in the current NiceGUI context."""
        link = self.connected()
        if link is None:
            self.form.field("file")
        else:
            name, path = link
            connected_line(
                "Result file",
                name,
                short_path(path, self.base_dir) if path else "",
                tip=path or "",
            )
        self.body = ui.column().classes("w-full gap-1.5")
        self._build_body()

    def _build_body(self) -> None:
        """(Re)build everything below the file field for the current file."""
        self.body.clear()
        with self.body:
            error = ""
            self._infos = []
            info_text = ""
            try:
                path = self._path()
                if path is not None:
                    info_text = h5reader.file_info(path).label
                    self._infos = h5reader.list_results(path)
            except _READ_ERRORS as exc:
                path = None
                error = str(exc)
            if error:
                ui.label(error).classes("text-xs text-red-700 break-all apb-h5-error")
            if not self._infos:
                if not error:
                    ui.label("Choose a result file to list the results it contains.").classes(
                        "text-xs text-slate-500"
                    )
                # No readable file (yet): plain fields, so a pipeline can still be prepared.
                for name in ("result", "subcases", "ids", "columns", "unit_system"):
                    self.form.field(name)
                return

            with ui.row().classes("w-full items-start no-wrap gap-1"):
                ui.label(info_text).classes("text-xs text-slate-500 grow apb-h5-info")
                if path is not None:
                    ui.button(
                        icon="open_in_new", on_click=lambda p=path: self.open_viewer(p)
                    ).props('dense flat round size=sm aria-label="Open in viewer"').classes(
                        "apb-h5-open-viewer"
                    ).tooltip("Open in viewer: look through the whole file in the HDF5 viewer")
            ui.label("Results in file").classes("apb-section-title mt-1")
            search = (
                ui.input(placeholder="Search results", value=self._search)
                .props("dense outlined clearable")
                .classes("w-full")
            )
            self.list = ui.column().classes(
                "w-full max-h-[26rem] overflow-auto border rounded p-1 gap-0 apb-h5-results"
            )

            def on_search(e: Any) -> None:
                self._search = e.value or ""
                self._render_list()

            search.on_value_change(on_search)
            self._render_list()

            selected = str(self.resolve(self.params.get("result") or ""))
            detail: ResultDetail | None = None
            if selected and path is not None:
                try:
                    detail = h5reader.describe_result(path, selected, self._units())
                except _READ_ERRORS:
                    ui.label(f"The selected result {selected!r} is not in this file.").classes(
                        "text-xs text-amber-700"
                    )
            if detail is None:
                if not selected:
                    ui.label("Click a result to select it and preview its content.").classes(
                        "text-xs text-slate-500"
                    )
                self.form.field("unit_system")
                return
            ui.label(f"Filters · {detail.title}").classes("apb-section-title mt-1")
            self._filters(detail)

    def _render_list(self) -> None:
        """Fill the result list according to the search text."""
        result = str(self.resolve(self.params.get("result") or ""))
        selected = h5reader.normalise_result_path(result) if result else ""
        self.list.clear()
        with self.list:
            groups = group_results(self._infos, self._search)
            if not groups:
                ui.label("No result matches the search.").classes("text-xs text-slate-500")
            for group, infos in groups.items():
                ui.label(group).classes("text-[10px] font-medium text-slate-500 mt-1")
                for info in infos:
                    row = ui.row().classes(
                        "w-full no-wrap items-center gap-1 px-1 rounded cursor-pointer "
                        "hover:bg-slate-100 apb-h5-result"
                        + (
                            " bg-blue-100 border-l-4 border-blue-500 apb-h5-selected"
                            if info.path == selected
                            else ""
                        )
                    )
                    row.on("click", lambda i=info: self._select(i))
                    with row:
                        with ui.column().classes("gap-0 grow min-w-0"):
                            ui.label(info.title).classes("text-xs break-words")
                            if info.known:
                                ui.label(info.path).classes("text-[10px] text-slate-500 break-all")
                        ui.label(info.size).classes("text-[10px] text-slate-500 whitespace-nowrap")

        # Rebuilding the list resets its scroll position: bring the selection back into view.
        ui.run_javascript(
            "setTimeout(() => document.querySelector('.apb-h5-selected')"
            "?.scrollIntoView({block: 'nearest'}), 50)"
        )

    def _filters(self, detail: ResultDetail) -> None:
        """Create the subcase, ID, column and unit fields for the selected result (NAS-13)."""
        self._multi("subcases", "Subcases", list(detail.subcases), "Empty = all subcases")
        if detail.id_column:
            self.form.field("ids")
            ui.label(id_summary(detail)).classes("text-[10px] text-slate-500 -mt-1 apb-h5-ids")
        labels = {
            c: f"{c} [{detail.units[c]}]" if detail.units.get(c, "-") != "-" else c
            for c in detail.columns
        }
        self._multi("columns", "Columns", list(detail.columns), "Empty = all columns", labels)
        self.form.field("unit_system")

    def _multi(
        self,
        name: str,
        label: str,
        available: list[Any],
        hint: str,
        labels: dict[Any, str] | None = None,
    ) -> None:
        """Create a multi-select for a list parameter, with the values present in the file."""
        current = self.params.get(name) or []
        if not isinstance(current, list):  # a ${variable}: keep the plain text field
            self.form.field(name)
            return
        options = {v: (labels or {}).get(v, str(v)) for v in available}
        options.update({v: f"{v} (not in file)" for v in current if v not in options})
        if not options:
            return
        select = (
            ui.select(options, value=list(current), label=label, multiple=True)
            .props("dense outlined use-chips")
            .classes("w-full")
        )
        describe(select, hint, placeholder=False)
        select.on_value_change(lambda e: self._changed(name, list(e.value or [])))
