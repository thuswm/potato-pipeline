"""Property panel of the Nastran analysis block (spec 09 §3.9).

The generated form for the parameters, plus status lines computed without running anything:
is the program found, what is the input, which result files exist and how do they relate to
the input. Buttons open the ``.f06`` and the folder.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nicegui import ui

from apb.blocks.nastran.analysis import NastranRun, NastranRunParams
from apb.blocks.nastran.status import AnalysisStatus, analysis_status
from apb.gui.forms import ParamForm, short_path


class AnalysisPanel:
    """Builds the panel of one Nastran analysis block.

    Args:
        params: Stored parameters of the block (kept up to date by ``on_change``).
        on_change: Called with ``(name, value)`` to store a parameter.
        pick_file: Async callback for the file field.
        resolve: Replaces ``${variable}`` references.
        base_dir: Folder that relative paths refer to.
        open_file: Opens a file or folder with the program of the system.
    """

    def __init__(
        self,
        params: dict[str, Any],
        *,
        on_change: Callable[[str, Any], None],
        pick_file: Callable[[str], Any] | None,
        resolve: Callable[[Any], Any],
        base_dir: Path,
        open_file: Callable[[Path], None],
    ) -> None:
        self.params = params
        self.on_change = on_change
        self.resolve = resolve
        self.base_dir = base_dir
        self.open_file = open_file
        self.form = ParamForm(NastranRunParams, params, self._changed, pick_file)

    def _changed(self, name: str, value: Any) -> None:
        self.on_change(name, value)
        self.refresh()

    def build(self) -> None:
        """Create the widgets in the current NiceGUI context (spec 11 §4.5).

        *Settings*: input deck, status lines, run mode, what fails the block. *Advanced*
        (folded): command, path, options, time limit and message settings, which are
        usually set once per computer.
        """
        self.form.field("input_file")
        self.status_box = ui.column().classes("w-full gap-0.5 apb-analysis-status")
        self.refresh()
        self.form.field("run_mode")
        self.form.field("fail_on")
        self.form.advanced(list(NastranRun.advanced_params))

    def refresh(self) -> None:
        """Compute the status again and show it."""
        status = analysis_status(self.params, self.base_dir, self.resolve)
        self.status_box.clear()
        with self.status_box:
            self._show(status)

    # ---- the status lines -----------------------------------------------------------------

    def _line(
        self, label: str, text: str, tone: str = "text-slate-700", cls: str = "", tip: str = ""
    ) -> None:
        with ui.row().classes("w-full no-wrap gap-2 items-start"):
            ui.label(label).classes("text-[10px] text-slate-500 w-14 shrink-0 mt-0.5")
            shown = ui.label(text).classes(f"text-xs break-words min-w-0 {tone} {cls}")
            if tip:
                shown.tooltip(tip)

    def _show(self, status: AnalysisStatus) -> None:
        if status.problem:
            self._line("Status", status.problem, "text-red-700", "apb-analysis-problem")
            if status.deck is None:
                return
        if status.command:
            self._line(
                "Command", f"{status.command}   ({status.command_from})", cls="apb-analysis-command"
            )
        program = status.program
        if program is not None:
            if not program.checked:
                self._line(
                    "Nastran", "the command is not checked (several commands)", "text-slate-500"
                )
            elif program.found:
                self._line(
                    "Nastran",
                    f"{short_path(program.found)}   (path: {status.path_from})",
                    "text-green-800",
                    "apb-analysis-program",
                    tip=str(program.found),
                )
            else:
                self._line(
                    "Nastran",
                    f"'{program.program}' not found: set the path in this block or in the "
                    "config file",
                    "text-red-700",
                    "apb-analysis-program",
                )
        if status.deck_exists:
            extra = f" + {status.includes} included file(s)" if status.includes else ""
            request = {True: " · HDF5 request found ✔", False: " · no HDF5 request ✘", None: ""}[
                status.hdf5_request
            ]
            tone = "text-red-700" if status.hdf5_request is False else "text-slate-700"
            self._line(
                "Input",
                f"{status.deck.name if status.deck else ''}{extra} · "
                f"changed {status.modified}{request}",
                tone,
                "apb-analysis-input",
            )
        if status.results:
            parts = [
                f"{r.name} {'✔ ' + r.modified if r.exists else '✘ missing'}" for r in status.results
            ]
            self._line("Results", " · ".join(parts), cls="apb-analysis-results")
            if status.relation:
                bad = (
                    "older" in status.relation
                    or "changed" in status.relation
                    or "not finish" in status.relation
                )
                self._line(
                    "",
                    status.relation,
                    "text-amber-700" if bad else "text-slate-500",
                    "apb-analysis-relation",
                )
        with ui.row().classes("gap-1 mt-1"):
            f06 = next((r for r in status.results if r.name.endswith(".f06") and r.exists), None)
            if f06 is not None:
                ui.button(
                    "Open .f06", icon="description", on_click=lambda: self.open_file(f06.path)
                ).props("dense flat no-caps size=sm")
            if status.deck is not None and status.deck.parent.is_dir():
                folder = status.deck.parent
                ui.button(
                    "Open folder", icon="folder_open", on_click=lambda: self.open_file(folder)
                ).props("dense flat no-caps size=sm")
