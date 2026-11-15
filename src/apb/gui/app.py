"""The browser GUI (spec 06): library, canvas, parameter panel, run, preview and log.

Started with ``apb gui [pipeline]``. Each browser tab gets its own editor.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
from nicegui import ui

from apb import __version__
from apb.blocks.nastran.blocks import Hdf5Results
from apb.blocks.python.block import PythonBlock, PythonParams
from apb.blocks.python.scripting import ScriptError, write_skeleton
from apb.engine.events import EventKind, RunEvent
from apb.engine.graph import ValidationIssue, prepare
from apb.engine.registry import BlockRegistry, default_registry
from apb.engine.variables import VariableError, resolve
from apb.gui.canvas import NodeCanvas, Payload, add_canvas_css
from apb.gui.editor import ConnectionKey, EditError, EditorState
from apb.gui.filedialog import FileDialog, confirm
from apb.gui.forms import ParamForm, suffix_hint
from apb.gui.nastran_panel import PEEK_ROWS, Hdf5ResultsPanel
from apb.gui.preview import show_result, show_value
from apb.gui.runner import EVENT_STATES, RunController, summary
from apb.units import UnitSystem

HEADER_HEIGHT = 52
MAX_LOG_LINES = 500


class EditorPage:
    """One editor in one browser tab."""

    def __init__(self, registry: BlockRegistry, initial: Path | None) -> None:
        """Build the page.

        Args:
            registry: Available block types.
            initial: Pipeline file to open, if any.
        """
        self.state = EditorState(registry)
        self.runner = RunController(self.state, self._on_run_event)
        self.selected: str | None = None
        self._offset = 0
        self._states_dirty = True
        self._last_folder = initial.parent if initial else Path.cwd()
        add_canvas_css()
        ui.add_css(f"""
            .apb-main {{ height: calc(100vh - {HEADER_HEIGHT}px); }}
            .q-page {{ padding: 0; }}
            .apb-log-line {{ font-family: monospace; font-size: 11.5px; white-space: pre-wrap; }}
        """)
        self._build_header()
        self._build_library(registry)
        if initial is not None:
            self._open_path(initial, redraw=False)
        self._build_panel()
        with (
            ui.element("div").classes("apb-main w-full"),
            ui.splitter(horizontal=True, value=66).classes("w-full h-full") as split,
        ):
            with split.before:
                self.canvas = NodeCanvas(self.state.graph_spec())
            with split.after, ui.row().classes("w-full h-full no-wrap gap-0"):
                with ui.column().classes("w-3/5 h-full p-2 gap-1 border-r overflow-auto"):
                    self.preview_title = ui.label("Preview").classes(
                        "text-sm font-medium text-slate-700"
                    )
                    self.preview = ui.column().classes("w-full gap-1")
                with ui.column().classes("w-2/5 h-full p-2 gap-1"):
                    with ui.row().classes("w-full items-center"):
                        ui.label("Log").classes("text-sm font-medium text-slate-700")
                        ui.space()
                        ui.button(icon="delete_sweep", on_click=self._clear_log).props(
                            "flat dense size=sm"
                        ).tooltip("Clear log")
                    with ui.scroll_area().classes("w-full grow border rounded") as self.log_area:
                        self.log = ui.column().classes("w-full gap-0")
        self.canvas.on_event("block_moved", self._on_moved)
        self.canvas.on_event("block_selected", self._on_selected)
        self.canvas.on_event("block_removed", self._on_removed)
        self.canvas.on_event("connection_created", self._on_connected)
        self.canvas.on_event("connection_removed", self._on_disconnected)
        self.state.on_change(self._on_state_change)
        ui.timer(0.1, self.runner.drain)
        ui.timer(0.5, self._refresh_states)
        self._update_title()
        self._show_preview()

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
            with ui.button("Pipeline", icon="tune").props("flat color=white dense"), ui.menu():
                ui.menu_item("Variables and settings…", on_click=self.edit_pipeline_settings)
            with ui.button("View", icon="visibility").props("flat color=white dense"), ui.menu():
                ui.menu_item("Fit to window", on_click=lambda: self.canvas.fit_view())
                ui.menu_item("Zoom in", on_click=lambda: self.canvas.zoom(1))
                ui.menu_item("Zoom out", on_click=lambda: self.canvas.zoom(-1))
                ui.menu_item("Reset view", on_click=lambda: self.canvas.reset_view())
            ui.separator().props("vertical dark").classes("mx-1")
            self.run_button = ui.button(
                "Run", icon="play_arrow", color="positive", on_click=lambda: self.run_pipeline(None)
            ).props("dense")
            self.stop_button = ui.button(
                "Stop", icon="stop", color="negative", on_click=self.runner.stop
            ).props("dense")
            self.stop_button.disable()
            ui.space()
            self.title = ui.label().classes("text-sm opacity-90")
            ui.label(f"v{__version__}").classes("text-xs opacity-50")

    def _build_library(self, registry: BlockRegistry) -> None:
        groups: dict[str, list[Any]] = defaultdict(list)
        for cls in registry:
            groups[cls.category].append(cls)
        with ui.left_drawer(value=True, bordered=True).classes("p-2 bg-white").props("width=240"):
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
        with ui.right_drawer(value=True, bordered=True).classes("p-3 bg-white").props("width=340"):
            self.panel = ui.column().classes("w-full gap-2")
        self._show_panel()

    # ---- block panel ----------------------------------------------------------------------

    def _show_panel(self) -> None:
        self.panel.clear()
        with self.panel:
            if self.selected is None or not any(
                b.id == self.selected for b in self.state.pipeline.blocks
            ):
                self.selected = None
                ui.label("Block").classes("font-medium text-slate-700")
                ui.label("Select a block on the canvas to edit its parameters.").classes(
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
            with ui.row().classes("w-full items-center no-wrap"):
                ui.label(cls.title if cls else "Unknown block").classes(
                    "font-medium text-slate-700"
                )
                ui.space()
                ui.button(icon="play_arrow", on_click=lambda: self.run_pipeline(block.id)).props(
                    "flat dense color=positive"
                ).tooltip("Run to here")
                ui.button(icon="delete", on_click=lambda: self.delete_block(block.id)).props(
                    "flat dense color=negative"
                ).tooltip("Delete block")
            label = ui.input("Label", value=block.label).props("dense outlined").classes("w-full")
            label.on("blur", lambda: self._rename(block.id, label.value))
            label.on("keydown.enter", lambda: self._rename(block.id, label.value))
            ui.label(f"{block.type_id} · id {block.id}").classes("text-xs text-slate-500")
            if cls is None:
                ui.label(
                    "This block type is not installed; its parameters cannot be edited."
                ).classes("text-sm text-red-700")
                return
            if cls.description:
                ui.label(cls.description).classes("text-xs text-slate-600")
            self.issues = ui.column().classes("w-full gap-0")
            self._show_issues()
            ui.label("Parameters").classes("text-xs font-medium text-slate-600 mt-1")
            if cls is Hdf5Results:  # own panel with the result browser (spec 02 §3.7)
                Hdf5ResultsPanel(
                    block.params,
                    on_change=lambda name, value: self._set_param(block.id, name, value),
                    pick_file=self._pick_file,
                    resolve=self._resolve_variables,
                    base_dir=self.state.base_dir,
                    default_units=self.state.pipeline.settings.default_unit_system,
                    show_peek=lambda table, error: self._show_peek(block.id, table, error),
                    log=lambda text: self._log(f"  {self._block_name(block.id)}: {text}"),
                ).build()
            else:
                ParamForm(
                    cls.Params,
                    block.params,
                    on_change=lambda name, value: self._set_param(block.id, name, value),
                    pick_file=self._pick_file,
                ).build()
            if cls is PythonBlock:
                self._python_tools(block.id)
            inputs, outputs = self.state.ports(block)
            for title, ports in (("Inputs", inputs), ("Outputs", outputs)):
                if ports:
                    ui.label(title).classes("text-xs font-medium text-slate-600 mt-2")
                    for p in ports:
                        extra = " · several" if p.multiple else ""
                        extra += " · optional" if p.optional else ""
                        ui.label(f"{p.display_name} — {p.type}{extra}").classes("text-xs")

    def _block_issues(self, block_id: str) -> list[ValidationIssue]:
        graph = prepare(self.state.pipeline, self.state.registry)
        return [i for i in graph.issues if i.block_id == block_id]

    def _show_issues(self) -> None:
        if self.selected is None:
            return
        self.issues.clear()
        with self.issues:
            for issue in self._block_issues(self.selected):
                with ui.row().classes("items-start no-wrap gap-1"):
                    ui.icon("error_outline", color="red-7", size="xs").classes("mt-0.5")
                    ui.label(issue.message).classes("text-xs text-red-700")

    def _python_tools(self, block_id: str) -> None:
        block = self.state.pipeline.block(block_id)
        try:
            params = PythonParams.model_validate(block.params)
        except Exception:
            return
        if not params.script:
            return
        path = Path(params.script)
        path = path if path.is_absolute() else self.state.base_dir / path
        with ui.row().classes("gap-1 mt-1"):
            if path.is_file():
                ui.button(
                    "Edit script", icon="code", on_click=lambda: self._edit_script(path)
                ).props("dense outline")
            else:

                def create() -> None:
                    try:
                        write_skeleton(path, params.inputs, params.outputs)
                    except ScriptError as exc:
                        ui.notify(str(exc), type="warning")
                        return
                    ui.notify(f"Created {path.name}", type="positive")
                    self._show_panel()

                ui.button("Create script", icon="note_add", on_click=create).props(
                    "dense outline"
                ).tooltip("Create the file with a run() matching the ports")

    async def _edit_script(self, path: Path) -> None:
        with ui.dialog().props("maximized") as dialog, ui.card().classes("w-full h-full"):
            header = ui.row().classes("w-full items-center")
            editor = ui.codemirror(path.read_text(encoding="utf-8"), language="Python").classes(
                "w-full grow border rounded"
            )
            with header:
                ui.label(str(path)).classes("font-medium")
                ui.space()
                ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
                ui.button("Save", icon="save", on_click=lambda: dialog.submit(editor.value))
        text = await dialog
        dialog.delete()
        if text is not None:
            path.write_text(text, encoding="utf-8", newline="\n")
            ui.notify(f"Saved {path.name}", type="positive")
            self._states_dirty = True

    async def _pick_file(self, hint: str) -> str | None:
        suffix = suffix_hint(hint)
        path = await FileDialog(
            title="Choose file", start=self.state.base_dir, suffix=suffix, save=suffix == ".py"
        )
        return self.state.relative_path(path) if path else None

    def _set_param(self, block_id: str, name: str, value: Any) -> None:
        block = self.state.pipeline.block(block_id)
        ports_before = self.state.ports(block)
        removed = self.state.set_param(block_id, name, value)
        self.runner.mark_edited(block_id)
        if self.state.ports(block) != ports_before:
            self.canvas.replace_node(
                self.state.node_spec(block), self.state.connections_of(block_id)
            )
            if removed:
                ui.notify(
                    f"{len(removed)} connection(s) removed: their ports changed", type="warning"
                )
        self._show_issues()

    # ---- title, states, log ---------------------------------------------------------------

    def _on_state_change(self) -> None:
        self._states_dirty = True
        self._update_title()

    def _update_title(self) -> None:
        self.title.text = self.state.display_name
        ui.page_title(f"{self.state.display_name} — Analysis Pipeline Builder")
        # Ask the browser to warn before closing the tab with unsaved changes (GUI-12).
        flag = "true" if self.state.dirty else "false"
        ui.run_javascript(
            f"window.apbDirty = {flag}; window.onbeforeunload = "
            "() => (window.apbDirty ? true : undefined);"
        )

    def _refresh_states(self) -> None:
        if self._states_dirty and not self.runner.running:
            self._states_dirty = False
            self.canvas.set_states(self.runner.display_states())

    def _log(self, text: str, level: str = "info", block_id: str | None = None) -> None:
        colours = {
            "info": "text-slate-700",
            "ok": "text-green-700",
            "warn": "text-amber-700",
            "error": "text-red-700",
        }
        with self.log:
            line = ui.label(text).classes(f"apb-log-line {colours.get(level, '')}")
            if block_id:
                line.classes("cursor-pointer hover:bg-slate-100")
                line.on("click", lambda b=block_id: self.select_block(b))
        children = list(self.log)
        for child in children[:-MAX_LOG_LINES]:
            child.delete()
        self.log_area.scroll_to(percent=1.0)

    def _clear_log(self) -> None:
        self.log.clear()

    def _block_name(self, block_id: str | None) -> str:
        if block_id is None:
            return ""
        try:
            block = self.state.pipeline.block(block_id)
        except KeyError:
            return block_id
        return block.label or block_id

    def _on_run_event(self, event: RunEvent) -> None:
        if event.block_id and event.kind in EVENT_STATES:
            self.canvas.set_states({event.block_id: str(EVENT_STATES[event.kind])})
        name = self._block_name(event.block_id)
        match event.kind:
            case EventKind.RUN_STARTED:
                self._log(f"Run started ({event.message})")
            case EventKind.BLOCK_FINISHED | EventKind.BLOCK_CACHED:
                self._log(f"✔ {name}: {event.message}", "ok", event.block_id)
            case EventKind.BLOCK_FAILED:
                self._log(f"✘ {name}: {event.message}", "error", event.block_id)
            case EventKind.BLOCK_SKIPPED:
                self._log(f"- {name}: skipped ({event.message})", "warn", event.block_id)
            case EventKind.LOG:
                self._log(f"  {name}: {event.message}", "info", event.block_id)
            case _:
                pass

    # ---- selection and preview ------------------------------------------------------------

    def select_block(self, block_id: str) -> None:
        """Select a block (e.g. from the log) and show its panel and preview."""
        self.selected = block_id
        self.canvas.select_node(block_id)
        self._show_panel()
        self._show_preview()

    def _show_preview(self) -> None:
        self.preview.clear()
        if self.selected is None:
            self.preview_title.text = "Preview"
            with self.preview:
                ui.label("Select a block to see its results after a run.").classes(
                    "text-sm text-slate-500"
                )
            return
        self.preview_title.text = f"Preview — {self._block_name(self.selected)}"
        with self.preview:
            show_result(self.runner.result(self.selected))

    def _resolve_variables(self, value: Any) -> Any:
        """Replace ``${variable}`` references; undefined variables are left as they are."""
        try:
            return resolve(value, self.state.pipeline.variables)
        except VariableError:
            return value

    def _show_peek(self, block_id: str, table: pd.DataFrame | None, error: str) -> None:
        """Show a preview read straight from a result file, before the block has run.

        Args:
            block_id: Block the preview belongs to (ignored if it is no longer selected).
            table: The first rows of the result, or ``None`` if reading failed.
            error: Why reading failed.
        """
        if self.selected != block_id:
            return
        self.preview.clear()
        self.preview_title.text = f"Preview — {self._block_name(block_id)}"
        with self.preview:
            ui.label(
                f"Preview from file (at most the first {PEEK_ROWS} rows) — not yet run"
            ).classes("text-xs text-amber-700 apb-peek")
            if table is None:
                ui.label(error).classes("text-sm text-red-700 whitespace-pre-wrap")
            else:
                show_value("table", table)

    # ---- canvas events --------------------------------------------------------------------

    def _reload_canvas(self) -> None:
        graph = self.state.graph_spec()
        self.canvas.props["graph"] = graph  # also correct after a browser reconnect
        self.canvas.load_graph(graph)
        self._states_dirty = True

    def _on_moved(self, p: Payload) -> None:
        self.state.move_block(str(p["id"]), float(p["x"]), float(p["y"]))

    def _on_selected(self, p: Payload) -> None:
        block_id = p.get("id")
        new = str(block_id) if block_id else None
        if new == self.selected:
            return
        self.selected = new
        self._show_panel()
        self._show_preview()

    def _on_removed(self, p: Payload) -> None:
        block_id = str(p["id"])
        self.state.remove_block(block_id)
        if self.selected == block_id:
            self.selected = None
            self._show_preview()
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
        self.runner.mark_edited(key.target_block)
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
        self.runner.mark_edited(str(p["target_block"]))
        self._show_panel()

    # ---- run --------------------------------------------------------------------------------

    async def run_pipeline(self, until: str | None) -> None:
        """Run the whole pipeline, or the blocks needed for ``until`` (GUI-08)."""
        if self.runner.running:
            return
        if self.state.path is None:
            ui.notify(
                "The pipeline is not saved: relative file paths refer to the folder "
                f"{self.state.base_dir}",
                type="info",
            )
        self.run_button.disable()
        self.stop_button.enable()
        try:
            outcome = await self.runner.run(until)
        finally:
            self.run_button.enable()
            self.stop_button.disable()
        if isinstance(outcome, list):
            if not outcome:
                return
            self._log(f"Cannot run: {len(outcome)} problem(s)", "error")
            for issue in outcome:
                self._log(
                    f"  {self._block_name(issue.block_id) or 'Pipeline'}: {issue.message}",
                    "error",
                    issue.block_id,
                )
            self.canvas.set_states({i.block_id: "error" for i in outcome if i.block_id})
            ui.notify("The pipeline has problems: see the log", type="negative")
            return
        text, kind = summary(outcome)
        self._log(text, "ok" if kind == "positive" else "warn" if kind == "warning" else "error")
        ui.notify(text, type=kind)
        self._states_dirty = True
        self._show_preview()

    # ---- pipeline settings -----------------------------------------------------------------

    async def edit_pipeline_settings(self) -> None:
        """Dialog for pipeline variables, default unit system and metadata (GUI-14)."""
        pipeline = self.state.pipeline
        rows = [
            [k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v]
            for k, v in pipeline.variables.items()
        ]
        with ui.dialog() as dialog, ui.card().classes("w-[40rem] max-w-full"):
            ui.label("Pipeline variables and settings").classes("text-lg font-medium")
            ui.label(
                "Variables are used in block parameters as ${name}, and can be "
                "overridden on the command line with --set name=value."
            ).classes("text-xs text-slate-500")
            container = ui.column().classes("w-full gap-1")

            def remove(row: list[str]) -> None:
                rows.remove(row)
                render()

            def add() -> None:
                rows.append(["", ""])
                render()

            def render() -> None:
                container.clear()
                with container:
                    for row in rows:
                        with ui.row().classes("w-full no-wrap gap-1 items-center"):
                            ui.input("Name", value=row[0]).props("dense outlined").classes(
                                "w-1/3"
                            ).on_value_change(lambda e, r=row: r.__setitem__(0, e.value))
                            ui.input("Value", value=row[1]).props("dense outlined").classes(
                                "grow"
                            ).on_value_change(lambda e, r=row: r.__setitem__(1, e.value))
                            ui.button(icon="close", on_click=lambda r=row: remove(r)).props(
                                "flat dense size=sm"
                            )
                    ui.button("Add variable", icon="add", on_click=add).props("flat dense")

            render()
            unit = (
                ui.select(
                    {u.value: u.value for u in UnitSystem},
                    value=str(pipeline.settings.default_unit_system),
                    label="Default unit system",
                )
                .props("dense outlined")
                .classes("w-full")
            )
            title = (
                ui.input("Title", value=pipeline.metadata.title)
                .props("dense outlined")
                .classes("w-full")
            )
            author = (
                ui.input("Author", value=pipeline.metadata.author)
                .props("dense outlined")
                .classes("w-full")
            )
            description = (
                ui.textarea("Description", value=pipeline.metadata.description)
                .props("dense outlined autogrow")
                .classes("w-full")
            )
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
                ui.button("Apply", on_click=lambda: dialog.submit(True))
        ok = await dialog
        dialog.delete()
        if not ok:
            return
        variables: dict[str, Any] = {}
        for name, text in rows:
            name = (name or "").strip()
            if not name:
                continue
            if not name.isidentifier():
                ui.notify(
                    f"'{name}' is not a valid variable name (letters, digits, _)", type="warning"
                )
                return
            try:
                variables[name] = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                variables[name] = text
        self.state.set_variables(variables)
        self.state.set_settings(
            default_unit_system=UnitSystem(unit.value),
            title=title.value or "",
            author=author.value or "",
            description=description.value or "",
        )
        self._show_panel()

    # ---- block actions ---------------------------------------------------------------------

    async def add_block(self, type_id: str) -> None:
        """Add a block in the middle of the visible canvas area."""
        x, y = await self.canvas.view_center()
        self._offset = (self._offset + 1) % 8
        block = self.state.add_block(type_id, x + 24 * self._offset, y + 24 * self._offset)
        self.canvas.add_node(self.state.node_spec(block))
        self.select_block(block.id)  # open its parameter form right away

    def delete_block(self, block_id: str) -> None:
        """Delete a block from the panel button."""
        self.state.remove_block(block_id)
        self.canvas.remove_node(block_id)
        self.selected = None
        self._show_panel()
        self._show_preview()

    def _rename(self, block_id: str, label: str) -> None:
        if not label.strip():
            return
        self.state.rename_block(block_id, label.strip())
        self.canvas.update_node(self.state.node_spec(self.state.pipeline.block(block_id)))
        self.preview_title.text = f"Preview — {label.strip()}"

    # ---- files -------------------------------------------------------------------------------

    async def _discard_ok(self) -> bool:
        return not self.state.dirty or await confirm("The pipeline has unsaved changes.")

    async def new(self) -> None:
        """File > New."""
        if await self._discard_ok():
            self.state.new()
            self._after_load()

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
        if any(b.type_id == PythonBlock.type_id for b in self.state.pipeline.blocks):
            ui.notify(
                "This pipeline contains Python blocks: only run pipelines from sources you trust.",
                type="info",
                position="top",
                timeout=8000,
            )
        if redraw:
            self._after_load()

    def _after_load(self) -> None:
        self.selected = None
        self.runner.results.clear()
        self.runner.executor.clear_cache()
        self._reload_canvas()
        self._show_panel()
        self._show_preview()

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
        self._states_dirty = True
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
