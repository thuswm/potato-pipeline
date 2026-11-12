"""Server-side file dialog (spec 06 §3.4).

The browser cannot hand full local paths to the server, but the server runs on the user's
machine, so the dialog browses the local file system from Python.
"""

from __future__ import annotations

import os
import string
from pathlib import Path

from nicegui import ui


def _roots() -> list[Path]:
    """Drive letters on Windows, ``/`` elsewhere."""
    if os.name == "nt":
        return [Path(f"{d}:\\") for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    return [Path("/")]


class FileDialog(ui.dialog):
    """Pick a file to open, or a name to save as. ``await`` it to get the path (or None)."""

    def __init__(
        self,
        *,
        title: str,
        start: Path,
        suffix: str = ".apb.json",
        save: bool = False,
        default_name: str = "",
    ) -> None:
        """Create the dialog.

        Args:
            title: Dialog title.
            start: Folder (or file) to start in.
            suffix: Only files with this suffix are listed.
            save: Ask for a (new) file name instead of picking an existing file.
            default_name: Initial file name in save mode.
        """
        super().__init__()
        self.suffix = suffix
        self.save_mode = save
        self.folder = start if start.is_dir() else start.parent
        if not self.folder.is_dir():
            self.folder = Path.home()
        with self, ui.card().classes("w-[44rem] max-w-full"):
            ui.label(title).classes("text-lg font-medium")
            with ui.row().classes("w-full items-center no-wrap gap-1"):
                ui.button(icon="arrow_upward", on_click=self._up).props("flat dense").tooltip(
                    "Parent folder"
                )
                ui.button(icon="home", on_click=lambda: self._go(Path.home())).props(
                    "flat dense"
                ).tooltip("Home folder")
                for root in _roots() if os.name == "nt" else []:
                    ui.button(str(root)[:2], on_click=lambda r=root: self._go(r)).props(
                        "flat dense"
                    )
                self.path_label = ui.label().classes("text-sm text-gray-600 truncate")
            self.listing = ui.scroll_area().classes("w-full h-72 border rounded")
            self.name = ui.input("File name", value=default_name).classes("w-full")
            self.name.on("keydown.enter", self._ok)
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=lambda: self.submit(None)).props("flat")
                ui.button("Save" if save else "Open", on_click=self._ok)
        self._refresh()

    def _go(self, folder: Path) -> None:
        if folder.is_dir():
            self.folder = folder
            self._refresh()

    def _up(self) -> None:
        self._go(self.folder.parent)

    def _refresh(self) -> None:
        self.path_label.text = str(self.folder)
        self.listing.clear()
        try:
            entries = sorted(self.folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            with self.listing:
                ui.label(f"Cannot read folder: {exc.strerror}").classes("text-red-600 p-2")
            return
        with self.listing:
            shown = 0
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    item = ui.item(on_click=lambda e=entry: self._go(e)).classes("w-full")
                    with item, ui.row().classes("items-center gap-2"):
                        ui.icon("folder", color="amber-7")
                        ui.label(entry.name)
                    shown += 1
                elif entry.name.endswith(self.suffix):
                    item = ui.item(on_click=lambda e=entry: self._pick(e)).classes("w-full")
                    with item, ui.row().classes("items-center gap-2"):
                        ui.icon("description", color="blue-7")
                        ui.label(entry.name)
                    shown += 1
            if not shown:
                ui.label(f"No folders or {self.suffix} files here").classes("text-gray-500 p-2")

    def _pick(self, entry: Path) -> None:
        self.name.value = entry.name
        if not self.save_mode:
            self.submit(entry)

    def _ok(self) -> None:
        name = (self.name.value or "").strip()
        if not name:
            ui.notify("Enter a file name", type="warning")
            return
        path = Path(name).expanduser()
        path = path if path.is_absolute() else self.folder / path
        if self.save_mode and not path.name.endswith(self.suffix):
            path = path.with_name(path.name.removesuffix(".json") + self.suffix)
        if not self.save_mode and not path.is_file():
            ui.notify(f"File not found: {path.name}", type="warning")
            return
        self.submit(path)


async def confirm(question: str, ok_label: str = "Discard changes") -> bool:
    """Ask a yes/no question; return ``True`` if confirmed."""
    with ui.dialog() as dialog, ui.card():
        ui.label(question)
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
            ui.button(ok_label, color="negative", on_click=lambda: dialog.submit(True))
    result = await dialog
    dialog.delete()
    return bool(result)
