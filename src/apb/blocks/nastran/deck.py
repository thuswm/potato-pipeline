"""Scanning a Nastran input deck (spec 09 §3.5, §3.6). Pure functions.

Finds the files a deck ``INCLUDE``s (needed to notice a changed load file) and whether the
deck asks for HDF5 output (without it Nastran writes no ``.h5``).
"""

from __future__ import annotations

import re
from pathlib import Path

_INCLUDE = re.compile(r"""^\s*INCLUDE\s+(?:'([^']*)'|"([^"]*)"|(\S+))""", re.IGNORECASE)
_HDF5OUT = re.compile(r"^\s*HDF5OUT\b", re.IGNORECASE)
_MDLPRM_HDF5 = re.compile(r"^\s*MDLPRM\s*[,\s]\s*HDF5\b", re.IGNORECASE)

#: Largest include nesting followed (a cycle is detected earlier).
MAX_DEPTH = 20


class DeckError(ValueError):
    """A deck that cannot be scanned: a missing include or an include cycle."""


def _read(path: Path) -> str:
    return path.read_text(encoding="latin-1")  # decks are ASCII; never fails on odd bytes


def find_includes(deck: Path) -> list[Path]:
    r"""Return every file the deck includes, directly or through other includes.

    Paths are relative to the folder of the deck (where Nastran runs); ``\\`` is accepted as
    separator. Comment lines (``$``) are ignored.

    Args:
        deck: The input deck.

    Returns:
        The included files in the order they are found, each once.

    Raises:
        DeckError: If an included file does not exist or includes form a cycle.
    """
    folder = deck.parent
    found: list[Path] = []

    def scan(path: Path, chain: tuple[Path, ...]) -> None:
        if len(chain) > MAX_DEPTH:
            raise DeckError(f"Includes are nested more than {MAX_DEPTH} levels deep")
        for line in _read(path).splitlines():
            if line.lstrip().startswith("$"):
                continue
            match = _INCLUDE.match(line)
            if match is None:
                continue
            name = next(g for g in match.groups() if g is not None).strip().replace("\\", "/")
            target = Path(name) if Path(name).is_absolute() else folder / name
            resolved = target.resolve()
            if resolved in chain or resolved == path.resolve():
                raise DeckError(f"Include cycle: {path.name} includes {name}")
            if not target.is_file():
                raise DeckError(f"{path.name} includes {name}, which does not exist")
            if resolved not in [f.resolve() for f in found]:
                found.append(target)
                scan(target, (*chain, path.resolve()))

    scan(deck, ())
    return found


def has_hdf5_request(files: list[Path]) -> bool:
    """Return whether the deck (or an included file) asks for HDF5 output.

    Recognises ``HDF5OUT`` and ``MDLPRM,HDF5`` (fixed or free format, any case) and ignores
    comment lines.
    """
    for path in files:
        for line in _read(path).splitlines():
            if line.lstrip().startswith("$"):
                continue
            if _HDF5OUT.match(line) or _MDLPRM_HDF5.match(line):
                return True
    return False
