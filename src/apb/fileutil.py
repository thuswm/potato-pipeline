"""Safe writing of output files (Excel workbooks, Word reports)."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

#: Program that typically locks a file type on Windows, for the error message.
_APPS = {".xlsx": "Excel", ".docx": "Word"}


class OutputFileError(Exception):
    """Raised when an output file cannot be written, e.g. because it is open in Word/Excel."""


@contextlib.contextmanager
def atomic_output(path: Path) -> Iterator[Path]:
    """Yield a temporary path to write to; on success it replaces ``path``.

    A failed write never leaves a half-written file, and an existing file stays intact.
    Parent folders are created.

    Args:
        path: Final output file.

    Yields:
        Temporary file in the same folder (same suffix).

    Raises:
        OutputFileError: If ``path`` cannot be replaced (on Windows: open in another program).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=path.suffix, prefix=".apb-", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        yield tmp
        try:
            tmp.replace(path)
        except PermissionError as exc:
            app = _APPS.get(path.suffix.lower(), "the program using it")
            raise OutputFileError(
                f"Cannot write {path.name}: the file is in use. Close it in {app} and run again."
            ) from exc
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
