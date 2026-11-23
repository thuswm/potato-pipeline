"""Settings of this computer: the ``config.toml`` edited from the GUI (spec 06 §3.7, GUI-36).

The dialog edits the ``[nastran]`` section (command, path, options; spec 09 §3.3) and the
``[gui]`` section (window mode, browser, idle time; spec 06 §3.6). A block's own value wins
over these; an empty value means "built-in default". The check and the conversion to the
file's sections are plain functions, so they can be tested without a browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nicegui import ui

from apb.blocks.nastran import command as cmd
from apb.config import ConfigError, config_path, read_section, write_section
from apb.gui.launcher import WindowMode, default_idle_seconds


class SettingsError(ValueError):
    """A value in the settings dialog is not valid; the message is for the user."""


@dataclass
class UserSettings:
    """The values shown in the dialog (text as typed; empty = built-in default).

    Attributes:
        nastran_command: Command template, e.g. ``nastran {input} {options}``.
        nastran_path: Folder(s) with the Nastran program, put in front of ``PATH``.
        nastran_options: Nastran keywords, comma-separated (``mem=4gb, smp=4``).
        window: Window mode (``app``, ``browser``, ``none``).
        browser: Chrome/Edge executable for the app window.
        idle_seconds: Seconds without a page before the server stops (empty = default).
    """

    nastran_command: str = ""
    nastran_path: str = ""
    nastran_options: str = ""
    window: str = WindowMode.APP.value
    browser: str = ""
    idle_seconds: str = ""


def load_user_settings(path: Path | None = None) -> UserSettings:
    """Read the dialog's values from the config file (missing file = all defaults).

    Raises:
        ConfigError: If the file cannot be read.
    """
    nastran = read_section("nastran", path)
    gui = read_section("gui", path)
    idle = gui.get("idle_shutdown_seconds")
    return UserSettings(
        nastran_command=str(nastran.get("command", "")),
        nastran_path=str(nastran.get("path", "")),
        nastran_options=", ".join(str(o) for o in nastran.get("options", [])),
        window=str(gui.get("window", WindowMode.APP.value)),
        browser=str(gui.get("browser", "")),
        idle_seconds="" if idle is None else f"{idle:g}" if isinstance(idle, float) else str(idle),
    )


def to_sections(values: UserSettings) -> dict[str, dict[str, Any]]:
    """Check the values and return them as config file sections.

    Empty values become ``None`` (the key is removed, the default applies). The window mode
    ``app`` is the default and is not written.

    Raises:
        SettingsError: For an invalid command, option, window mode or idle time.
    """
    command = values.nastran_command.strip()
    options = [o.strip() for o in values.nastran_options.replace(";", ",").split(",")]
    options = [o for o in options if o]
    try:
        if command:
            cmd.check_template(command)
        for option in options:
            cmd.validate_option(option)
    except cmd.CommandError as exc:
        raise SettingsError(str(exc)) from exc
    try:
        mode = WindowMode(values.window)
    except ValueError as exc:
        raise SettingsError(f"Unknown window mode {values.window!r}") from exc
    idle_text = values.idle_seconds.strip()
    idle: float | None = None
    if idle_text:
        try:
            idle = float(idle_text)
        except ValueError as exc:
            raise SettingsError("The idle time must be a number of seconds") from exc
        if idle <= 0:
            raise SettingsError("The idle time must be more than 0 s")
    return {
        "nastran": {
            "command": command or None,
            "path": values.nastran_path.strip() or None,
            "options": options or None,
        },
        "gui": {
            "window": None if mode is WindowMode.APP else mode.value,
            "browser": values.browser.strip() or None,
            "idle_shutdown_seconds": idle,
        },
    }


def save_user_settings(values: UserSettings, path: Path | None = None) -> Path:
    """Check the values and write them to the config file; return the file.

    Raises:
        SettingsError: For invalid values (nothing is written).
        ConfigError: If the file cannot be read or written.
    """
    sections = to_sections(values)
    target = path or config_path()
    for name, section in sections.items():
        write_section(name, section, target)
    return target


def nastran_found(values: UserSettings) -> str:
    """Return a line saying whether the Nastran program of these settings is found."""
    try:
        settings = cmd.resolve("", values.nastran_path.strip(), [], _defaults(values))
    except cmd.CommandError as exc:
        return f"✘ {exc}"
    status = cmd.program_status(
        cmd.build_command(settings, Path("job.bdf")), cmd.environment(settings)
    )
    if not status.checked:
        return "The command is not checked (several commands)"
    if status.found:
        return f"✔ Nastran found: {status.found}"
    return f"✘ '{status.program}' not found on PATH or in the folder above"


def _defaults(values: UserSettings) -> cmd.Defaults:
    return cmd.Defaults(values.nastran_command.strip(), values.nastran_path.strip(), ())


class UserSettingsDialog:
    """The dialog *File > Settings of this computer…* (GUI-36).

    Args:
        path: Config file (default: :func:`apb.config.config_path`).
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_path()

    async def show(self) -> bool:
        """Show the dialog; return ``True`` if the settings were saved."""
        try:
            values = load_user_settings(self.path)
        except ConfigError as exc:
            ui.notify(str(exc), type="negative", multi_line=True)
            return False
        with ui.dialog() as dialog, ui.card().classes("w-[40rem] max-w-full apb-user-settings"):
            ui.label("Settings of this computer").classes("text-lg font-medium")
            ui.label(
                f"Stored in {self.path}. They apply to all pipelines on this computer; a "
                "value set in a block wins. Empty = built-in default."
            ).classes("text-xs text-slate-500 break-all")

            ui.label("MSC Nastran").classes("apb-section-title mt-2")
            command = (
                ui.input("Command", value=values.nastran_command, placeholder=cmd.DEFAULT_COMMAND)
                .props("dense outlined stack-label")
                .classes("w-full")
            )
            command.tooltip("Placeholders: " + ", ".join(f"{{{p}}}" for p in cmd.PLACEHOLDERS))
            folder = (
                ui.input(
                    "Folder with the Nastran program",
                    value=values.nastran_path,
                    placeholder="(found on PATH)",
                )
                .props("dense outlined stack-label")
                .classes("w-full")
            )
            folder.tooltip("Put in front of PATH, e.g. C:\\MSC.Software\\MSC_Nastran\\2024.1\\bin")
            options = (
                ui.input("Options", value=values.nastran_options, placeholder="e.g. mem=4gb, smp=4")
                .props("dense outlined stack-label")
                .classes("w-full")
            )
            options.tooltip("Nastran keywords added to every run on this computer")
            found = ui.label().classes("text-xs apb-user-settings-found")

            def check() -> None:
                current = UserSettings(nastran_command=command.value, nastran_path=folder.value)
                text = nastran_found(current)
                found.text = text
                found.classes(
                    replace="text-xs apb-user-settings-found "
                    + ("text-green-800" if text.startswith("✔") else "text-slate-600")
                )

            for box in (command, folder):
                box.on("blur", check)
            check()

            ui.label("Window").classes("apb-section-title mt-2")
            ui.label("Takes effect the next time the tool is started.").classes(
                "text-xs text-slate-500"
            )
            window = (
                ui.select(
                    {
                        "app": "Separate Chrome/Edge app window",
                        "browser": "Tab in the default browser",
                        "none": "No window (open the address yourself)",
                    },
                    value=values.window
                    if values.window in {m.value for m in WindowMode}
                    else "app",
                    label="Show the tool in",
                )
                .props("dense outlined stack-label")
                .classes("w-full")
            )
            browser = (
                ui.input("Chrome/Edge program", value=values.browser, placeholder="(searched)")
                .props("dense outlined stack-label")
                .classes("w-full")
            )
            idle = (
                ui.input(
                    "Stop after no window is open for [s]",
                    value=values.idle_seconds,
                    placeholder=f"{default_idle_seconds(WindowMode.APP):g} s (app window), "
                    f"{default_idle_seconds(WindowMode.BROWSER):g} s (browser tab)",
                )
                .props("dense outlined stack-label")
                .classes("w-full")
            )

            def save() -> None:
                chosen = UserSettings(
                    nastran_command=command.value or "",
                    nastran_path=folder.value or "",
                    nastran_options=options.value or "",
                    window=str(window.value or "app"),
                    browser=browser.value or "",
                    idle_seconds=idle.value or "",
                )
                try:
                    written = save_user_settings(chosen, self.path)
                except (SettingsError, ConfigError) as exc:
                    ui.notify(str(exc), type="negative", multi_line=True)
                    return
                ui.notify(f"Saved {written}", type="positive")
                dialog.submit(True)

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
                ui.button("Save", on_click=save)
        saved = await dialog
        dialog.delete()
        return bool(saved)
