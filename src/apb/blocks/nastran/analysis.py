"""The ``nastran.run`` block: run MSC Nastran from the pipeline (spec 09).

The block points at an input deck, starts Nastran when needed (run mode), delivers the result
files and checks the ``.f06`` for warnings and errors. Results are written next to the deck.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apb.blocks.nastran import command as cmd
from apb.blocks.nastran.deck import DeckError, find_includes, has_hdf5_request
from apb.blocks.nastran.f06 import F06Report, Message, Severity, parse_f06, read_text
from apb.blocks.solvers.fingerprint import fingerprint
from apb.blocks.solvers.process import run_command
from apb.blocks.solvers.record import (
    ADOPTED,
    FINISHED,
    RUNNING,
    RunRecord,
    read_record,
    record_path,
    write_record,
)
from apb.blocks.solvers.runmode import Action, Results, RunMode, decide
from apb.config import config_path
from apb.engine.block import (
    NASTRAN_DECK_TYPES,
    Block,
    BlockCancelledError,
    FilePath,
    RunContext,
    file_types,
)
from apb.engine.types import Port, PortType

#: Files Nastran writes for a job that the block deletes before a run, so that a failing
#: run can never be mistaken for a fresh one.
RESULT_SUFFIXES = (".f06", ".f04", ".log", ".h5")


class NastranError(ValueError):
    """The analysis cannot be run or its results are not valid; the message is for the user."""


class NastranRunParams(BaseModel):
    """Parameters of :class:`NastranRun`."""

    model_config = ConfigDict(extra="forbid")

    input_file: FilePath = Field(
        description="Nastran input deck (.bdf, .dat, ...); the results are written next to it",
        json_schema_extra=file_types(*NASTRAN_DECK_TYPES),
    )
    run_mode: RunMode = Field(
        default=RunMode.IF_CHANGED,
        description="never: use existing results; if_changed: run when the input changed; "
        "always: run every time the pipeline is run",
    )
    command: str = Field(
        default="",
        description="Shell command that starts Nastran; empty = default of the computer "
        f"({cmd.DEFAULT_COMMAND})",
    )
    path: str = Field(
        default="",
        description="Folder(s) with the Nastran program, put in front of PATH; empty = default "
        "of the computer",
    )
    options: list[str] = Field(
        default_factory=list, description="Nastran keywords for {options}, e.g. mem=4gb"
    )
    timeout_minutes: float | None = Field(
        title="Time limit [min]",
        default=None,
        gt=0,
        description="Stop Nastran after this time; empty = no limit",
    )
    fail_on: Literal["fatal", "warning", "never"] = Field(
        default="fatal", description="Which messages in the .f06 fail the block"
    )
    ignore_messages: list[int] = Field(
        title="Ignore message numbers",
        default_factory=list,
        description="Warning numbers that are not counted, e.g. 9058",
    )
    include_information: bool = Field(
        title="List information messages",
        default=False,
        description="Also list the INFORMATION messages",
    )

    @field_validator("options")
    @classmethod
    def _options(cls, value: list[str]) -> list[str]:
        return [cmd.validate_option(o) for o in value]

    @field_validator("command")
    @classmethod
    def _command(cls, value: str) -> str:
        return cmd.check_template(value) if value.strip() else value


def job_files(deck: Path) -> dict[str, Path]:
    """Return the result files of a deck's job, next to the deck."""
    return {s.lstrip("."): deck.with_suffix(s) for s in RESULT_SUFFIXES}


def messages_table(
    report: F06Report, f06: Path, *, include_information: bool, ignore: set[int]
) -> pd.DataFrame:
    """Build the ``messages`` output: the warnings and errors of the ``.f06`` (spec 09 §3.5)."""
    shown = [
        m
        for m in report.messages
        if (m.severity is Severity.INFORMATION and include_information)
        or (m.severity is Severity.WARNING and m.number not in ignore)
        or m.severity is Severity.FATAL
    ]
    table = pd.DataFrame(
        {
            "SEVERITY": pd.Series([m.severity.value for m in shown], dtype="str"),
            "NUMBER": pd.Series([m.number for m in shown], dtype="int64"),
            "MODULE": pd.Series([m.source for m in shown], dtype="str"),
            "COUNT": pd.Series([m.count for m in shown], dtype="int64"),
            "LINE": pd.Series([m.line for m in shown], dtype="int64"),
            "TEXT": pd.Series([m.text for m in shown], dtype="str"),
        }
    )
    table.attrs["title"] = "Nastran messages"
    table.attrs["source_file"] = str(f06)
    return table


def _describe(message: Message, *, severity: bool = True) -> str:
    where = f" ({message.source})" if message.source else ""
    times = f" x{message.count}" if message.count > 1 else ""
    number = f"{message.number}" if message.number else ""
    head = f"{message.severity.value} {number}".strip() if severity else number
    return f"{head}{where}{times}: {message.text}"


def _time(seconds: float) -> str:
    return datetime.fromtimestamp(seconds).astimezone().isoformat(timespec="seconds")


class NastranRun(Block[NastranRunParams]):
    """Run MSC Nastran on an input deck and check the results."""

    type_id = "nastran.run"
    category = "Input/Nastran"
    title = "MSC Nastran analysis"
    description = (
        "Runs MSC Nastran on a .bdf (when needed, see run mode), delivers the .f06 and .h5 "
        "and lists the warnings and errors of the .f06."
    )
    outputs = (
        Port("h5", PortType.TEXT, label="HDF5 results"),
        Port("f06", PortType.TEXT, label="F06 file"),
        Port("messages", PortType.TABLE, label="Messages"),
        Port("status", PortType.TEXT, label="Status"),
    )
    Params = NastranRunParams
    advanced_params = (
        "command",
        "path",
        "options",
        "timeout_minutes",
        "ignore_messages",
        "include_information",
    )

    @classmethod
    def always_runs(cls, params: BaseModel | None) -> bool:
        """Run mode ``always`` bypasses the cache (ENG-20)."""
        return isinstance(params, NastranRunParams) and params.run_mode is RunMode.ALWAYS

    def input_files(self, params: NastranRunParams, ctx: RunContext) -> list[Path]:
        """The deck and the files it includes: a change of one of them invalidates the cache."""
        deck = ctx.resolve_path(params.input_file)
        try:
            return [deck, *find_includes(deck)] if deck.is_file() else [deck]
        except (DeckError, OSError):
            return [deck]  # the problem is reported when the block runs

    def output_files(
        self, params: NastranRunParams, outputs: Mapping[str, Any], ctx: RunContext
    ) -> list[Path]:
        """Return the ``.f06`` and ``.h5``: the block is done only while they exist."""
        return [Path(outputs["f06"]), Path(outputs["h5"])]

    # ---- run ----------------------------------------------------------------------------

    def run(
        self, params: NastranRunParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Decide what to do (spec 09 §3.2), run Nastran if needed, check the results."""
        deck = ctx.resolve_path(params.input_file)
        if not deck.is_file():
            raise NastranError(f"Input deck not found: {deck}")
        files = job_files(deck)
        f06, h5 = files["f06"], files["h5"]
        try:
            includes = find_includes(deck)
            inputs_all = [deck, *includes]
            digest, listing = fingerprint(inputs_all, deck.parent)
            settings = cmd.resolve(params.command, params.path, params.options, cmd.load_defaults())
        except (DeckError, cmd.CommandError, OSError) as exc:
            raise NastranError(str(exc)) from exc
        if includes:
            ctx.log(f"Input: {deck.name} + {len(includes)} included file(s)")

        existing = {p.name: p.stat().st_mtime for p in (f06, h5) if p.is_file()}
        record = read_record(record_path(deck))
        decision = decide(
            params.run_mode,
            Results(
                files=existing,
                expected=(f06.name, h5.name),
                complete=f06.is_file() and parse_f06(read_text(f06)).finished,
                newest_input=max(p.stat().st_mtime for p in inputs_all),
                record=record,
            ),
            digest,
            list(settings.options),
        )
        ctx.log(decision.reason)
        if decision.warning:
            ctx.log(f"WARNING: {decision.warning}")
        if decision.action is Action.FAIL:
            raise NastranError(decision.reason)
        if decision.action is Action.RUN:
            self._execute(params, ctx, deck, inputs_all, settings, digest, listing)
        elif decision.action is Action.ADOPT:
            self._write_record(
                deck,
                RunRecord(
                    ADOPTED,
                    digest,
                    listing,
                    list(settings.options),
                    "",
                    "",
                    _time(max(existing.values())),
                ),
            )
        return self._check(params, ctx, f06, h5)

    def _execute(
        self,
        params: NastranRunParams,
        ctx: RunContext,
        deck: Path,
        inputs_all: list[Path],
        settings: cmd.Settings,
        digest: str,
        listing: list[dict[str, str]],
    ) -> None:
        """Run Nastran: checks, clean-up, command, record."""
        if not has_hdf5_request(inputs_all):
            raise NastranError(
                f"{deck.name} has no HDF5 request, so Nastran would not write an .h5 file. "
                "Add HDF5OUT (or MDLPRM,HDF5,0) to the bulk data"
            )
        for path in job_files(deck).values():
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise NastranError(
                    f"Cannot remove the old result file {path.name}: {exc.strerror or exc} "
                    "(is it open in another program?)"
                ) from exc
        command = cmd.build_command(settings, deck)
        env = cmd.environment(settings)
        status = cmd.program_status(command, env)
        if status.checked and status.found is None:
            raise NastranError(
                f"Nastran program '{status.program}' not found. Set the path in the block "
                f"or in the config file {config_path()}, or the command"
            )
        ctx.log(f"Command: {command}   (in {deck.parent})")
        started = _time(datetime.now().timestamp())
        base = RunRecord(RUNNING, digest, listing, list(settings.options), command, started)
        self._write_record(deck, base)
        timeout = params.timeout_minutes * 60 if params.timeout_minutes else None
        try:
            result = run_command(
                command,
                cwd=deck.parent,
                env=env,
                on_line=ctx.log,
                cancel=ctx.cancel_event,
                timeout=timeout,
            )
        except OSError as exc:
            raise NastranError(f"Nastran could not be started: {exc}") from exc
        if result.cancelled:
            raise BlockCancelledError("Nastran was stopped by the user")
        if result.timed_out:
            raise NastranError(
                f"Nastran did not finish within {params.timeout_minutes:g} minutes and was stopped"
            )
        if result.exit_code != 0:
            last = " | ".join(result.tail[-3:])
            raise NastranError(f"Nastran exited with code {result.exit_code}. Last output: {last}")
        base.status, base.finished = FINISHED, _time(datetime.now().timestamp())
        base.exit_code, base.duration = result.exit_code, result.duration
        self._write_record(deck, base)
        ctx.log(f"Nastran finished in {result.duration:.0f} s")

    @staticmethod
    def _write_record(deck: Path, record: RunRecord) -> None:
        try:
            write_record(record_path(deck), record)
        except OSError as exc:
            raise NastranError(f"Cannot write the run record next to the results: {exc}") from exc

    def _check(
        self, params: NastranRunParams, ctx: RunContext, f06: Path, h5: Path
    ) -> dict[str, Any]:
        """Check the result files and the messages of the ``.f06`` (spec 09 §3.5)."""
        if not f06.is_file():
            raise NastranError(f"Nastran did not write {f06.name}")
        report = parse_f06(read_text(f06))
        if not report.finished:
            raise NastranError(
                f"{f06.name} ends without END OF JOB: the analysis did not finish. "
                "Look into the .f06 and the .log"
            )
        if not h5.is_file():
            raise NastranError(
                f"Nastran did not write {h5.name}. Check the HDF5 request in the input deck"
            )
        ignore = set(params.ignore_messages)
        table = messages_table(
            report, f06, include_information=params.include_information, ignore=ignore
        )
        fatal = report.of(Severity.FATAL)
        warnings = [m for m in report.of(Severity.WARNING) if m.number not in ignore]
        for message in report.messages:
            if message.severity is Severity.INFORMATION and not params.include_information:
                continue
            if message.severity is Severity.WARNING and message.number in ignore:
                ctx.log(f"Ignored: {_describe(message)}")
                continue
            if message.severity is Severity.INFORMATION:
                ctx.log(_describe(message))
            else:
                ctx.log(f"{message.severity.value}: {_describe(message, severity=False)}")
        status = "FATAL" if fatal else "WARNINGS" if warnings else "OK"
        ctx.log(f"Status: {status} ({len(fatal)} fatal, {len(warnings)} warning message(s))")
        failing = fatal if params.fail_on != "never" else []
        if params.fail_on == "warning":
            failing = [*fatal, *warnings]
        if failing:
            shown = "; ".join(_describe(m) for m in failing[:3])
            more = f" (and {len(failing) - 3} more)" if len(failing) > 3 else ""
            raise NastranError(
                f"{f06.name} contains {len(failing)} message(s) that fail the block: {shown}{more}"
            )
        return {"h5": str(h5), "f06": str(f06), "messages": table, "status": status}


BLOCKS: tuple[type[Block[Any]], ...] = (NastranRun,)
