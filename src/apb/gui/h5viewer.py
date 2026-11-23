"""HDF5 viewer: a read-only view of a whole ``.h5`` file in its own browser tab (spec 10).

The page is served at ``/h5?file=<path>`` (H5V-01). All file access goes through
:mod:`apb.blocks.nastran.h5browse`, which opens the file read-only for each read.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import h5py
import pandas as pd
from nicegui import run, ui

from apb.blocks.nastran import h5browse, h5reader
from apb.blocks.nastran.h5reader import NastranH5Error
from apb.gui.filedialog import FileDialog, confirm
from apb.gui.preview import table_rows
from apb.units import UnitSystem

#: Route of the viewer page.
ROUTE = "/h5"

#: Page sizes offered for the table (H5V spec §3.4).
PAGE_SIZES = (50, 100, 500)

#: Tree row: icon, name and size of a dataset.
TREE_HEADER = (
    '<div class="row items-center no-wrap">'
    '<q-icon :name="props.node.icon" size="xs" class="q-mr-xs text-grey-7"/>'
    '<span class="text-no-wrap">{{ props.node.label }}</span>'
    '<span class="q-ml-sm text-grey-6 text-caption text-no-wrap">{{ props.node.size }}</span>'
    "</div>"
)

#: Choices of the unit selector: value -> label. ``""`` = no unit labels.
UNIT_CHOICES = {"": "no units", "SI": "SI (m, N, kg)", "SI_mm": "SI (mm, N, t)"}


def viewer_url(
    file: str | Path,
    *,
    dataset: str = "",
    subcases: Sequence[int] = (),
    ids: str = "",
    units: str = "",
) -> str:
    """Return the URL of the viewer page for a file (H5V-01, spec 10 §3.2).

    Args:
        file: The ``.h5`` file (made absolute).
        dataset: Dataset to select: absolute path or result path (``NODAL/DISPLACEMENT``).
        subcases: Subcase filter of the result view.
        ids: ID filter of the result view.
        units: Unit system (``"SI"``, ``"SI_mm"`` or ``""``).

    Returns:
        A path with query string, e.g. ``/h5?file=%2Fdata%2Fmodal.h5``.
    """
    query: dict[str, str] = {"file": str(Path(file).expanduser().resolve())}
    if dataset:
        query["dataset"] = dataset
    if subcases:
        query["subcases"] = ",".join(str(s) for s in subcases)
    if ids:
        query["ids"] = ids
    if units:
        query["units"] = units
    return f"{ROUTE}?{urllib.parse.urlencode(query)}"


def parse_int_list(text: str) -> list[int]:
    """Parse ``"1, 2,3"`` into ``[1, 2, 3]``; invalid parts are ignored."""
    return [int(p) for p in text.replace(";", ",").split(",") if p.strip().isdigit()]


def _number(n: int) -> str:
    """Return a count with spaces as thousands separators, e.g. ``30 080``."""
    return f"{n:,}".replace(",", " ")


def build_tree(nodes: Sequence[h5browse.H5Node]) -> list[dict[str, Any]]:
    """Turn the flat node list into the nested structure of ``ui.tree`` (H5V-03).

    Args:
        nodes: From :func:`h5browse.list_tree` (sorted by path).

    Returns:
        Tree nodes with ``id`` (path), ``label`` (name), ``size`` and ``children``.
    """
    roots: list[dict[str, Any]] = []
    by_path: dict[str, dict[str, Any]] = {}
    for node in nodes:
        item: dict[str, Any] = {
            "id": node.path,
            "label": node.name,
            "size": node.size_label if node.kind == "dataset" else "",
            "icon": "folder" if node.kind == "group" else "table_chart",
        }
        if node.kind == "group":
            item["children"] = []
        by_path[node.path] = item
        parent = by_path.get(node.parent)
        (parent["children"] if parent is not None else roots).append(item)
    return roots


def size_label(size: int) -> str:
    """Return a file size for display, e.g. ``2.4 MB``."""
    for unit, factor in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}"
    return f"{size} B"


def file_summary(path: Path) -> str:
    """Return the one-line file summary of the page header (H5V-09)."""
    parts = [str(path), size_label(path.stat().st_size)]
    try:
        info = h5reader.file_info(path)
        if info.label:
            parts.append(info.label)
    except NastranH5Error:
        pass
    subcases = h5browse.subcase_summary(path)
    if subcases:
        parts.append("Subcases: " + ", ".join(s.label for s in subcases))
    return " · ".join(parts)


def default_units(path: Path, requested: str) -> str:
    """Return the unit system to start with: requested, else the file's own, else none."""
    if requested and requested in UNIT_CHOICES:
        return requested
    try:
        with h5py.File(path, "r") as h5:
            stored = h5.attrs.get("APB_UNIT_SYSTEM", "")
        text = stored.decode() if isinstance(stored, bytes) else str(stored)
        return text if text in UNIT_CHOICES else ""
    except OSError:
        return ""


class H5ViewerPage:
    """The viewer page for one file (spec 10 §3.1)."""

    def __init__(
        self,
        file: str,
        *,
        dataset: str = "",
        subcases: str = "",
        ids: str = "",
        units: str = "",
    ) -> None:
        """Build the page.

        Args:
            file: Path of the ``.h5`` file (from the URL).
            dataset: Dataset to select first.
            subcases: Subcase filter, e.g. ``"1,2"``.
            ids: ID filter.
            units: Unit system ``"SI"``, ``"SI_mm"`` or ``""``.
        """
        self.path = Path(file).expanduser() if file else None
        self.dataset = ""
        self.result = ""
        self.selection: h5browse.RowSelection | None = None
        self.page_size = 100
        self.page = 1
        self.result_view = True
        self.subcases = parse_int_list(subcases)
        self.modes: list[int] = []
        self.ids = ids
        self.units = default_units(self.path, units) if self.path and self.path.is_file() else ""
        self._initial = dataset

        ui.page_title(f"{self.path.name if self.path else 'No file'} — HDF5 viewer")
        ui.add_css(".apb-h5v-tree .q-tree__node-header { padding: 0 4px; }")
        with ui.column().classes("w-full h-screen no-wrap gap-0"):
            self._header()
            self.body = ui.row().classes("w-full grow no-wrap overflow-hidden")
        self._load()

    # ---------------------------------------------------------------------------- layout

    def _header(self) -> None:
        with ui.row().classes("w-full items-center gap-2 px-3 py-1 bg-slate-800 text-white"):
            ui.icon("account_tree")
            ui.label(f"HDF5 viewer — {self.path.name if self.path else ''}").classes(
                "text-base font-medium"
            )
            ui.space()
            ui.button("Reload", icon="refresh", on_click=self._reload).props(
                "flat dense color=white"
            ).tooltip("Read the file again (e.g. after a new Nastran run)")
            self.copy_button = ui.button("Copy path", icon="content_copy", on_click=self._copy)
            self.copy_button.props("flat dense color=white").tooltip(
                "Copy the dataset path for an HDF5 block"
            )
            self.export_button = ui.button("Export CSV", icon="download", on_click=self._export)
            self.export_button.props("flat dense color=white").tooltip(
                "Write the current view (all pages, with filters) to a CSV file"
            )
            ui.select(
                UNIT_CHOICES, value=self.units, on_change=lambda e: self._set_units(e.value)
            ).props("dense dark options-dense borderless").classes("w-36 apb-h5v-units").tooltip(
                "Unit system of the model, for the unit labels"
            )
        self.summary = ui.label().classes("w-full px-3 py-1 text-xs text-slate-600 bg-slate-100")

    def _load(self) -> None:
        """Read the tree and build the body (or show the error, H5V-13)."""
        self.body.clear()
        self.copy_button.disable()
        self.export_button.disable()
        if self.path is None:
            with self.body:
                self._message(
                    "No file given. Open the viewer from File > HDF5 viewer… or "
                    "with `apb h5view <file>`."
                )
            return
        try:
            nodes = h5browse.list_tree(self.path)
            self.summary.set_text(file_summary(self.path))
        except (NastranH5Error, OSError) as exc:
            self.summary.set_text(str(self.path))
            with self.body:
                self._message(str(exc))
            return
        self.paths = {n.path: n for n in nodes}
        with self.body:
            splitter = ui.splitter(value=25, limits=(10, 60)).classes("w-full h-full")
            with splitter.before, ui.column().classes("w-full h-full no-wrap gap-1 p-2"):
                search = (
                    ui.input(placeholder="search…")
                    .props("dense clearable")
                    .classes("w-full apb-h5v-search")
                )
                with ui.scroll_area().classes("w-full grow"):
                    self.tree = (
                        ui.tree(build_tree(nodes), node_key="id", on_select=self._on_select)
                        .props("dense no-transition")
                        .classes("apb-h5v-tree text-sm")
                    )
                    self.tree.add_slot("default-header", TREE_HEADER)
                search.bind_value_to(self.tree, "filter")
                # Matches may sit in folded groups: unfold everything while searching.
                search.on_value_change(lambda e: self.tree.expand() if e.value else None)
            with splitter.after:
                self.right = ui.column().classes("w-full h-full no-wrap overflow-auto p-2 gap-1")
        start = self._initial or self.dataset
        if start:
            self._select(start)
        else:
            with self.right:
                self._message("Select a dataset or group in the tree.", muted=True)

    @staticmethod
    def _message(text: str, muted: bool = False) -> None:
        ui.label(text).classes(
            "p-4 text-sm apb-h5v-message " + ("text-slate-500" if muted else "text-red-700")
        )

    # ---------------------------------------------------------------------------- events

    def _on_select(self, event: Any) -> None:
        if event.value:
            self._select(str(event.value))

    def _select(self, dataset: str) -> None:
        """Show a group or dataset; result paths such as ``NODAL/DISPLACEMENT`` work too."""
        full = h5browse.absolute_path(dataset)
        if full not in self.paths:
            full = h5browse.absolute_path(
                f"{h5reader.RESULT_ROOT}/{h5reader.normalise_result_path(dataset)}"
            )
        node = self.paths.get(full)
        self.right.clear()
        if node is None:
            with self.right:
                self._message(f"{dataset} not found in the file.")
            return
        if full != self.dataset:
            if self.dataset:  # filters of the previous dataset do not carry over
                self.subcases, self.modes, self.ids = [], [], ""
            self.page = 1
        self.dataset = full
        self.result = h5browse.result_path(full) if node.kind == "dataset" else ""
        parents = [p for p in self.paths if full.startswith(p + "/")]
        if node.kind == "group":
            parents.append(full)
        self.tree.expand(parents)
        self.tree.props["selected"] = full
        self.copy_button.enable()
        if node.kind == "group":
            self.export_button.disable()
            with self.right:
                ui.label(full).classes("text-sm font-medium break-all apb-h5v-path")
                ui.label(f"Group · {node.size_label}").classes("text-xs text-slate-500")
                self._details(open_=True)
            return
        self.export_button.enable()
        with self.right:
            with ui.row().classes("w-full items-center gap-3"):
                ui.label(full).classes("text-sm font-medium break-all apb-h5v-path")
                self.count_label = ui.label().classes("text-xs text-slate-500 apb-h5v-count")
                ui.space()
                if self.result:
                    ui.toggle(
                        {True: "Result", False: "Raw"},
                        value=self.result_view,
                        on_change=lambda e: self._set_view(bool(e.value)),
                    ).props("dense no-caps size=sm").classes("apb-h5v-view").tooltip(
                        "Result: as the HDF5 block delivers it. Raw: as stored in the file"
                    )
            self.filters = ui.row().classes("w-full items-end gap-2")
            self.table_area = ui.column().classes("w-full gap-1")
            self._details(open_=False)
        self._build_filters()
        self._refresh_selection()

    def _build_filters(self) -> None:
        """Subcase, mode and ID filters of a result dataset (H5V-07)."""
        self.filters.clear()
        if not self.result:
            return
        try:
            detail = h5reader.describe_result(self.path or "", self.result)
        except NastranH5Error:
            return
        self.subcases = [s for s in self.subcases if s in detail.subcases]
        modes = h5browse.mode_choices(self.path or "", self.result, detail)
        self.modes = [m for m in self.modes if m in modes]
        with self.filters:
            if detail.subcases:
                ui.select(
                    list(detail.subcases),
                    multiple=True,
                    value=self.subcases,
                    label="Subcases",
                    on_change=lambda e: self._set_filter("subcases", e.value),
                ).props("dense use-chips clearable options-dense").classes(
                    "min-w-40 apb-h5v-subcases"
                )
            if modes:
                ui.select(
                    modes,
                    multiple=True,
                    value=self.modes,
                    label="Modes",
                    on_change=lambda e: self._set_filter("modes", e.value),
                ).props("dense use-chips clearable options-dense").classes("min-w-40 apb-h5v-modes")
            if detail.id_column:
                ids = (
                    ui.input(
                        f"{detail.id_column} filter",
                        value=self.ids,
                        placeholder="e.g. 1001-1200, 3005",
                    )
                    .props("dense clearable")
                    .classes("w-56 apb-h5v-ids")
                )
                ids.on("keydown.enter", lambda: self._set_filter("ids", ids.value or ""))
                ids.on("blur", lambda: self._set_filter("ids", ids.value or ""))
                ids.on("clear", lambda: self._set_filter("ids", ""))
                ui.label(
                    f"present: {detail.id_min} - {detail.id_max} ({_number(detail.id_count)} IDs)"
                ).classes("text-xs text-slate-500 pb-2")

    def _set_filter(self, name: str, value: Any) -> None:
        if name == "ids":
            if value == self.ids:
                return
            self.ids = str(value)
        elif name == "subcases":
            self.subcases = [int(v) for v in value or []]
        else:
            self.modes = [int(v) for v in value or []]
        self.page = 1
        self._refresh_selection()

    def _set_view(self, result_view: bool) -> None:
        self.result_view = result_view
        self._show_page()

    def _set_units(self, units: str) -> None:
        self.units = units or ""
        if self.dataset:
            self._select(self.dataset)

    @property
    def unit_system(self) -> UnitSystem | None:
        """The selected unit system (``None`` = no unit labels)."""
        return UnitSystem(self.units) if self.units else None

    # ----------------------------------------------------------------------------- table

    def _refresh_selection(self) -> None:
        """Find the matching rows once per filter change (spec 10 §3.4)."""
        try:
            self.selection = h5browse.select_rows(
                self.path or "",
                self.dataset,
                subcases=self.subcases,
                modes=self.modes,
                ids=self.ids,
            )
        except NastranH5Error as exc:
            self.selection = None
            self.table_area.clear()
            with self.table_area:
                self._message(str(exc))
            return
        self._show_page()

    def _show_page(self) -> None:
        sel = self.selection
        if sel is None:
            return
        pages = max(1, -(-sel.total // self.page_size))
        self.page = min(self.page, pages)
        start = (self.page - 1) * self.page_size
        result_view = self.result_view and bool(sel.result)
        try:
            df = h5browse.read_page(
                sel,
                start,
                self.page_size,
                result_view=result_view,
                unit_system=self.unit_system,
            )
        except NastranH5Error as exc:
            self.table_area.clear()
            with self.table_area:
                self._message(str(exc))
            return
        node = self.paths[sel.dataset]
        filtered = sel.total != node.rows
        shown = f"{start + 1}-{min(start + self.page_size, sel.total)}" if sel.total else "0"
        self.count_label.set_text(
            f"{_number(sel.total)} rows"
            + (f" of {_number(node.rows)} (filtered)" if filtered else "")
            + f" · showing {shown}"
        )
        if not result_view:
            df = df.reset_index().rename(columns={"index": "row"})
        self.table_area.clear()
        with self.table_area:
            self._table(df)
            with ui.row().classes("w-full items-center justify-end gap-2"):
                ui.pagination(
                    1,
                    pages,
                    direction_links=True,
                    value=self.page,
                    on_change=lambda e: self._go(int(e.value)),
                ).props("dense max-pages=7 boundary-links").classes("apb-h5v-pager")
                ui.select(
                    list(PAGE_SIZES),
                    value=self.page_size,
                    on_change=lambda e: self._set_page_size(int(e.value)),
                ).props("dense options-dense borderless").classes("w-20")
                ui.label("rows/page").classes("text-xs text-slate-500")

    @staticmethod
    def _table(df: pd.DataFrame) -> None:
        if df.empty and not len(df.columns):
            ui.label("Empty dataset").classes("text-sm text-slate-500")
            return
        columns, rows = table_rows(df, limit=len(df))
        ui.table(columns=columns, rows=rows, pagination=0).props(
            "dense flat bordered hide-pagination virtual-scroll"
        ).classes("w-full text-xs apb-h5v-table max-h-[60vh]")

    def _go(self, page: int) -> None:
        if page != self.page:
            self.page = page
            self._show_page()

    def _set_page_size(self, size: int) -> None:
        first = (self.page - 1) * self.page_size
        self.page_size = size
        self.page = first // size + 1
        self._show_page()

    # --------------------------------------------------------------------------- details

    def _details(self, open_: bool) -> None:
        """Attributes and columns of the selected item (H5V-04)."""
        try:
            details = h5browse.node_details(self.path or "", self.dataset, self.unit_system)
        except NastranH5Error as exc:
            self._message(str(exc))
            return
        with ui.expansion("Details", value=open_).classes("w-full apb-h5v-details").props("dense"):
            if details.title:
                ui.label(f"Result: {details.title}").classes("text-sm")
            if details.attributes:
                ui.label("Attributes").classes("text-xs font-medium text-slate-600")
                rows = [{"name": k, "value": v} for k, v in details.attributes.items()]
                ui.table(
                    columns=[
                        {"name": "name", "label": "Name", "field": "name", "align": "left"},
                        {"name": "value", "label": "Value", "field": "value", "align": "left"},
                    ],
                    rows=rows,
                    pagination=0,
                ).props("dense flat bordered hide-pagination").classes("text-xs apb-h5v-attrs")
            else:
                ui.label("No attributes").classes("text-xs text-slate-500")
            if details.columns:
                ui.label("Columns").classes("text-xs font-medium text-slate-600 pt-1")
                rows = [
                    {
                        "name": c.name,
                        "type": c.dtype + (f" {list(c.shape)}" if c.shape else ""),
                        "kind": c.kind,
                        "unit": c.unit,
                    }
                    for c in details.columns
                ]
                ui.table(
                    columns=[
                        {"name": k, "label": k.capitalize(), "field": k, "align": "left"}
                        for k in ("name", "type", "kind", "unit")
                    ],
                    rows=rows,
                    pagination=0,
                ).props("dense flat bordered hide-pagination").classes("text-xs apb-h5v-columns")

    # --------------------------------------------------------------------------- actions

    def _reload(self) -> None:
        """Read the file again; keep the selection if it still exists (H5V-12)."""
        self._initial = ""
        if self.path is not None and self.path.is_file():
            self.units = default_units(self.path, self.units)
        self._load()
        ui.notify("File read again", position="bottom-right")

    def _copy(self) -> None:
        """Copy the dataset path in the form an HDF5 block uses (H5V-10)."""
        if not self.dataset:
            return
        text = h5browse.copy_path(self.dataset)
        ui.clipboard.write(text)
        ui.notify(f"Copied {text}", position="bottom-right")

    async def _export(self) -> None:
        """Write the current view to a CSV file (H5V-11)."""
        sel = self.selection
        if sel is None or self.path is None:
            return
        if sel.total > h5browse.LARGE_EXPORT_ROWS and not await confirm(
            f"The view has {_number(sel.total)} rows. Export them all?", ok_label="Export"
        ):
            return
        name = f"{self.path.stem}_{sel.dataset.rsplit('/', 1)[-1]}.csv"
        target = await FileDialog(
            title="Export CSV", start=self.path.parent, suffix=".csv", save=True, default_name=name
        )
        if target is None:
            return
        result_view = self.result_view and bool(sel.result)
        try:
            n = await run.io_bound(
                h5browse.export_csv,
                sel,
                target,
                result_view=result_view,
                unit_system=self.unit_system,
            )
        except (NastranH5Error, OSError) as exc:
            ui.notify(f"Export failed: {exc}", type="negative", multi_line=True)
            return
        ui.notify(f"Wrote {_number(n)} rows to {target}", type="positive", position="bottom-right")


def register_route() -> None:
    """Register the viewer page at :data:`ROUTE` (call once, before ``ui.run``)."""

    @ui.page(ROUTE)
    def h5_page(
        file: str = "", dataset: str = "", subcases: str = "", ids: str = "", units: str = ""
    ) -> None:
        H5ViewerPage(file, dataset=dataset, subcases=subcases, ids=ids, units=units)
