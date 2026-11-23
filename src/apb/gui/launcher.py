"""Application window: open the GUI in a Chrome/Edge app window and watch it (spec 06 §3.6).

The window is a browser started with ``--app=<url>`` and its own profile folder, so it is a
separate browser process. When that process ends (the user closed the window) the server
stops; when the user quits, the process is ended (the window closes). Without Chrome or
Edge the default browser opens a tab, and the server stops after a while without any page
connected (:class:`IdleMonitor`).

Pure Python apart from starting processes; no NiceGUI imports, so it is unit-testable.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from apb.config import ConfigError, config_dir, read_section

#: Seconds without any connected page before the server stops (browser mode, GUI-28).
DEFAULT_IDLE_SECONDS = 30.0

#: The same for the app window (GUI-27): a closed window should end the tool quickly; a
#: reload reconnects within a second.
DEFAULT_APP_IDLE_SECONDS = 5.0

#: A browser process that ends this quickly handed the window to a browser process that was
#: already running with the same profile; its end does not mean "window closed".
HANDOVER_SECONDS = 3.0

#: Size of a new app window.
WINDOW_SIZE = "1500,950"


class WindowMode(StrEnum):
    """How the GUI is shown (GUI-29)."""

    APP = "app"
    """Chrome/Edge app window (default)."""
    BROWSER = "browser"
    """A tab in the default browser."""
    NONE = "none"
    """Only the server; the user opens the page."""


@dataclass(frozen=True)
class WindowSettings:
    """The window settings in effect (command line > config file > default).

    Attributes:
        mode: Window mode.
        browser: Configured Chrome/Edge executable (``""`` = search).
        idle_seconds: Seconds without any connected page before the server stops (app and
            browser mode); ``None`` = the mode's default (:func:`default_idle_seconds`).
    """

    mode: WindowMode = WindowMode.APP
    browser: str = ""
    idle_seconds: float | None = None

    @property
    def idle(self) -> float:
        """Idle time in effect: the configured one, else the mode's default."""
        return default_idle_seconds(self.mode) if self.idle_seconds is None else self.idle_seconds


def default_idle_seconds(mode: WindowMode) -> float:
    """Return the default idle time of a window mode (GUI-27, GUI-28)."""
    return DEFAULT_APP_IDLE_SECONDS if mode is WindowMode.APP else DEFAULT_IDLE_SECONDS


def window_settings(cli_mode: str | None = None, path: Path | None = None) -> WindowSettings:
    """Return the window settings: ``--window`` > ``[gui]`` of the config file > default.

    Args:
        cli_mode: Value of ``--window`` (``None`` if not given).
        path: Config file (default: the user's config file).

    Raises:
        ConfigError: If the config file cannot be read or has invalid ``[gui]`` values.
    """
    section = read_section("gui", path)
    raw_mode = cli_mode or str(section.get("window", WindowMode.APP))
    try:
        mode = WindowMode(raw_mode)
    except ValueError as exc:
        raise ConfigError(
            f"Unknown window mode {raw_mode!r}; use one of: "
            + ", ".join(m.value for m in WindowMode)
        ) from exc
    raw_idle = section.get("idle_shutdown_seconds")
    try:
        idle = None if raw_idle is None else float(raw_idle)
    except (TypeError, ValueError) as exc:
        raise ConfigError("[gui] idle_shutdown_seconds must be a number") from exc
    return WindowSettings(mode, str(section.get("browser", "")), idle)


# --------------------------------------------------------------------------------------
# Finding Chrome / Edge
# --------------------------------------------------------------------------------------

_LINUX_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
)

_MAC_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)

_WINDOWS_RELATIVE = (
    r"Google\Chrome\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
    r"Chromium\Application\chrome.exe",
)


def browser_candidates(
    platform: str | None = None, environ: Mapping[str, str] | None = None
) -> list[Path]:
    """Return the places Chrome, Edge or Chromium are looked for, in order of preference.

    Args:
        platform: ``sys.platform`` value (default: this computer's).
        environ: Environment variables (default: this process's).

    Returns:
        Candidate executables (they may not exist). On Linux only programs found on
        ``PATH`` are returned.
    """
    platform = platform or sys.platform
    env = os.environ if environ is None else environ
    if platform.startswith("win"):
        roots = [env.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        return [Path(root) / rel for rel in _WINDOWS_RELATIVE for root in roots if root]
    if platform == "darwin":
        return [Path(p) for p in _MAC_APPS]
    found = [shutil.which(name, path=env.get("PATH")) for name in _LINUX_NAMES]
    return [Path(p) for p in found if p]


def find_browser(configured: str = "", candidates: Sequence[Path] | None = None) -> Path | None:
    """Return the Chrome/Edge executable to use for the app window, or ``None``.

    Args:
        configured: Path from the config file (``[gui] browser``); used if it exists.
        candidates: Places to look (default: :func:`browser_candidates`).
    """
    if configured:
        path = Path(configured).expanduser()
        found = path if path.is_file() else shutil.which(configured)
        return Path(found) if found else None
    for candidate in browser_candidates() if candidates is None else candidates:
        if candidate.is_file():
            return candidate
    return None


def profile_dir() -> Path:
    """Return the browser profile folder of the app window (next to ``config.toml``)."""
    return config_dir() / "browser-profile"


def app_command(browser: Path, url: str, profile: Path) -> list[str]:
    """Return the command line that opens ``url`` in an app window (GUI-25).

    Args:
        browser: Chrome/Edge executable.
        url: Page to open.
        profile: Own profile folder (makes the window a separate browser process).
    """
    return [
        str(browser),
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        f"--window-size={WINDOW_SIZE}",
    ]


def wait_for_port(port: int, timeout: float = 20.0) -> bool:
    """Wait until the local server accepts connections; ``False`` after ``timeout`` s."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


# --------------------------------------------------------------------------------------
# The app window process
# --------------------------------------------------------------------------------------


class AppWindow:
    """The browser process that shows the app window(s) (GUI-25 … GUI-27).

    Args:
        browser: Chrome/Edge executable.
        profile: Profile folder.
        on_closed: Called (from a background thread) when the user closed the window,
            i.e. the watched browser process ended without :meth:`close`.
        on_handover: Called when the started process ended at once because a browser
            with the same profile was already running and took the window over; the
            window can then not be watched.
        popen: Process factory (for tests).
    """

    def __init__(
        self,
        browser: Path,
        profile: Path,
        *,
        on_closed: Callable[[], None],
        on_handover: Callable[[], None] = lambda: None,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.browser = browser
        self.profile = profile
        self.on_closed = on_closed
        self.on_handover = on_handover
        self._popen = popen
        self.process: Any = None
        self._closing = False
        self._watcher: threading.Thread | None = None

    def open(self, url: str) -> None:
        """Open a window for ``url``; the first one is watched, later ones join it."""
        self.profile.mkdir(parents=True, exist_ok=True)
        command = app_command(self.browser, url, self.profile)
        process = self._popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL
        )
        if self.process is None:
            self.process = process
            self._watcher = threading.Thread(target=self._watch, daemon=True, name="apb-window")
            self._watcher.start()

    def _watch(self) -> None:
        started = time.monotonic()
        code = self.process.wait()
        if self._closing:
            return
        if code == 0 and time.monotonic() - started < HANDOVER_SECONDS:
            self.on_handover()
            return
        self.on_closed()

    def close(self, timeout: float = 5.0) -> None:
        """End the browser process: its windows close (GUI-26)."""
        self._closing = True
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the watcher thread (tests)."""
        if self._watcher is not None:
            self._watcher.join(timeout)


# --------------------------------------------------------------------------------------
# Idle shutdown (browser mode)
# --------------------------------------------------------------------------------------


class IdleMonitor:
    """Stop the server when no page has been connected for a while (GUI-28).

    Only counts once a page has connected, so a server whose page was never opened keeps
    running. A reload reconnects within a second and does not trigger it.

    Args:
        seconds: Idle time before :attr:`on_idle` is called.
        on_idle: Stops the server.
        call_later: Schedules ``callback`` after ``delay`` seconds (the event loop's
            ``call_later``).
    """

    def __init__(
        self,
        seconds: float,
        on_idle: Callable[[], None],
        call_later: Callable[[float, Callable[[], None]], Any],
    ) -> None:
        self.seconds = seconds
        self.on_idle = on_idle
        self.call_later = call_later
        self.connected = 0
        self.ever_connected = False
        self._generation = 0

    def connect(self) -> None:
        """A page connected."""
        self.connected += 1
        self.ever_connected = True
        self._generation += 1

    def disconnect(self) -> None:
        """A page disconnected; start the countdown if it was the last one."""
        self.connected = max(0, self.connected - 1)
        if self.connected == 0 and self.ever_connected:
            self._generation += 1
            generation = self._generation
            self.call_later(self.seconds, lambda: self._check(generation))

    def _check(self, generation: int) -> None:
        if self.connected == 0 and generation == self._generation:
            self.on_idle()


def opened_message(browser: Path | None, profile: Path | None, urls: Sequence[str]) -> str:
    """Return the console line that says how the pages were shown (GUI-32).

    Args:
        browser: Chrome/Edge executable of the app window (``None`` = default browser).
        profile: Profile folder of the app window.
        urls: The opened pages.
    """
    where = ", ".join(urls)
    if browser is None:
        return (
            f"Potato Pipeline: opened {where} in the default browser. The tool stops when no "
            "page has been open for a while, or with File > Quit."
        )
    lower = browser.name.lower()
    name = "Edge" if "edge" in lower else "Chromium" if "chromium" in lower else "Chrome"
    return (
        f"Potato Pipeline: opened {where} in a separate {name} app window "
        f"({browser}, own profile {profile}).\n"
        "Closing that window or File > Quit stops the tool. For a tab in your normal browser "
        "instead, start with --window browser."
    )


def open_in_browser(url: str) -> None:
    """Open ``url`` in a tab of the default browser (fallback, GUI-28)."""
    webbrowser.open(url)


def open_windows_only(window: WindowSettings, urls: Sequence[str]) -> None:
    """Open pages of a server that is already running (second start, spec 06 §3.6.1).

    In app mode the windows join the app window's browser process if it runs; they are not
    watched (the running server does that).

    Args:
        window: Window settings.
        urls: Full URLs to open.
    """
    if window.mode is WindowMode.NONE:
        return
    browser = find_browser(window.browser) if window.mode is WindowMode.APP else None
    for url in urls:
        if browser is not None:
            profile = profile_dir()
            profile.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(
                app_command(browser, url, profile),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        else:
            open_in_browser(url)
