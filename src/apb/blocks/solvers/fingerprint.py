"""Fingerprint of a solver's input files (spec 09 §3.6)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path


def fingerprint(files: Sequence[Path], base: Path) -> tuple[str, list[dict[str, str]]]:
    """Return a hash over the content of the input files.

    Args:
        files: The input deck and every file it includes.
        base: Folder the names in the listing are relative to (the deck's folder).

    Returns:
        ``(hash, listing)``: the combined SHA-256 and ``[{"path", "sha256"}]`` per file, in
        the given order, for the run record.

    Raises:
        OSError: If a file cannot be read.
    """
    listing = []
    for path in files:
        try:
            name = path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            name = path.resolve().as_posix()
        listing.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    combined = hashlib.sha256(json.dumps(listing, sort_keys=True).encode("utf-8")).hexdigest()
    return combined, listing
