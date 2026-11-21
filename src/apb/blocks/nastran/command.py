"""How Nastran is started: the shell command, the path setting, the config file (spec 09 §3.3).

The command and the path can be set in the block and as the default of the computer in a
config file; the block wins over the file, which wins over the built-in default.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import string
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from apb.config import ConfigError, config_path, read_section

#: The command typed at work (``nastran job.bdf``) plus the keywords that make it wait.
DEFAULT_COMMAND = "nastran {input} batch=no old=no scr=yes {options}"

#: Names that can be used between braces in a command.
PLACEHOLDERS = ("input", "input_path", "job", "folder", "options")

_OPTION = re.compile(r"[A-Za-z][A-Za-z0-9_]*=[A-Za-z0-9_.:/\\+-]+")
_COMPOUND = re.compile(r"&&|\|\||[;|<>`$]")


class CommandError(ValueError):
    """An invalid command, option or config file; the message is for the user."""


@dataclass(frozen=True)
class Defaults:
    """The defaults of the computer from the config file.

    Attributes:
        command: Command template (empty = built-in default).
        path: Folder(s) in front of ``PATH``.
        options: Options added to every run on this computer.
    """

    command: str = ""
    path: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    """The command settings of one run, after merging block, config file and defaults.

    Attributes:
        command: The command template.
        path: Folder(s) in front of ``PATH`` (empty = ``PATH`` as it is).
        options: The merged options for ``{options}``.
        command_from: ``block``, ``config file`` or ``default``.
        path_from: ``block``, ``config file`` or ``PATH``.
    """

    command: str
    path: str
    options: tuple[str, ...]
    command_from: str
    path_from: str


def validate_option(option: str) -> str:
    """Check a Nastran keyword ``key=value`` (no spaces, no shell characters).

    Raises:
        CommandError: If it is not of that form.
    """
    if not _OPTION.fullmatch(option):
        raise CommandError(
            f"Option {option!r} must be of the form key=value without spaces or shell "
            "characters, e.g. mem=4gb"
        )
    return option


def check_template(command: str) -> str:
    """Check that a command only uses the known placeholders.

    Raises:
        CommandError: For unknown placeholders or broken braces.
    """
    try:
        names = [n for _, n, _, _ in string.Formatter().parse(command) if n]
    except ValueError as exc:
        raise CommandError(f"Cannot read the command: {exc}") from exc
    unknown = [n for n in names if n not in PLACEHOLDERS]
    if unknown:
        raise CommandError(
            f"Unknown placeholder {{{unknown[0]}}} in the command. Available: "
            + ", ".join(f"{{{p}}}" for p in PLACEHOLDERS)
        )
    return command


def load_defaults(path: Path | None = None) -> Defaults:
    """Read the ``[nastran]`` section of the config file (none = no defaults).

    Raises:
        CommandError: If the file cannot be read or has wrong content.
    """
    target = path or config_path()
    try:
        section = read_section("nastran", target)
    except ConfigError as exc:
        raise CommandError(str(exc)) from exc
    try:
        command, folder = str(section.get("command", "")), str(section.get("path", ""))
        options = tuple(validate_option(str(o)) for o in section.get("options", []))
        if command:
            check_template(command)
    except (CommandError, TypeError) as exc:
        raise CommandError(f"Config file {target}: {exc}") from exc
    return Defaults(command, folder, options)


def merge_options(*groups: Sequence[str]) -> tuple[str, ...]:
    """Merge option lists; a later keyword replaces an earlier one with the same name."""
    merged: dict[str, str] = {}
    for group in groups:
        for option in group:
            merged[option.split("=", 1)[0].lower()] = option
    return tuple(merged.values())


def resolve(
    command: str, path: str, options: Sequence[str], defaults: Defaults | None = None
) -> Settings:
    """Merge the settings of the block, the config file and the built-in defaults."""
    defaults = defaults or Defaults()
    chosen_command, command_from = (
        (command, "block")
        if command.strip()
        else (defaults.command, "config file")
        if defaults.command
        else (DEFAULT_COMMAND, "default")
    )
    chosen_path, path_from = (
        (path, "block")
        if path.strip()
        else (defaults.path, "config file")
        if defaults.path
        else ("", "PATH")
    )
    return Settings(
        check_template(chosen_command),
        chosen_path,
        merge_options(defaults.options, [validate_option(o) for o in options]),
        command_from,
        path_from,
    )


def _quote(text: str) -> str:
    return subprocess.list2cmdline([text]) if os.name == "nt" else shlex.quote(text)


def build_command(settings: Settings, deck: Path) -> str:
    """Fill the placeholders of the command for one deck.

    Args:
        settings: The merged settings.
        deck: The input deck; Nastran runs in its folder.

    Returns:
        The command line for the shell. Paths and names are quoted where needed.
    """
    values = {
        "input": _quote(deck.name),
        "input_path": _quote(str(deck.resolve())),
        "job": _quote(deck.stem),
        "folder": _quote(str(deck.resolve().parent)),
        "options": " ".join(settings.options),
    }
    return " ".join(string.Formatter().vformat(settings.command, (), values).split())


def environment(settings: Settings, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the environment for the command: the current one, ``path`` in front of ``PATH``."""
    env = dict(os.environ if base is None else base)
    if settings.path.strip():
        existing = env.get("PATH", "")
        env["PATH"] = settings.path.strip() + (os.pathsep + existing if existing else "")
    return env


@dataclass(frozen=True)
class ProgramStatus:
    """Whether the program of a command is found (shown in the panel).

    Attributes:
        program: The first word of the command.
        found: Full path of the program, or ``None``.
        checked: ``False`` if the command is too complex to check (several commands, pipes).
    """

    program: str
    found: str | None
    checked: bool = True


def program_status(command_line: str, env: Mapping[str, str]) -> ProgramStatus:
    """Check that the first word of a command is found on the (extended) ``PATH``."""
    if _COMPOUND.search(command_line):
        return ProgramStatus("", None, checked=False)
    try:
        words = shlex.split(command_line, posix=os.name != "nt")
    except ValueError:
        return ProgramStatus("", None, checked=False)
    if not words:
        return ProgramStatus("", None, checked=False)
    program = words[0].strip("\"'")
    found = shutil.which(program, path=env.get("PATH"))
    return ProgramStatus(program, found)
