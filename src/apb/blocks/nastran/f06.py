"""Reading the messages of an MSC Nastran ``.f06`` file (spec 09 §3.5). Pure functions.

A message is a block such as::

     *** USER WARNING MESSAGE 9058 (SUBDMAP SESTATIC)
         THE SOLUTION FOR THE RESIDUAL STRUCTURE AND THE APPLIED LOADS ... ARE ZERO.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

#: Lines of text collected for one message.
MAX_TEXT_LINES = 10

_START = re.compile(
    r"^\s*\*\*\*\s+(?:USER|SYSTEM)\s+(INFORMATION|WARNING|ALARM|FATAL)\s+MESSAGE\s*(\d+)?\s*(.*?)\s*$"
)
_PAGE_HEADER = re.compile(r"^1\s+.*\bPAGE\s+\d+\s*$")
_END_OF_JOB = re.compile(r"\*\s\*\s\*\s+END OF JOB\s+\*\s\*\s\*")


class Severity(StrEnum):
    """Severity of a message, from the worst."""

    FATAL = "FATAL"
    WARNING = "WARNING"
    INFORMATION = "INFORMATION"

    @property
    def rank(self) -> int:
        """Sort order (fatal first)."""
        return ("FATAL", "WARNING", "INFORMATION").index(self.value)


@dataclass(frozen=True)
class Message:
    """One message of the ``.f06``, possibly occurring several times.

    Attributes:
        severity: ``FATAL``, ``WARNING`` (also Nastran's ALARM) or ``INFORMATION``.
        number: The message number (0 if it has none).
        source: The module or subDMAP in parentheses (e.g. ``SUBDMAP SESTATIC``).
        text: The message text, lines joined.
        line: Line number of the first occurrence (1-based).
        count: How often the message occurs.
    """

    severity: Severity
    number: int
    source: str
    text: str
    line: int
    count: int = 1


@dataclass(frozen=True)
class F06Report:
    """What was found in a ``.f06``.

    Attributes:
        messages: Messages, grouped, worst first, then by line.
        finished: The file contains ``* * * END OF JOB * * *``.
        lines: Number of lines read.
    """

    messages: tuple[Message, ...]
    finished: bool
    lines: int

    def of(self, severity: Severity) -> list[Message]:
        """Return the messages of one severity."""
        return [m for m in self.messages if m.severity is severity]


def read_text(path: Path) -> str:
    """Read an ``.f06`` as text (undecodable bytes are replaced; the file may be large)."""
    return path.read_text(encoding="utf-8", errors="replace")


def parse_f06(text: str) -> F06Report:
    """Find the messages of an ``.f06`` and whether the job finished.

    Args:
        text: Content of the file.

    Returns:
        The report. Repeated messages (same severity, number, source and text) are one
        entry with a count and the line of the first occurrence.
    """
    lines = text.splitlines()
    groups: dict[tuple[Severity, int, str, str], Message] = {}
    i = 0
    while i < len(lines):
        match = _START.match(lines[i])
        if match is None:
            i += 1
            continue
        word, number, rest = match.groups()
        severity = {"FATAL": Severity.FATAL, "INFORMATION": Severity.INFORMATION}.get(
            word, Severity.WARNING
        )
        source, body = "", []
        if rest.startswith("(") and rest.endswith(")"):
            source = rest[1:-1].strip()
        elif rest:
            body.append(rest)
        first = i + 1
        i += 1
        while i < len(lines) and len(body) < MAX_TEXT_LINES:
            line = lines[i]
            if (
                not line.strip()
                or not line.startswith(" ")
                or _START.match(line)
                or _PAGE_HEADER.match(line)
            ):
                break
            body.append(line.strip())
            i += 1
        text_joined = " ".join(body)
        key = (severity, int(number or 0), source, text_joined)
        known = groups.get(key)
        if known is None:
            groups[key] = Message(severity, int(number or 0), source, text_joined, first)
        else:
            groups[key] = Message(
                severity, known.number, source, text_joined, known.line, known.count + 1
            )
    ordered = sorted(groups.values(), key=lambda m: (m.severity.rank, m.line))
    return F06Report(tuple(ordered), _END_OF_JOB.search(text) is not None, len(lines))
