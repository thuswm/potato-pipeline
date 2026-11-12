"""Events emitted while a pipeline runs (ENG-06, ENG-11)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


class BlockState(StrEnum):
    """Execution state of a block."""

    IDLE = "idle"
    STALE = "stale"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


class EventKind(StrEnum):
    """Kind of a run event."""

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    BLOCK_STARTED = "block_started"
    BLOCK_FINISHED = "block_finished"
    BLOCK_CACHED = "block_cached"
    BLOCK_FAILED = "block_failed"
    BLOCK_SKIPPED = "block_skipped"
    PROGRESS = "progress"
    LOG = "log"


@dataclass(frozen=True)
class RunEvent:
    """One event of a run.

    Attributes:
        kind: What happened.
        block_id: Block concerned (``None`` for run-level events).
        message: Text (log line, error message, ...).
        detail: Extra text, e.g. a traceback.
        progress: Progress fraction for ``PROGRESS`` events.
        timestamp: Time of the event (seconds since the epoch).
    """

    kind: EventKind
    block_id: str | None = None
    message: str = ""
    detail: str = ""
    progress: float = 0.0
    timestamp: float = field(default_factory=time.time)


type EventSink = Callable[[RunEvent], None]
