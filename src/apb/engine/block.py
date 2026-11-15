"""Block base class and run context (spec 01 §3.1).

A block type is a subclass of :class:`Block` with a Pydantic parameter model::

    class ScaleParams(BaseModel):
        factor: float = 1.0

    class Scale(Block[ScaleParams]):
        type_id = "example.scale"
        category = "Table"
        title = "Scale"
        inputs = (Port("table", PortType.TABLE),)
        outputs = (Port("table", PortType.TABLE),)
        Params = ScaleParams

        def run(self, params, inputs, ctx):
            return {"table": inputs["table"] * params.factor}
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from apb.engine.types import Port
from apb.units import UnitSystem

#: String parameter holding a file path; the GUI shows a file picker for it.
FilePath = Annotated[str, Field(json_schema_extra={"apb_widget": "file"})]

#: String parameter holding code; the GUI shows a code editor for it.
Code = Annotated[str, Field(json_schema_extra={"apb_widget": "code"})]


class NoParams(BaseModel):
    """Parameter model of blocks without parameters."""

    model_config = ConfigDict(extra="forbid")


class BlockCancelledError(Exception):
    """Raised inside a block when the user stopped the run."""


@dataclass
class RunContext:
    """Services available to a block while it runs.

    Attributes:
        block_id: ID of the block instance being run.
        pipeline_dir: Directory of the pipeline file; relative paths resolve against it.
        output_dir: Directory for files written by blocks (reports, workbooks).
        default_unit_system: Pipeline setting used when a block's own setting is empty.
        log_callback: Receives log lines.
        progress_callback: Receives progress fractions (0..1).
        cancel_event: Set when the user stops the run.
        input_names: Names of the values at ``multiple`` inputs (connection name, else
            derived from the source block; see :func:`apb.engine.graph.connection_name`).
    """

    block_id: str
    pipeline_dir: Path
    output_dir: Path
    default_unit_system: UnitSystem = UnitSystem.SI_MM
    log_callback: Callable[[str], None] = field(default=lambda _msg: None)
    progress_callback: Callable[[float], None] = field(default=lambda _f: None)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    input_names: dict[str, list[str]] = field(default_factory=dict)
    """For each ``multiple`` input port: the names of its values, in list order."""
    pipeline_file: Path | None = None
    """The pipeline file, if the run started from one."""
    upstream_files: list[Path] = field(default_factory=list)
    """Files read by this block and all blocks upstream of it (for traceability)."""

    def log(self, message: str) -> None:
        """Write a line to the block's log."""
        self.log_callback(message)

    def progress(self, fraction: float) -> None:
        """Report progress of the block (0..1)."""
        self.progress_callback(min(max(fraction, 0.0), 1.0))

    def check_cancelled(self) -> None:
        """Raise :class:`BlockCancelledError` if the user stopped the run.

        Long-running blocks call this regularly.
        """
        if self.cancel_event.is_set():
            raise BlockCancelledError("Run stopped by user")

    def resolve_path(self, path: str | Path) -> Path:
        """Resolve a path from the pipeline file; relative paths are relative to its folder.

        Args:
            path: Absolute or pipeline-relative path (forward slashes work on all platforms).

        Returns:
            Absolute path.
        """
        p = Path(path).expanduser()
        return p if p.is_absolute() else (self.pipeline_dir / p).resolve()

    def unit_system(self, value: UnitSystem | None) -> UnitSystem:
        """Return ``value`` or, if it is ``None``, the pipeline default unit system."""
        return value if value is not None else self.default_unit_system


class Block[P: BaseModel](ABC):
    """Base class of all block types.

    Class attributes describe the block type; :meth:`run` does the work. Blocks must be
    deterministic and may only have side effects through files they declare.
    """

    type_id: ClassVar[str]
    """Globally unique type identifier, e.g. ``"nastran.hdf5_results"``."""
    version: ClassVar[int] = 1
    """Version of the block type; increase when the parameter model changes."""
    category: ClassVar[str]
    """Library group, e.g. ``"Input/Nastran"``."""
    title: ClassVar[str]
    """Display name."""
    description: ClassVar[str] = ""
    """Help text shown in the GUI."""
    inputs: ClassVar[tuple[Port, ...]] = ()
    outputs: ClassVar[tuple[Port, ...]] = ()
    Params: ClassVar[type[BaseModel]] = NoParams
    """Pydantic model of the block's parameters."""
    always_run: ClassVar[bool] = False
    """Never take outputs from the cache; for blocks that write files (Excel, report)."""

    @abstractmethod
    def run(self, params: P, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Compute the outputs.

        Args:
            params: Validated parameters.
            inputs: Input port name -> value (``None`` for unconnected optional inputs,
                a list for ``multiple`` inputs).
            ctx: Run context.

        Returns:
            Output port name -> value, one entry per output port.
        """

    def input_files(self, params: P, ctx: RunContext) -> list[Path]:
        """Return files read by the block; a change of one of them invalidates the cache."""
        return []

    def output_files(self, params: P, outputs: Mapping[str, Any], ctx: RunContext) -> list[Path]:
        """Return the files the block wrote (reports, workbooks).

        A block that writes files counts as ``done`` only while they exist (spec 08 §3.4).

        Args:
            params: Validated parameters.
            outputs: The outputs the run returned.
            ctx: Run context (resolves relative paths).
        """
        return []

    @classmethod
    def always_runs(cls, params: BaseModel | None) -> bool:
        """Return whether the block bypasses the cache and runs on every run.

        Defaults to the class attribute ``always_run``; a block whose behaviour depends on a
        parameter (the run mode of an analysis block) overrides it (ENG-20).

        Args:
            params: Validated parameters of the block instance, if known.
        """
        return cls.always_run

    @classmethod
    def input_ports(cls, params: BaseModel | None) -> tuple[Port, ...]:
        """Return the input ports; override for blocks whose ports depend on parameters.

        Args:
            params: Validated parameters, or ``None`` if they are invalid.
        """
        return cls.inputs

    @classmethod
    def output_ports(cls, params: BaseModel | None) -> tuple[Port, ...]:
        """Return the output ports; override for blocks whose ports depend on parameters.

        Args:
            params: Validated parameters, or ``None`` if they are invalid.
        """
        return cls.outputs

    @classmethod
    def migrate(cls, old_version: int, params: dict[str, Any]) -> dict[str, Any]:
        """Upgrade stored parameters from an older block version.

        Args:
            old_version: Version stored in the pipeline file.
            params: Stored parameters.

        Returns:
            Parameters valid for the current version (default: unchanged).
        """
        return params


type AnyBlockType = type[Block[Any]]
