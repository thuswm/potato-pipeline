"""Property panel of the Python block: script view, templates, static check (spec 04 §3.5).

Shows the script of the block read-only with the result of the static check, offers the
start choices (existing file / new from template) while there is no script, and never
overwrites a script that exists (PY-13): there is no replace action. The one change the
panel makes to an existing script is the ``def run(...)`` line, kept in step with the input
ports (PY-16, spec 04 §3.9).
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
from apb.blocks.python.signature import (
    output_differences,
    ports_from_signature,
    ports_missing_from_run,
    rewrite_run_signature,
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
        old_inputs = self._inputs() if name == "inputs" else None
        self.on_change(name, value)
        if old_inputs is not None:
            self._sync_run(old_inputs, self._inputs())
        if name in _WATCHED:
            self._render_script()

    # ---- ports and run() in step (PY-16, PY-17) -------------------------------------------

    def _code_source(self) -> tuple[str, Path | None] | None:
        """Return ``(code, script file)`` (file ``None`` for inline code), or ``None``."""
        path = self._script_path()
        if path is not None:
            if not path.is_file():
                return None
            try:
                return read_script(path), path
            except ScriptError:
                return None
        inline = str(self.params.get("inline_code") or "")
        return (inline, None) if inline.strip() else None

    def _sync_run(self, old: list[PortSpec], new: list[PortSpec]) -> None:
        """Rewrite the ``def run(...)`` line for changed input ports (PY-16)."""
        found = self._code_source()
        if found is None:
            return
        source, path = found
        try:
            change = rewrite_run_signature(source, old, new)
        except ScriptError as exc:
            ui.notify(f"run() not updated: {exc}", type="warning", multi_line=True)
            return
        if not change.changed:
            return
        if path is not None:
            try:
                path.write_text(change.source, encoding="utf-8", newline="\n")
            except OSError as exc:
                ui.notify(f"run() not updated: {exc.strerror or exc}", type="warning")
                return
            # The file is saved now; the ports only with the pipeline (spec 04 §3.9.1).
            message = (
                f"run() in {path.name} updated and saved: {change.summary()}. "
                "Save the pipeline to keep the ports."
            )
        else:
            self.on_change("inline_code", change.source)
            message = f"run() updated: {change.summary()}"
        if change.renamed:
            message += ". Only the run() line is changed: rename the name in the code too."
        ui.notify(message, type="info", multi_line=True)
        if path is None:
            self.rebuild()  # the inline code field shows the new text

    def _add_ports(self, proposed: list[PortSpec]) -> None:
        """Add input ports for ``run()`` arguments that have none (PY-17)."""
        self.on_change("inputs", ports_to_params([*self._inputs(), *proposed]))
        ui.notify("Added input port(s): " + ", ".join(p.name for p in proposed), type="info")
        self.rebuild()

    def _remove_inputs(self, names: list[str]) -> None:
        """Remove input ports that ``run()`` does not take (PY-21); ``run()`` is unchanged."""
        kept = [p for p in self._inputs() if p.name not in names]
        self.on_change("inputs", ports_to_params(kept))
        ui.notify("Removed input port(s): " + ", ".join(names), type="info")
        self.rebuild()

    def _add_outputs(self, names: list[str]) -> None:
        """Add output ports for keys ``run()`` returns (PY-22), type Any."""
        added = [PortSpec(name=n) for n in names]
        self.on_change("outputs", ports_to_params([*self._outputs(), *added]))
        ui.notify("Added output port(s): " + ", ".join(names), type="info")
        self.rebuild()

    def _remove_outputs(self, names: list[str]) -> None:
        """Remove output ports that ``run()`` does not return (PY-22)."""
        kept = [p for p in self._outputs() if p.name not in names]
        self.on_change("outputs", ports_to_params(kept))
        ui.notify("Removed output port(s): " + ", ".join(names), type="info")
        self.rebuild()

    def _update_run(self) -> None:
        """Add the ports missing from ``run()`` to its line (PY-16)."""
        ports = self._inputs()
        self._sync_run(ports, ports)
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
                ui.label(
                    "This block uses code stored in the pipeline file (below), not a .py file."
                ).classes("text-xs text-slate-500")
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
        check = check_script_source(source, self._inputs(), path.name, self._outputs())
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
        if not check.ok:
            self._fix_buttons(source)
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

    def _fix_buttons(self, source: str) -> None:
        """Offer the fixes, in both directions, for ports that do not match the code.

        The ports stay the definition of the block; each fix is one click (PY-16, PY-17,
        PY-21, PY-22, spec 04 §3.9.1).
        """
        proposed = ports_from_signature(source, self._inputs())
        missing = [p.name for p in ports_missing_from_run(source, self._inputs())]
        outputs = output_differences(source, self._outputs())
        new_outputs = [n for n in outputs.new if n.isidentifier()]

        def fix(label: str, icon: str, action: Callable[[], None], tip: str) -> None:
            ui.button(label, icon=icon, on_click=action).props(
                "dense outline no-caps size=sm"
            ).tooltip(tip)

        with ui.row().classes("gap-1 apb-script-fix"):
            if proposed:
                fix(
                    "Add input port(s): " + ", ".join(p.name for p in proposed),
                    "add",
                    lambda: self._add_ports(proposed),
                    "Add an input port for each run() argument that has none "
                    "(type from the annotation)",
                )
            if missing:
                names = ", ".join(missing)
                fix(
                    f"Remove input port(s): {names}",
                    "remove",
                    lambda: self._remove_inputs(missing),
                    "The code is right: remove the ports run() does not take "
                    "(their connectors go too)",
                )
                fix(
                    f"Add {names} to run()",
                    "edit",
                    self._update_run,
                    "The ports are right: rewrite only the def run(...) line so it takes them",
                )
            if new_outputs:
                fix(
                    "Add output port(s): " + ", ".join(new_outputs),
                    "add",
                    lambda: self._add_outputs(new_outputs),
                    "Add an output port for each key run() returns that has none (type Any; "
                    "change it under Ports)",
                )
            if outputs.unused:
                fix(
                    "Remove output port(s): " + ", ".join(outputs.unused),
                    "remove",
                    lambda: self._remove_outputs(outputs.unused),
                    "Remove the output ports that run() does not return (their connectors go too)",
                )

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
