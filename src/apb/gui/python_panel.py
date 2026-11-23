"""Property panel of the Python block: script view, templates, static check (spec 04 §3.5).

Shows the script of the block read-only with the result of the static check, offers the
start choices (existing file / new from template) while there is no script, and never
overwrites a script that exists (PY-13): there is no replace action.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from nicegui import ui
from pydantic import ValidationError

from apb.blocks.python.block import PythonParams
from apb.blocks.python.scripting import (
    PortSpec,
    ScriptError,
    check_script_source,
    modified_text,
    ports_to_params,
    read_script,
)
from apb.blocks.python.templates import create_script, template_titles
from apb.engine.variables import VariableError
from apb.gui.filedialog import FileDialog
from apb.gui.forms import ParamForm, PickFile, short_path

#: Parameters whose change makes the script section outdated.
_WATCHED = {"script", "inline_code", "inputs", "outputs"}


def declared_ports(raw: Any) -> list[PortSpec]:
    """Return the valid ports from stored parameters, skipping malformed entries."""
    ports: list[PortSpec] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            ports.append(PortSpec.model_validate(item))
        except ValidationError:
            continue
    return ports


class PythonPanel:
    """Builds the panel of one Python block.

    Args:
        params: Stored parameters of the block (kept up to date by ``on_change``).
        on_change: Called with ``(name, value)`` to store a parameter.
        pick_file: Async callback for the file field (see :class:`ParamForm`).
        resolve: Replaces ``${variable}`` references in a value.
        base_dir: Folder that relative paths refer to.
        relative_path: Expresses a path relative to the pipeline folder when possible.
        edit_script: Async callback opening the script in the in-app editor.
        open_in_editor: Opens a file with the program the system has for it.
        rebuild: Rebuilds the whole panel (after ports changed).
    """

    def __init__(
        self,
        params: dict[str, Any],
        *,
        on_change: Callable[[str, Any], None],
        pick_file: PickFile | None,
        resolve: Callable[[Any], Any],
        base_dir: Path,
        relative_path: Callable[[Path], str],
        edit_script: Callable[[Path], Awaitable[None]],
        open_in_editor: Callable[[Path], None],
        rebuild: Callable[[], None],
    ) -> None:
        self.params = params
        self.on_change = on_change
        self.resolve = resolve
        self.base_dir = base_dir
        self.relative_path = relative_path
        self.edit_script = edit_script
        self.open_in_editor = open_in_editor
        self.rebuild = rebuild
        self.form = ParamForm(PythonParams, params, self._changed, pick_file)

    # ---- parameters -----------------------------------------------------------------------

    def _changed(self, name: str, value: Any) -> None:
        self.on_change(name, value)
        if name in _WATCHED:
            self._render_script()

    def _script_path(self) -> Path | None:
        """Return the absolute path of the script, or ``None`` if the block has none."""
        try:
            value = self.resolve(self.params.get("script") or "")
        except VariableError:
            return None
        if not value:
            return None
        path = Path(str(value)).expanduser()
        return path if path.is_absolute() else (self.base_dir / path).resolve()

    def _inputs(self) -> list[PortSpec]:
        return declared_ports(self.params.get("inputs"))

    def _outputs(self) -> list[PortSpec]:
        return declared_ports(self.params.get("outputs"))

    # ---- widgets --------------------------------------------------------------------------

    def build(self) -> None:
        """Create the side panel in the current NiceGUI context (spec 11 §4.7).

        The ports are edited in the panel's *Ports* section (:meth:`build_ports`). Inline
        code is under *Advanced* unless the block uses it instead of a script file.
        """
        self.form.field("script")
        self.script_box = ui.column().classes("w-full gap-1 apb-script")
        self._render_script()
        uses_inline = bool(str(self.params.get("inline_code") or "").strip()) and not (
            self.params.get("script")
        )
        if uses_inline:
            self.form.field("inline_code")
        else:
            self.form.advanced(["inline_code"])

    def build_ports(self) -> None:
        """The input and output port editors (in the *Ports* section of the panel)."""
        self.form.field("inputs")
        self.form.field("outputs")

    def _render_script(self) -> None:
        """(Re)build the code view or the start choices."""
        self.script_box.clear()
        path = self._script_path()
        inline = str(self.params.get("inline_code") or "").strip()
        with self.script_box:
            if path is not None and path.is_file():
                self._code_view(path)
                return
            if path is not None:
                ui.label(f"File not found: {path}").classes("text-xs text-red-700 apb-script-error")
            elif inline:
                ui.label("This block uses the inline code below.").classes("text-xs text-slate-500")
                return
            else:
                ui.label("The block has no script yet. Start with:").classes(
                    "text-xs text-slate-600"
                )
            with ui.row().classes("gap-1"):
                ui.button(
                    "Use existing file…", icon="folder_open", on_click=self._use_existing
                ).props("dense outline")
                ui.button(
                    "New from template…", icon="note_add", on_click=self._new_from_template
                ).props("dense outline")

    def _code_view(self, path: Path) -> None:
        """Show file information, the check result, the code and the buttons (PY-11, PY-14)."""
        try:
            source = read_script(path)
        except ScriptError as exc:
            ui.label(str(exc)).classes("text-xs text-red-700 apb-script-error")
            return
        info = f"{short_path(path.parent, self.base_dir) or '.'} · changed {modified_text(path)}"
        ui.label(info).classes("text-[10px] text-slate-500 apb-script-info").tooltip(str(path))
        check = check_script_source(source, self._inputs(), path.name)
        with ui.row().classes("w-full items-start no-wrap gap-1 apb-script-check"):
            if check.ok:
                ui.icon("check_circle", color="green-7", size="xs").classes("mt-0.5")
                ui.label("run() matches the ports").classes("text-xs text-green-800 grow")
            else:
                ui.icon("error_outline", color="red-7", size="xs").classes("mt-0.5")
                ui.label("\n".join(check.problems)).classes(
                    "text-xs text-red-700 grow whitespace-pre-wrap"
                )
            ui.button(icon="refresh", on_click=self._render_script).props(
                'flat dense round size=sm aria-label="Check"'
            ).tooltip("Check: read the file again and check it (the script is not run)")
        code = ui.code(source, language="python").classes(
            "w-full max-h-40 overflow-hidden text-xs border rounded cursor-pointer apb-script-code"
        )
        code.tooltip("Click to open the code in the editor")
        code.on("click", lambda: self._edit(path))
        with ui.row().classes("w-full items-center gap-1"):
            ui.button("Edit", icon="code", on_click=lambda: self._edit(path)).props(
                "dense unelevated no-caps"
            ).classes("apb-script-edit").tooltip("Edit the script in a large editor here")
            ui.button(icon="open_in_new", on_click=lambda: self._open(path)).props(
                'dense flat round aria-label="Open in editor"'
            ).tooltip("Open in editor: the program your system uses for .py files")

    def _open(self, path: Path) -> None:
        try:
            self.open_in_editor(path)
        except OSError as exc:
            ui.notify(f"Cannot open {path.name}: {exc.strerror or exc}", type="warning")

    async def _edit(self, path: Path) -> None:
        await self.edit_script(path)
        self._render_script()

    # ---- start choices --------------------------------------------------------------------

    def _start_folder(self) -> Path:
        scripts = self.base_dir / "scripts"
        return scripts if scripts.is_dir() else self.base_dir

    async def _use_existing(self) -> None:
        path = await FileDialog(title="Choose script", start=self._start_folder(), suffix=".py")
        if path:
            self.on_change("script", self.relative_path(path))
            self.rebuild()

    async def _new_from_template(self) -> None:
        """Choose a template and a new file name, create the file, link it (PY-12, PY-13)."""
        key = await self._ask_template()
        if key is None:
            return
        path = await FileDialog(
            title="New script from template",
            start=self._start_folder(),
            suffix=".py",
            save=True,
            default_name="calculation.py",
        )
        if path is None:
            return
        if path.exists():
            ui.notify(
                f"{path.name} already exists — use 'Use existing file…' to link it. "
                "Existing scripts are never replaced.",
                type="warning",
                multi_line=True,
            )
            return
        try:
            inputs, outputs = create_script(path, key, self._inputs(), self._outputs())
        except ScriptError as exc:
            ui.notify(str(exc), type="warning", multi_line=True)
            return
        # Store everything first and rebuild once at the end: the rebuild deletes the button
        # this handler runs in, after which no further GUI updates are possible from here.
        self.on_change("script", self.relative_path(path))
        if (inputs, outputs) != (self._inputs(), self._outputs()):
            self.on_change("inputs", ports_to_params(inputs))
            self.on_change("outputs", ports_to_params(outputs))
        ui.notify(f"Created {path.name}", type="positive")
        self.rebuild()

    @staticmethod
    async def _ask_template() -> str | None:
        """Let the user pick a template; returns its key or ``None`` if cancelled."""
        titles = template_titles()
        with ui.dialog() as dialog, ui.card().classes("w-[34rem] max-w-full"):
            ui.label("New script from template").classes("text-lg font-medium")
            choice = ui.radio(titles, value=next(iter(titles))).classes("apb-template-choice")
            ui.label(
                "Specialised templates add their ports to a block that has none; otherwise "
                "they use the ports the block already has."
            ).classes("text-xs text-slate-500")
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
                ui.button("Next…", on_click=lambda: dialog.submit(choice.value))
        result = await dialog
        dialog.delete()
        return None if result is None else str(result)
