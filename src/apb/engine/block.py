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
from pydantic.config import JsonDict, JsonValue

from apb.engine.types import Port
from apb.units import UnitSystem

#: String parameter holding a file path; the GUI shows a file picker for it.
FilePath = Annotated[str, Field(json_schema_extra={"apb_widget": "file"})]

#: String parameter holding the path of a file the block writes (GUI: file picker and a
#: "written" line with an *Open* icon).
OutputPath = Annotated[str, Field(json_schema_extra={"apb_widget": "file", "apb_output": True})]


#: Nastran input deck suffixes (spec 09).
NASTRAN_DECK_TYPES = (".bdf", ".dat", ".nas", ".blk")


def file_types(*suffixes: str) -> JsonDict:
    """Return the ``json_schema_extra`` naming the file types of a file parameter.

    The GUI's file dialog lists only files ending in one of them (it has an "all files"
    switch) and, in save mode, appends the first one to a name without any of them.

    Args:
        suffixes: File name endings, e.g. ``".bdf"`` or ``".apb.json"``.

    Example:
        ``deck: FilePath = Field(description="...", json_schema_extra=file_types(".bdf"))``
    """
    types: list[JsonValue] = list(suffixes)
    return {"apb_file_types": types}


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
    shown: dict[str, Any] = field(default_factory=dict)
    """Values a script passed to ``apb.show()``: extra preview tabs, also after an error."""
    connected_outputs: frozenset[str] | None = None
    """Output ports with at least one connector (``None`` = not known: treat all as used).

    Only filled for blocks with :attr:`Block.uses_connected_outputs`."""

    def output_used(self, port: str) -> bool:
        """Return ``True`` if an output port is connected, or if that is not known."""
        return self.connected_outputs is None or port in self.connected_outputs

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
    advanced_params: ClassVar[tuple[str, ...]] = ()
    """Parameters that are rarely changed; the property panel folds them (spec 11 PNL-03)."""
    provides_variables: ClassVar[bool] = False
    """The block defines variables for other blocks (see :meth:`provided_variables`)."""
    uses_connected_outputs: ClassVar[bool] = False
    """The block's work depends on which outputs are connected (``ctx.connected_outputs``);
    the set is then part of the cache key, so connecting an output can make it stale."""

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
    def provided_variables(cls, params: BaseModel) -> dict[str, Any]:
        """Return variables this block defines for ``${name}`` in all block parameters.

        Used by blocks whose values are known without running, such as the Constants block
        (spec 03 §3.5.2). Such a block's own parameters must not contain ``${...}``. The
        values are collected while the pipeline is prepared, before any parameter is
        resolved.

        Args:
            params: Validated parameters of the block instance.

        Returns:
            Name -> value (default: none).
        """
        return {}

    @classmethod
    def port_fingerprint(cls, params: BaseModel, port: str) -> str | None:
        """Return a fingerprint of one output, if it is known from the parameters alone.

        Downstream cache keys then depend on this output only, not on the whole block: a
        change of one constant re-runs only the blocks connected to that constant's port
        (spec 03 §3.5.1). ``None`` (the default) means "use the block's cache key".

        Args:
            params: Validated parameters of the block instance.
            port: Output port name.
        """
        return None

    @classmethod
    def apply_overrides(
        cls, params: dict[str, Any], overrides: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Return stored parameters with overridden variables applied (CLI ``--set``).

        Only relevant for blocks that provide variables: an override of one of their
        names must also change what the block itself outputs (spec 03 CON-09).

        Args:
            params: Stored parameters.
            overrides: Name -> value from the command line.

        Returns:
            The parameters to use (default: unchanged).
        """
        return params

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
