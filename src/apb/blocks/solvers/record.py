"""The run record next to a solver's results (spec 09 §3.6).

``<job>.apb-run.json`` says what the results were made from, so that "did the input change
since?" has an answer. It is written as ``running`` before the solver starts and completed
when it ends; a crash or *Stop* leaves ``running``. Results taken over from outside the tool
get the status ``adopted``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from apb import __version__

SCHEMA = 1
RUNNING, FINISHED, ADOPTED = "running", "finished", "adopted"


@dataclass
class RunRecord:
    """What a solver run was made from.

    Attributes:
        status: ``running``, ``finished`` or ``adopted``.
        input_hash: Combined hash of the input files.
        inputs: ``[{"path", "sha256"}]`` of the input files.
        options: The options of the run (part of the comparison).
        command: The command that was run (for the analyst's records).
        started: Start time (ISO 8601).
        finished: End time (ISO 8601), empty while running.
        exit_code: Exit code of the solver.
        duration: Seconds.
        tool_version: Version of this tool.
    """

    status: str
    input_hash: str
    inputs: list[dict[str, str]] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    command: str = ""
    started: str = ""
    finished: str = ""
    exit_code: int | None = None
    duration: float = 0.0
    tool_version: str = __version__

    @property
    def complete(self) -> bool:
        """``True`` for a record of a run that finished (or results that were adopted)."""
        return self.status in (FINISHED, ADOPTED)


def record_path(deck: Path) -> Path:
    """Return the record file of a job: ``<deck folder>/<job>.apb-run.json``."""
    return deck.parent / f"{deck.stem}.apb-run.json"


def read_record(path: Path) -> RunRecord | None:
    """Read a record; ``None`` if it is missing, damaged or from another layout."""
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return None
        fields = {k: v for k, v in data.items() if k in RunRecord.__dataclass_fields__}
        record = RunRecord(**fields)
    except (OSError, ValueError, TypeError):
        return None
    return record if record.status in (RUNNING, FINISHED, ADOPTED) else None


def write_record(path: Path, record: RunRecord) -> None:
    """Write a record atomically.

    Raises:
        OSError: If it cannot be written.
    """
    data = {"schema": SCHEMA, **asdict(record)}
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
