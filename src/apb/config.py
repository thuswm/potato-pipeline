r"""The computer's config file ``config.toml`` (shared by the Nastran block and the GUI).

Location: ``$APB_CONFIG`` if set, else ``~/.config/apb/config.toml`` (Linux/macOS, or
``$XDG_CONFIG_HOME``) or ``%APPDATA%\apb\config.toml`` (Windows). Sections: ``[nastran]``
(spec 09 §3.3) and ``[gui]`` (spec 06 §3.6).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


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
