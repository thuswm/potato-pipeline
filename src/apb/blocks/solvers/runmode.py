"""The decision of spec 09 §3.2: run the solver, use the results, or refuse. Pure logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from apb.blocks.solvers.record import RunRecord


class RunMode(StrEnum):
    """When a solver block starts the solver."""

    NEVER = "never"
    IF_CHANGED = "if_changed"
    ALWAYS = "always"


class Action(StrEnum):
    """What the block does."""

    RUN = "run"
    USE = "use"
    ADOPT = "adopt"
    FAIL = "fail"


@dataclass(frozen=True)
class Results:
    """What exists on disk for the job.

    Attributes:
        files: Result file name -> modification time (seconds), for the files that exist
            (e.g. ``{"modal.f06": …, "modal.h5": …}``). Both result files are expected.
        expected: The names of the result files that must all exist.
        complete: The main output file (the ``.f06``) is complete.
        newest_input: Modification time of the newest input file (deck or include).
        record: The run record, if there is a usable one.
    """

    files: dict[str, float]
    expected: tuple[str, ...]
    complete: bool
    newest_input: float
    record: RunRecord | None = None

    @property
    def present(self) -> bool:
        """``True`` if all expected result files exist."""
        return all(name in self.files for name in self.expected)

    @property
    def missing(self) -> list[str]:
        """Names of the expected result files that do not exist."""
        return [name for name in self.expected if name not in self.files]

    @property
    def newer_than_input(self) -> bool:
        """``True`` if every result file is strictly newer than every input file."""
        return self.present and min(self.files[n] for n in self.expected) > self.newest_input


@dataclass(frozen=True)
class Decision:
    """The outcome of :func:`decide`.

    Attributes:
        action: What to do.
        reason: One sentence for the block log (and the error message for ``fail``).
        warning: A warning for the block log, e.g. results older than the input.
    """

    action: Action
    reason: str
    warning: str = ""


def decide(mode: RunMode, results: Results, input_hash: str, options: list[str]) -> Decision:
    """Decide what an analysis block does (spec 09 §3.2).

    Args:
        mode: The run mode.
        results: The result files and the run record found on disk.
        input_hash: Fingerprint of the current input files.
        options: Options of this run (compared with the record).

    Returns:
        The decision.
    """
    record = results.record
    if mode is RunMode.ALWAYS:
        return Decision(Action.RUN, "Run mode 'always': Nastran is started")
    if mode is RunMode.NEVER:
        if not results.present:
            return Decision(
                Action.FAIL,
                f"Result files not found: {', '.join(results.missing)}. Set the run mode to "
                "'If input changed' to run the analysis",
            )
        warning = ""
        if record is not None and record.complete and record.input_hash != input_hash:
            warning = "The input changed after these results were made"
        elif record is not None and not record.complete:
            warning = "The run that made these results did not finish"
        elif record is None and not results.newer_than_input:
            warning = "The input is newer than the results"
        return Decision(Action.USE, "Run mode 'never': the existing results are used", warning)
    # IF_CHANGED
    if not results.present:
        return Decision(Action.RUN, f"Result file(s) missing: {', '.join(results.missing)}")
    if record is not None:
        if record.complete and record.input_hash == input_hash and record.options == options:
            when = f"made {record.finished[:16].replace('T', ' ')} " if record.finished else ""
            return Decision(Action.USE, f"Results are up to date: {when}from the same input")
        if record.complete:
            return Decision(
                Action.RUN, "The input or the options changed since the results were made"
            )
        return Decision(Action.RUN, "A previous run did not finish")
    if not results.complete:
        return Decision(Action.RUN, "The existing .f06 is incomplete")
    if results.newer_than_input:
        return Decision(
            Action.ADOPT,
            "Results are newer than the input and are used (no run record: made outside the tool)",
        )
    return Decision(Action.RUN, "The existing results are older than the input")
