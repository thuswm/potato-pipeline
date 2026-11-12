"""The browser GUI (spec 06): page layout, library, canvas, block panel, files.

Started with ``apb gui [pipeline]``. Each browser tab gets its own editor.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from nicegui import ui

from apb import __version__
from apb.engine.registry import BlockRegistry, default_registry
from apb.gui.canvas import NodeCanvas, Payload, add_canvas_css
from apb.gui.editor import ConnectionKey, EditError, EditorState
from apb.gui.filedialog import FileDialog, confirm

HEADER_HEIGHT = 52


class EditorPage:
    """One editor in one browser tab."""

    def __init__(self, registry: BlockRegistry, initial: Path | None) -> None:
        """Build the page.

        Args:
            registry: Available block types.
            initial: Pipeline file to open, if any.
        """
        self.state = EditorState(registry)
        self.selected: str | None = None
        self._offset = 0
        self._last_folder = initial.parent if initial else Path.cwd()
        add_canvas_css()
        ui.add_css(f"""
            .apb-main {{ height: calc(100vh - {HEADER_HEIGHT}px); }}
            .q-page {{ padding: 0; }}
        """)
        self._build_header()
        self._build_library(registry)
        if initial is not None:
            self._open_path(initial, redraw=False)
        self._build_panel()
        with ui.element("div").classes("apb-main w-full"):
            self.canvas = NodeCanvas(self.state.graph_spec())
        self.canvas.on_event("block_moved", self._on_moved)
        self.canvas.on_event("block_selected", self._on_selected)
        self.canvas.on_event("block_removed", self._on_removed)
        self.canvas.on_event("connection_created", self._on_connected)
        self.canvas.on_event("connection_removed", self._on_disconnected)
        self.state.on_change(self._update_title)
        self._update_title()

    # ---- layout -------------------------------------------------------------------------

    def _build_header(self) -> None:
        with ui.header(elevated=True).classes("items-center gap-2 py-1 px-3 bg-slate-800"):
            ui.icon("account_tree", size="sm")
            ui.label("Analysis Pipeline Builder").classes("font-medium")
            with ui.button("File", icon="folder").props("flat color=white dense"), ui.menu():
                ui.menu_item("New", on_click=self.new)
                ui.menu_item("Open…", on_click=self.open)
                ui.separator()
                ui.menu_item("Save", on_click=self.save)
                ui.menu_item("Save as…", on_click=self.save_as)
            with ui.button("View", icon="visibility").props("flat color=white dense"), ui.menu():
                ui.menu_item("Fit to window", on_click=lambda: self.canvas.fit_view())
                ui.menu_item("Zoom in", on_click=lambda: self.canvas.zoom(1))
                ui.menu_item("Zoom out", on_click=lambda: self.canvas.zoom(-1))
                ui.menu_item("Reset view", on_click=lambda: self.canvas.reset_view())
            ui.space()
            self.title = ui.label().classes("text-sm opacity-90")
            ui.label(f"v{__version__}").classes("text-xs opacity-50")

    def _build_library(self, registry: BlockRegistry) -> None:
        groups: dict[str, list[Any]] = defaultdict(list)
        for cls in registry:
            groups[cls.category].append(cls)
        with ui.left_drawer(value=True, bordered=True).classes("p-2 bg-white").props("width=250"):
            ui.label("Block library").classes("font-medium text-slate-700")
            search = (
                ui.input(placeholder="Search…").props("dense clearable outlined").classes("w-full")
            )
            entries: list[tuple[ui.item, str]] = []
            expansions: list[tuple[ui.expansion, list[str]]] = []
            for category in sorted(groups):
                with (
                    ui.expansion(category, value=True)
                    .classes("w-full")
                    .props("dense header-class='text-sm font-medium text-slate-600'") as exp
                ):
                    texts = []
                    for cls in groups[category]:
                        item = (
                            ui.item(on_click=lambda c=cls: self.add_block(c.type_id))
                            .props("dense clickable")
                            .classes("text-sm rounded")
                        )
                        with item:
                            ui.label(cls.title)
                        if cls.description:
                            item.tooltip(cls.description)
                        text = f"{cls.title} {cls.type_id} {cls.description} {category}".lower()
                        entries.append((item, text))
                        texts.append(text)
                expansions.append((exp, texts))

            def filter_library() -> None:
                term = (search.value or "").lower().strip()
                for item, text in entries:
                    item.set_visibility(term in text)
                for exp, texts in expansions:
                    exp.set_visibility(any(term in t for t in texts))
                    if term:
                        exp.value = True

            search.on_value_change(filter_library)
            ui.label(
                "Click a block to add it. Drag from an output dot to an input dot to "
                "connect. Select a block or connection and press Delete to remove it."
            ).classes("text-xs text-slate-500 mt-3")

    def _build_panel(self) -> None:
        with ui.right_drawer(value=True, bordered=True).classes("p-3 bg-white").props("width=300"):
            ui.label("Block").classes("font-medium text-slate-700")
            self.panel = ui.column().classes("w-full gap-2")
        self._show_panel()

    def _show_panel(self) -> None:
        self.panel.clear()
        with self.panel:
            if self.selected is None:
                ui.label("Select a block on the canvas to see its details.").classes(
                    "text-sm text-slate-500"
                )
                n_blocks = len(self.state.pipeline.blocks)
                n_conn = len(self.state.pipeline.connections)
                ui.label(f"{n_blocks} blocks, {n_conn} connections").classes(
                    "text-xs text-slate-400"
                )
                return
            block = self.state.pipeline.block(self.selected)
            cls = self.state.block_class(block)
            label = ui.input("Label", value=block.label).classes("w-full")
            label.on("blur", lambda: self._rename(block.id, label.value))
            label.on("keydown.enter", lambda: self._rename(block.id, label.value))
            ui.label(f"Type: {cls.title if cls else 'not installed'}").classes("text-sm")
            ui.label(f"{block.type_id} · id {block.id}").classes("text-xs text-slate-500")
            if cls and cls.description:
                ui.label(cls.description).classes("text-xs text-slate-600")
            inputs, outputs = self.state.ports(block)
            for title, ports in (("Inputs", inputs), ("Outputs", outputs)):
                if ports:
                    ui.label(title).classes("text-xs font-medium text-slate-600 mt-2")
                    for p in ports:
                        extra = " · several" if p.multiple else ""
                        extra += " · optional" if p.optional else ""
                        ui.label(f"{p.display_name} — {p.type}{extra}").classes("text-xs")
            ui.label("Parameters are edited here in the next version.").classes(
                "text-xs text-slate-400 mt-2"
            )
            ui.button(
                "Delete block",
                icon="delete",
                color="negative",
                on_click=lambda: self.delete_block(block.id),
            ).props("flat dense").classes("mt-2")

    def _update_title(self) -> None:
        self.title.text = self.state.display_name
        ui.page_title(f"{self.state.display_name} — Analysis Pipeline Builder")
        # Ask the browser to warn before closing the tab with unsaved changes (GUI-12).
        flag = "true" if self.state.dirty else "false"
        ui.run_javascript(
            f"window.apbDirty = {flag}; window.onbeforeunload = "
            "() => (window.apbDirty ? true : undefined);"
        )

    # ---- canvas events ------------------------------------------------------------------

    def _reload_canvas(self) -> None:
        graph = self.state.graph_spec()
        self.canvas.props["graph"] = graph  # also correct after a browser reconnect
        self.canvas.load_graph(graph)

    def _on_moved(self, p: Payload) -> None:
        self.state.move_block(str(p["id"]), float(p["x"]), float(p["y"]))

    def _on_selected(self, p: Payload) -> None:
        block_id = p.get("id")
        self.selected = str(block_id) if block_id else None
        self._show_panel()

    def _on_removed(self, p: Payload) -> None:
        block_id = str(p["id"])
        self.state.remove_block(block_id)
        if self.selected == block_id:
            self.selected = None
        self._show_panel()

    def _on_connected(self, p: Payload) -> None:
        key = ConnectionKey(
            str(p["source_block"]),
            str(p["source_port"]),
            str(p["target_block"]),
            str(p["target_port"]),
        )
        try:
            self.state.connect(key)
        except (EditError, KeyError) as exc:
            self.canvas.disconnect(p)
            ui.notify(str(exc), type="warning", position="top")
        self._show_panel()

    def _on_disconnected(self, p: Payload) -> None:
        if p.get("source_block") is None:
            return
        self.state.disconnect(
            ConnectionKey(
                str(p["source_block"]),
                str(p["source_port"]),
                str(p["target_block"]),
                str(p["target_port"]),
            )
        )
        self._show_panel()

    # ---- actions ------------------------------------------------------------------------

    async def add_block(self, type_id: str) -> None:
        """Add a block in the middle of the visible canvas area."""
        x, y = await self.canvas.view_center()
        self._offset = (self._offset + 1) % 8
        block = self.state.add_block(type_id, x + 24 * self._offset, y + 24 * self._offset)
        self.canvas.add_node(self.state.node_spec(block))
        self._show_panel()

    def delete_block(self, block_id: str) -> None:
        """Delete a block from the panel button."""
        self.state.remove_block(block_id)
        self.canvas.remove_node(block_id)
        self.selected = None
        self._show_panel()

    def _rename(self, block_id: str, label: str) -> None:
        if not label.strip():
            return
        self.state.rename_block(block_id, label.strip())
        self.canvas.update_node(self.state.node_spec(self.state.pipeline.block(block_id)))

    async def _discard_ok(self) -> bool:
        return not self.state.dirty or await confirm("The pipeline has unsaved changes.")

    async def new(self) -> None:
        """File > New."""
        if await self._discard_ok():
            self.state.new()
            self.selected = None
            self._reload_canvas()
            self._show_panel()

    async def open(self) -> None:
        """File > Open."""
        if not await self._discard_ok():
            return
        path = await FileDialog(title="Open pipeline", start=self._last_folder)
        if path:
            self._open_path(path)

    def _open_path(self, path: Path, *, redraw: bool = True) -> None:
        try:
            self.state.open(path)
        except EditError as exc:
            ui.notify(str(exc), type="negative", position="top", multi_line=True)
            return
        self._last_folder = path.parent
        unknown = sorted(
            {b.type_id for b in self.state.pipeline.blocks if b.type_id not in self.state.registry}
        )
        if unknown:
            ui.notify(f"Block types not installed: {', '.join(unknown)}", type="warning")
        if any(b.type_id == "python.script" for b in self.state.pipeline.blocks):
            ui.notify(
                "This pipeline contains Python blocks: only run pipelines from sources you trust.",
                type="info",
                position="top",
                timeout=8000,
            )
        self.selected = None
        if redraw:
            self._reload_canvas()
            self._show_panel()

    async def save(self) -> None:
        """File > Save."""
        if self.state.path is None:
            await self.save_as()
            return
        self._save_to(None)

    async def save_as(self) -> None:
        """File > Save as."""
        name = self.state.path.name if self.state.path else "pipeline.apb.json"
        path = await FileDialog(
            title="Save pipeline as", start=self._last_folder, save=True, default_name=name
        )
        if path:
            if (
                path.exists()
                and path != self.state.path
                and not await confirm(f"{path.name} exists. Replace it?", ok_label="Replace")
            ):
                return
            self._save_to(path)

    def _save_to(self, path: Path | None) -> None:
        try:
            written = self.state.save(path)
        except EditError as exc:
            ui.notify(str(exc), type="negative", position="top")
            return
        self._last_folder = written.parent
        ui.notify(f"Saved {written.name}", type="positive", position="bottom-right")


def run_gui(pipeline: Path | None = None, *, port: int = 8765, show: bool = True) -> None:
    """Start the GUI server on localhost and open the browser (GUI-01).

    Args:
        pipeline: Pipeline file to open in the editor.
        port: Local port.
        show: Open the default browser.
    """
    registry = default_registry()
    initial = pipeline.resolve() if pipeline else None

    def root() -> None:
        EditorPage(registry, initial)

    ui.run(
        root,
        host="127.0.0.1",
        port=port,
        title="Analysis Pipeline Builder",
        show=show,
        reload=False,
        favicon="🔧",
        show_welcome_message=True,
    )
