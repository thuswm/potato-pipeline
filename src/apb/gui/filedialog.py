"""Server-side file dialog (spec 06 §3.4).

The browser cannot hand full local paths to the server, but the server runs on the user's
machine, so the dialog browses the local file system from Python.
"""

from __future__ import annotations

import os
import string
from collections.abc import Sequence
from pathlib import Path

from nicegui import ui


def make_folder(parent: Path, name: str) -> Path:
    """Create a sub-folder (GUI-17).

    Args:
        parent: Existing folder to create it in.
        name: Name of the new folder.

    Returns:
        Path of the new folder.

    Raises:
        ValueError: If the name is empty, contains a path separator, is ``.``/``..``, or
            the folder already exists, or if the folder cannot be created.
    """
    name = name.strip()
    if not name or name in {".", ".."} or any(c in name for c in "/\\") or ":" in name:
        raise ValueError("Enter a folder name without path separators")
    target = parent / name
    if target.exists():
        raise ValueError(f"{name} already exists")
    try:
        target.mkdir()
    except OSError as exc:
        raise ValueError(f"Cannot create {name}: {exc.strerror or exc}") from exc
    return target


def matches(name: str, suffixes: Sequence[str]) -> bool:
    """Return ``True`` if a file name ends in one of the suffixes (case-insensitive).

    An empty list of suffixes matches every name.
    """
    lower = name.lower()
    return not suffixes or any(lower.endswith(s.lower()) for s in suffixes)


def path_parts(folder: Path) -> list[tuple[str, Path]]:
    """Return the breadcrumb of a folder: ``(label, path)`` from the root to the folder.

    Example:
        ``/home/me/fem`` -> ``[("/", /), ("home", /home), ("me", /home/me), ("fem", ...)]``
    """
    chain = [folder, *folder.parents][::-1]
    return [(p.name or str(p), p) for p in chain]


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
        suffix: str | Sequence[str] = ".apb.json",
        save: bool = False,
        default_name: str = "",
    ) -> None:
        """Create the dialog.

        Args:
            title: Dialog title.
            start: Folder (or file) to start in.
            suffix: Only files ending in this suffix, or one of these suffixes, are listed
                (empty = all files); a switch shows all files anyway. In save mode the
                first suffix is appended to a name that has none of them.
            save: Ask for a (new) file name instead of picking an existing file.
            default_name: Initial file name in save mode.
        """
        super().__init__()
        self.suffixes = [suffix] if isinstance(suffix, str) else list(suffix)
        self.suffixes = [s for s in self.suffixes if s]
        self.show_all = False
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
                ui.button(icon="create_new_folder", on_click=self._new_folder).props(
                    'flat dense aria-label="New folder"'
                ).tooltip("New folder")
                for root in _roots() if os.name == "nt" else []:
                    ui.button(str(root)[:2], on_click=lambda r=root: self._go(r)).props(
                        "flat dense"
                    )
            # The path as clickable parts; a long path is cut at the start, so the end
            # (the folder shown) stays visible. Full path in the tooltip.
            self.path_row = ui.element("div").classes("apb-path w-full text-sm text-gray-700")
            self.path_row.style(
                "display:flex;justify-content:flex-end;overflow:hidden;white-space:nowrap"
            )
            self.listing = ui.scroll_area().classes("w-full h-72 border rounded")
            if self.suffixes:
                ui.switch(
                    f"Show all files (not only {' '.join(self.suffixes)})",
                    on_change=self._toggle_all,
                ).props("dense").classes("text-sm")
            self.name = ui.input("File name", value=default_name).classes("w-full")
            self.name.tooltip("A file name, or a full path; a folder path opens that folder")
            self.name.on("keydown.enter", self._ok)
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=lambda: self.submit(None)).props("flat")
                ui.button("Save" if save else "Open", on_click=self._ok)
        self._refresh()

    def _go(self, folder: Path) -> None:
        if folder.is_dir():
            self.folder = folder
            self._refresh()

    def _toggle_all(self, event: object) -> None:
        self.show_all = bool(getattr(event, "value", False))
        self._refresh()

    def _up(self) -> None:
        self._go(self.folder.parent)

    async def _new_folder(self) -> None:
        """Ask for a name, create the folder in the current one and open it (GUI-17)."""
        with ui.dialog() as ask, ui.card():
            ui.label("New folder").classes("font-medium")
            name = ui.input("Folder name").props("dense outlined autofocus")
            name.on("keydown.enter", lambda: ask.submit(name.value))
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=lambda: ask.submit(None)).props("flat")
                ui.button("Create", on_click=lambda: ask.submit(name.value))
        chosen = await ask
        ask.delete()
        if chosen is None:
            return
        try:
            self._go(make_folder(self.folder, str(chosen)))
        except ValueError as exc:
            ui.notify(str(exc), type="warning")

    def _refresh(self) -> None:
        self._render_path()
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
                elif self.show_all or matches(entry.name, self.suffixes):
                    item = ui.item(on_click=lambda e=entry: self._pick(e)).classes("w-full")
                    with item, ui.row().classes("items-center gap-2"):
                        ui.icon("description", color="blue-7")
                        ui.label(entry.name)
                    shown += 1
            if not shown:
                kinds = " ".join(self.suffixes) if self.suffixes and not self.show_all else ""
                ui.label(f"No folders or {kinds + ' ' if kinds else ''}files here").classes(
                    "text-gray-500 p-2"
                )

    def _render_path(self) -> None:
        """Show the folder as clickable parts (cut at the start when too long)."""
        self.path_row.clear()
        self.path_row.props(f'title="{self.folder}"')
        with self.path_row:
            parts = path_parts(self.folder)
            for i, (label, target) in enumerate(parts):
                if i:
                    ui.label("/").classes("px-1 text-gray-400 shrink-0")
                last = i == len(parts) - 1
                part = ui.label(label).classes(
                    "shrink-0 " + ("font-medium" if last else "cursor-pointer hover:underline")
                )
                if not last:
                    part.on("click", lambda t=target: self._go(t))

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
        if path.is_dir():  # a pasted folder path opens the folder
            self.name.value = ""
            self._go(path)
            return
        if self.save_mode and self.suffixes and not matches(path.name, self.suffixes):
            first = self.suffixes[0]
            path = path.with_name(path.name.removesuffix(".json") + first)
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
