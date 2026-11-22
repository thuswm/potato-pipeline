"""Tests of the application window and clean exit (spec 06 §3.6, GUI-25 … GUI-30)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from apb.config import ConfigError
from apb.gui import launcher
from apb.gui.launcher import AppWindow, IdleMonitor, WindowMode, WindowSettings

FAKE_BROWSER = Path(__file__).resolve().parents[1] / "fake_browser.py"
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

# --------------------------------------------------------------------------------------
# Settings (GUI-29)
# --------------------------------------------------------------------------------------


def test_window_settings_default_config_and_command_line(tmp_path: Path):
    config = tmp_path / "config.toml"
    assert launcher.window_settings(None, config) == WindowSettings()
    config.write_text(
        '[gui]\nwindow = "browser"\nbrowser = "/opt/chrome"\nidle_shutdown_seconds = 5\n',
        encoding="utf-8",
    )
    assert launcher.window_settings(None, config) == WindowSettings(
        WindowMode.BROWSER, "/opt/chrome", 5.0
    )
    assert launcher.window_settings("app", config).mode is WindowMode.APP  # command line wins


def test_window_settings_errors(tmp_path: Path):
    config = tmp_path / "config.toml"
    with pytest.raises(ConfigError, match="Unknown window mode 'tab'"):
        launcher.window_settings("tab", config)
    config.write_text('[gui]\nidle_shutdown_seconds = "soon"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a number"):
        launcher.window_settings(None, config)
    config.write_text("[gui\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot be read"):
        launcher.window_settings(None, config)


# --------------------------------------------------------------------------------------
# Finding Chrome / Edge (GUI-25)
# --------------------------------------------------------------------------------------


def test_candidates_on_windows():
    env = {
        "ProgramFiles": r"C:\Program Files",
        "ProgramFiles(x86)": r"C:\Program Files (x86)",
        "LOCALAPPDATA": r"C:\Users\me\AppData\Local",
    }
    names = [str(p) for p in launcher.browser_candidates("win32", env)]
    assert names[0].endswith("chrome.exe") and names[0].startswith(r"C:\Program Files")
    assert any(n.endswith("msedge.exe") and "(x86)" in n for n in names)
    assert names.index(next(n for n in names if "msedge" in n)) > 2  # Chrome first


@pytest.mark.skipif(sys.platform == "win32", reason="PATH lookup of Linux executables")
def test_candidates_on_linux_come_from_path(tmp_path: Path):
    for name in ("chromium", "microsoft-edge"):
        exe = tmp_path / name
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    found = launcher.browser_candidates("linux", {"PATH": str(tmp_path)})
    assert [p.name for p in found] == ["chromium", "microsoft-edge"]


def test_candidates_on_mac():
    assert "Google Chrome" in str(launcher.browser_candidates("darwin", {})[0])


def test_find_browser(tmp_path: Path):
    exe = tmp_path / "msedge.exe"
    exe.write_text("")
    assert launcher.find_browser("", [tmp_path / "chrome.exe", exe]) == exe
    assert launcher.find_browser("", [tmp_path / "chrome.exe"]) is None
    assert launcher.find_browser(str(exe), []) == exe  # configured path wins
    assert launcher.find_browser(str(tmp_path / "missing.exe"), [exe]) is None


def test_app_command(tmp_path: Path):
    command = launcher.app_command(Path("/usr/bin/chromium"), "http://127.0.0.1:8765/", tmp_path)
    assert command[0] == str(Path("/usr/bin/chromium"))
    assert "--app=http://127.0.0.1:8765/" in command
    assert f"--user-data-dir={tmp_path}" in command
    assert "--no-first-run" in command


def test_profile_dir_is_next_to_the_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert launcher.profile_dir() == tmp_path / "apb" / "browser-profile"


# --------------------------------------------------------------------------------------
# The window process (GUI-26, GUI-27)
# --------------------------------------------------------------------------------------


class FakeProcess:
    """Stands in for ``subprocess.Popen``: ``finish(code)`` ends it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        import threading

        self.args = args
        self.code: int | None = None
        self.terminated = False
        self._done = threading.Event()

    def finish(self, code: int) -> None:
        self.code = code
        self._done.set()

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout or 0)
        assert self.code is not None
        return self.code

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.terminated = True
        self.finish(-15)

    def kill(self) -> None:
        self.finish(-9)


def _window(tmp_path: Path, events: list[str]) -> tuple[AppWindow, list[FakeProcess]]:
    made: list[FakeProcess] = []

    def popen(*args: Any, **kwargs: Any) -> FakeProcess:
        made.append(FakeProcess(*args, **kwargs))
        return made[-1]

    window = AppWindow(
        Path("chrome"),
        tmp_path / "profile",
        on_closed=lambda: events.append("closed"),
        on_handover=lambda: events.append("handover"),
        popen=popen,
    )
    return window, made


def test_closing_the_window_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(launcher, "HANDOVER_SECONDS", 0.0)
    events: list[str] = []
    window, made = _window(tmp_path, events)
    window.open("http://127.0.0.1:1/")
    window.open("http://127.0.0.1:1/h5?file=x")  # joins the first process, not watched
    assert len(made) == 2 and (tmp_path / "profile").is_dir()
    made[0].finish(0)
    window.join(2)
    assert events == ["closed"]


def test_quit_terminates_the_window_without_reporting_a_close(tmp_path: Path):
    events: list[str] = []
    window, made = _window(tmp_path, events)
    window.open("http://127.0.0.1:1/")
    window.close()
    window.join(2)
    assert made[0].terminated and events == []


def test_a_quick_exit_is_a_handover_to_a_running_browser(tmp_path: Path):
    events: list[str] = []
    window, made = _window(tmp_path, events)
    window.open("http://127.0.0.1:1/")
    made[0].finish(0)  # well within HANDOVER_SECONDS
    window.join(2)
    assert events == ["handover"]


# --------------------------------------------------------------------------------------
# Idle shutdown (GUI-28)
# --------------------------------------------------------------------------------------


def test_idle_monitor():
    calls: list[str] = []
    scheduled: list[Callable[[], None]] = []
    monitor = IdleMonitor(30, lambda: calls.append("idle"), lambda d, cb: scheduled.append(cb))
    monitor.disconnect()  # never connected: nothing happens
    assert scheduled == []
    monitor.connect()
    monitor.disconnect()
    monitor.connect()  # a reload: reconnected before the time is up
    scheduled.pop()()
    assert calls == []
    monitor.connect()  # two pages
    monitor.disconnect()
    assert scheduled == []  # one page is still open
    monitor.disconnect()
    scheduled.pop()()
    assert calls == ["idle"]


# --------------------------------------------------------------------------------------
# The real server with a fake browser (Linux/macOS)
# --------------------------------------------------------------------------------------

posix_only = pytest.mark.skipif(os.name == "nt", reason="fake browser is a shell script")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Gui:
    """``apb gui`` started with a fake browser; ``log()`` returns the browser's records."""

    def __init__(self, tmp_path: Path, seconds: float, *args: str, idle: float = 2) -> None:
        wrapper = tmp_path / "chrome"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_BROWSER}" "$@"\n')
        wrapper.chmod(0o755)
        config = tmp_path / "config.toml"
        config.write_text(
            f'[gui]\nbrowser = "{wrapper}"\nidle_shutdown_seconds = {idle}\n', encoding="utf-8"
        )
        self.log_file = tmp_path / "browser.log"
        self.port = _free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
        env |= {
            "APB_CONFIG": str(config),
            "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
            "FAKE_BROWSER_LOG": str(self.log_file),
            "FAKE_BROWSER_SECONDS": str(seconds),
            "BROWSER": str(wrapper),  # the default browser of the fallback mode
            "PYTHONUTF8": "1",
        }
        self.output = tmp_path / "gui.log"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "apb.cli", "gui", "--port", str(self.port), *args],
            stdout=self.output.open("w"),
            stderr=subprocess.STDOUT,
            env=env,
            cwd=EXAMPLES,
        )

    def log(self) -> list[list[str]]:
        if not self.log_file.exists():
            return []
        return [json.loads(line) for line in self.log_file.read_text().splitlines()]

    def wait_for(self, check: Callable[[], bool], timeout: float = 20) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if check():
                return True
            time.sleep(0.2)
        return False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
        self.proc.wait(timeout=10)


@pytest.fixture
def gui_factory(tmp_path: Path):
    started: list[Gui] = []

    def make(seconds: float, *args: str, idle: float = 2) -> Gui:
        started.append(Gui(tmp_path, seconds, *args, idle=idle))
        return started[-1]

    yield make
    for gui in started:
        gui.stop()


@posix_only
def test_app_window_opens_and_closing_it_stops_the_server(gui_factory):
    gui = gui_factory(5)  # the "user" closes the window after 5 s
    assert gui.wait_for(lambda: bool(gui.log())), gui.output.read_text()
    (args,) = gui.log()
    assert f"--app={gui.url}" in args
    assert any(a.startswith("--user-data-dir=") and "browser-profile" in a for a in args)
    assert gui.wait_for(lambda: gui.proc.poll() is not None), gui.output.read_text()
    assert "the window was closed" in gui.output.read_text()


def _page(url: str):
    playwright = pytest.importorskip("playwright.sync_api")
    manager = playwright.sync_playwright().start()
    try:
        browser = manager.chromium.launch()
    except Exception as exc:  # browser not installed
        manager.stop()
        pytest.skip(f"Playwright browser not available: {exc}")
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(url)
    page.wait_for_selector(".drawflow")
    return manager, browser, page


@posix_only
def test_quit_closes_the_app_window(gui_factory):
    gui = gui_factory(3600)
    assert gui.wait_for(lambda: bool(gui.log()))
    manager, browser, page = _page(gui.url)
    try:
        page.get_by_role("button", name="File", exact=True).click()
        page.get_by_text("Quit", exact=True).click()
        assert gui.wait_for(lambda: gui.proc.poll() is not None), gui.output.read_text()
        assert gui.log()[-1] == ["terminated"]
    finally:
        browser.close()
        manager.stop()


@posix_only
def test_handover_then_idle_shutdown(gui_factory):
    gui = gui_factory(0)  # the fake browser hands the window over and returns at once
    assert gui.wait_for(lambda: bool(gui.log()))
    time.sleep(1)
    assert gui.proc.poll() is None  # not taken as "window closed"
    manager, browser, page = _page(gui.url)
    page.close()
    browser.close()
    manager.stop()
    assert gui.wait_for(lambda: gui.proc.poll() is not None, timeout=15), gui.output.read_text()
    assert "no page open any more" in gui.output.read_text()


@posix_only
def test_browser_mode_opens_a_tab_and_stops_when_no_page_is_open(gui_factory):
    gui = gui_factory(0, "--window", "browser")
    assert gui.wait_for(lambda: bool(gui.log()))
    assert gui.log()[0] == [gui.url]  # the default browser got the plain URL, no app mode
    time.sleep(3)
    assert gui.proc.poll() is None  # no page has connected yet: keeps running
    manager, browser, page = _page(gui.url)
    page.close()
    browser.close()
    manager.stop()
    assert gui.wait_for(lambda: gui.proc.poll() is not None, timeout=15), gui.output.read_text()


@posix_only
def test_no_browser_mode_never_stops_on_its_own(gui_factory):
    gui = gui_factory(0, "--no-browser", idle=0.5)
    manager, browser, page = _page_when_up(gui)
    page.close()
    browser.close()
    manager.stop()
    time.sleep(2)
    assert gui.proc.poll() is None
    assert gui.log() == []


def _page_when_up(gui: Gui):
    assert launcher.wait_for_port(gui.port)
    return _page(gui.url)
