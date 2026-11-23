r"""The computer's config file ``config.toml`` (shared by the Nastran block and the GUI).

Location: ``$APB_CONFIG`` if set, else ``~/.config/apb/config.toml`` (Linux/macOS, or
``$XDG_CONFIG_HOME``) or ``%APPDATA%\apb\config.toml`` (Windows). Sections: ``[nastran]``
(spec 09 §3.3) and ``[gui]`` (spec 06 §3.6).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import Table

from apb.fileutil import OutputFileError, atomic_output


class ConfigError(Exception):
    """Raised when the config file exists but cannot be read."""


def config_dir() -> Path:
    """Return the tool's folder in the user's config folder (it may not exist yet)."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "apb"


def config_path() -> Path:
    """Return the config file: ``$APB_CONFIG``, else ``config.toml`` in :func:`config_dir`."""
    override = os.environ.get("APB_CONFIG")
    if override:
        return Path(override)
    return config_dir() / "config.toml"


def read_section(name: str, path: Path | None = None) -> dict[str, Any]:
    """Return one ``[section]`` of the config file (empty if the file or section is missing).

    Args:
        name: Section name, e.g. ``"gui"``.
        path: Config file (default: :func:`config_path`).

    Raises:
        ConfigError: If the file exists but cannot be read or is not valid TOML, or the
            section is not a table.
    """
    target = path or config_path()
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Config file {target} cannot be read: {exc}") from exc
    section = data.get(name, {})
    if not isinstance(section, dict):
        raise ConfigError(f"[{name}] in the config file {target} must be a section")
    return section


def write_section(name: str, values: Mapping[str, Any], path: Path | None = None) -> Path:
    """Set the keys of one ``[section]`` of the config file and write it.

    Other sections, other keys of the section and comments are kept (``tomlkit``). A value
    that is ``None``, ``""`` or an empty list removes its key, so the built-in default
    applies again; an empty section is removed. The file and its folder are created if
    needed; the write is atomic.

    Args:
        name: Section name, e.g. ``"nastran"``.
        values: Key -> value; only these keys are changed.
        path: Config file (default: :func:`config_path`).

    Returns:
        The config file written.

    Raises:
        ConfigError: If the existing file cannot be read or written.
    """
    target = path or config_path()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except OSError as exc:
        raise ConfigError(f"Config file {target} cannot be read: {exc}") from exc
    try:
        doc = tomlkit.parse(text)
    except tomlkit.exceptions.TOMLKitError as exc:
        raise ConfigError(f"Config file {target} cannot be read: {exc}") from exc
    section = doc.get(name)
    if section is None:
        section = tomlkit.table()
        doc[name] = section
    elif not isinstance(section, Table):
        raise ConfigError(f"[{name}] in the config file {target} must be a section")
    for key, value in values.items():
        if value is None or value == "" or value == []:
            section.pop(key, None)
        else:
            section[key] = value
    if not section:
        doc.pop(name, None)
    try:
        with atomic_output(target) as tmp:
            tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    except (OSError, OutputFileError) as exc:
        raise ConfigError(f"Config file {target} cannot be written: {exc}") from exc
    return target
