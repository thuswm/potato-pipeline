"""What the panel of the analysis block shows, computed without running Nastran (spec 09 §3.9).

Pure functions: given the parameters as stored, report whether the program is found, what
the input is, which result files exist and how they relate to the input.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from apb.blocks.nastran import command as cmd
from apb.blocks.nastran.analysis import NastranRunParams, job_files
from apb.blocks.nastran.deck import DeckError, find_includes, has_hdf5_request
from apb.blocks.nastran.f06 import parse_f06, read_text
from apb.blocks.solvers.fingerprint import fingerprint
from apb.blocks.solvers.record import read_record, record_path


@dataclass(frozen=True)
class ResultFile:
    """One result file next to the deck.

    Attributes:
        name: File name.
        path: Full path.
        exists: Whether it exists.
        modified: Modification time as ``2026-11-15 14:25`` (empty if it does not exist).
    """

    name: str
    path: Path
    exists: bool
    modified: str = ""


@dataclass(frozen=True)
class AnalysisStatus:
    """The status lines of the analysis panel.

    Attributes:
        problem: Why nothing else could be determined (invalid settings, missing deck ...).
        command: The command that would be run.
        command_from: ``block``, ``config file`` or ``default``.
        program: What was found for the first word of the command.
        path_from: ``block``, ``config file`` or ``PATH``.
        deck: The input deck.
        deck_exists: Whether the deck exists.
        includes: Number of included files.
        modified: Modification time of the deck.
        hdf5_request: Whether the deck asks for HDF5 output (``None`` if unknown).
        results: The ``.f06`` and ``.h5``.
        relation: How the results relate to the input, in words.
        notes: Further hints, e.g. a config file problem.
    """

    problem: str = ""
    command: str = ""
    command_from: str = ""
    program: cmd.ProgramStatus | None = None
    path_from: str = ""
    deck: Path | None = None
    deck_exists: bool = False
    includes: int = 0
    modified: str = ""
    hdf5_request: bool | None = None
    results: list[ResultFile] = field(default_factory=list)
    relation: str = ""
    notes: list[str] = field(default_factory=list)


def _time(seconds: float) -> str:
    return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M")


def analysis_status(
    stored: dict[str, Any],
    base_dir: Path,
    resolve: Callable[[Any], Any] = lambda value: value,
) -> AnalysisStatus:
    """Describe an analysis block for its panel.

    Args:
        stored: The block's parameters as stored in the pipeline (``${variables}`` allowed).
        base_dir: Folder that relative paths refer to.
        resolve: Replaces ``${variable}`` references.

    Returns:
        The status. It never raises: problems are reported in ``problem`` and ``notes``.
    """
    try:
        params = NastranRunParams.model_validate(resolve(dict(stored)))
    except ValidationError as exc:
        if not stored.get("input_file"):
            return AnalysisStatus(problem="Choose the input deck")
        error = exc.errors()[0]
        where = ".".join(str(p) for p in error["loc"])
        message = str(error["msg"]).removeprefix("Value error, ")
        return AnalysisStatus(problem=f"{where}: {message}" if where else message)
    except (KeyError, ValueError) as exc:
        return AnalysisStatus(problem=str(exc))
    deck = Path(params.input_file).expanduser()
    deck = deck if deck.is_absolute() else (base_dir / deck).resolve()
    notes: list[str] = []
    try:
        defaults = cmd.load_defaults()
        settings = cmd.resolve(params.command, params.path, params.options, defaults)
    except cmd.CommandError as exc:
        return AnalysisStatus(problem=str(exc), deck=deck)
    command = cmd.build_command(settings, deck)
    program = cmd.program_status(command, cmd.environment(settings))
    files = job_files(deck)
    results = [
        ResultFile(p.name, p, p.is_file(), _time(p.stat().st_mtime) if p.is_file() else "")
        for p in (files["f06"], files["h5"])
    ]
    status = AnalysisStatus(
        command=command,
        command_from=settings.command_from,
        program=program,
        path_from=settings.path_from,
        deck=deck,
        deck_exists=deck.is_file(),
        results=results,
        notes=notes,
    )
    if not deck.is_file():
        return _with(status, problem=f"Input deck not found: {deck}")
    try:
        includes = find_includes(deck)
        everything = [deck, *includes]
        digest, _ = fingerprint(everything, deck.parent)
    except (DeckError, OSError) as exc:
        return _with(status, problem=str(exc))
    relation = _relation(deck, everything, digest, results)
    return _with(
        status,
        includes=len(includes),
        modified=_time(deck.stat().st_mtime),
        hdf5_request=has_hdf5_request(everything),
        relation=relation,
    )


def _with(status: AnalysisStatus, **changes: Any) -> AnalysisStatus:
    from dataclasses import replace

    return replace(status, **changes)


def _relation(deck: Path, inputs: list[Path], digest: str, results: list[ResultFile]) -> str:
    """Say how the existing results relate to the input (the three states of spec 09 §3.2)."""
    if not all(r.exists for r in results):
        return "no results yet" if not any(r.exists for r in results) else "result file(s) missing"
    record = read_record(record_path(deck))
    if record is not None:
        if not record.complete:
            return "made by this tool, but the run did not finish"
        if record.input_hash == digest:
            return "made by this tool, input unchanged"
        return "made by this tool, input changed since"
    newest = max(p.stat().st_mtime for p in inputs)
    complete = parse_f06(read_text(results[0].path)).finished
    newer = min(r.path.stat().st_mtime for r in results) > newest
    if newer and complete:
        return "made outside the tool, newer than the input (will be used)"
    if not complete:
        return "made outside the tool, the .f06 is incomplete (will be replaced)"
    return "made outside the tool, older than the input (will be replaced)"
