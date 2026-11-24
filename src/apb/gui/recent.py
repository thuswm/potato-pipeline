"""Recently opened pipelines of this computer (spec 06 §3.8, GUI-38).

The list is kept in ``recent.json`` next to the config file (:func:`apb.config.config_path`),
newest first, at most :data:`MAX_RECENT` entries. It belongs to the computer, not to a
pipeline. A missing or broken file counts as an empty list.
"""

from __future__ import annotations

import json
from pathlib import Path

from apb.config import config_path
from apb.fileutil import OutputFileError, atomic_output

#: Number of pipelines kept in the list.
MAX_RECENT = 10


def recent_path() -> Path:
    """Return the file holding the list: ``recent.json`` in the config file's folder."""
    return config_path().parent / "recent.json"


def recent_files(path: Path | None = None) -> list[Path]:
    """Return the recent pipelines, newest first (files that no longer exist included).

    Args:
        path: List file (default: :func:`recent_path`).
    """
    target = path or recent_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("files") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return [Path(e) for e in entries if isinstance(e, str) and e][:MAX_RECENT]


def _write(files: list[Path], path: Path | None) -> None:
    target = path or recent_path()
    text = json.dumps({"files": [str(f) for f in files]}, indent=2) + "\n"
    try:
        with atomic_output(target) as tmp:
            tmp.write_text(text, encoding="utf-8")
    except (OSError, OutputFileError):
        pass  # the list is a convenience: never fail an open or a save because of it


def add_recent(pipeline: Path, path: Path | None = None) -> list[Path]:
    """Put a pipeline at the top of the list (moving it if it is already there).

    Args:
        pipeline: The pipeline file that was opened or saved.
        path: List file (default: :func:`recent_path`).

    Returns:
        The new list.
    """
    entry = pipeline.expanduser().resolve()
    files = [entry, *(f for f in recent_files(path) if f != entry)][:MAX_RECENT]
    _write(files, path)
    return files


def clear_recent(path: Path | None = None) -> None:
    """Empty the list.

    Args:
        path: List file (default: :func:`recent_path`).
    """
    _write([], path)
