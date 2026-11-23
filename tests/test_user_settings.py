"""Settings of this computer: config file writing and the dialog's checks (GUI-36)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apb.blocks.nastran import command as cmd
from apb.config import ConfigError, read_section, write_section
from apb.gui.analysis_panel import inherited
from apb.gui.user_settings import (
    SettingsError,
    UserSettings,
    load_user_settings,
    save_user_settings,
    to_sections,
)


def test_write_section_keeps_comments_and_other_keys(tmp_path: Path):
    config = tmp_path / "apb" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        '# my settings\n[gui]\nwindow = "browser"  # at work\n\n[nastran]\ncommand = "x"\n',
        encoding="utf-8",
    )
    write_section("nastran", {"path": "C:/msc/bin", "command": ""}, config)
    text = config.read_text(encoding="utf-8")
    assert "# my settings" in text and "# at work" in text
    assert read_section("nastran", config) == {"path": "C:/msc/bin"}  # empty = removed
    write_section("nastran", {"path": None}, config)
    assert "[nastran]" not in config.read_text(encoding="utf-8")  # empty section removed
    assert read_section("gui", config) == {"window": "browser"}


def test_write_section_creates_the_file_and_refuses_broken_ones(tmp_path: Path):
    config = tmp_path / "new" / "config.toml"
    write_section("nastran", {"options": ["mem=4gb"]}, config)
    assert read_section("nastran", config) == {"options": ["mem=4gb"]}
    config.write_text("[nastran\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot be read"):
        write_section("nastran", {"path": "x"}, config)


def test_settings_round_trip(tmp_path: Path):
    config = tmp_path / "config.toml"
    assert load_user_settings(config) == UserSettings()
    chosen = UserSettings(
        nastran_command="nastran {input} {options}",
        nastran_path="/opt/msc/bin",
        nastran_options="mem=4gb; smp=4",
        window="browser",
        idle_seconds="12",
    )
    save_user_settings(chosen, config)
    loaded = load_user_settings(config)
    assert loaded.nastran_options == "mem=4gb, smp=4"
    assert loaded.window == "browser" and loaded.idle_seconds == "12"
    assert cmd.load_defaults(config).path == "/opt/msc/bin"  # what the block uses
    save_user_settings(UserSettings(), config)  # all defaults again
    assert read_section("nastran", config) == {} and read_section("gui", config) == {}


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (UserSettings(nastran_options="mem 4gb"), "key=value"),
        (UserSettings(nastran_command="nastran {deck}"), "Unknown placeholder"),
        (UserSettings(window="tab"), "Unknown window mode"),
        (UserSettings(idle_seconds="soon"), "number of seconds"),
        (UserSettings(idle_seconds="0"), "more than 0"),
    ],
)
def test_invalid_values_are_refused(values: UserSettings, message: str):
    with pytest.raises(SettingsError, match=message):
        to_sections(values)


def test_a_block_shows_what_it_inherits(tmp_path: Path):
    config = tmp_path / "config.toml"
    assert inherited(config)["command"] == f"default: {cmd.DEFAULT_COMMAND}"
    save_user_settings(UserSettings(nastran_path="/opt/msc/bin", nastran_options="mem=4gb"), config)
    shown = inherited(config)
    assert shown["path"] == "settings: /opt/msc/bin" and shown["options"] == "settings: mem=4gb"
