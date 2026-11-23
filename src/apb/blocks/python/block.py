"""The Python block: user-written calculations inside a pipeline (spec 04).

By default the code lives in an external ``.py`` file next to the pipeline (PY-05). The
file defines ``run(...)`` with one argument per declared input port and returns a dict
with one entry per declared output port.

Security: the code runs with the user's rights in the same process. Opening a pipeline
from an untrusted source is like running an untrusted script (spec 04 §3.3).
"""

from __future__ import annotations

import contextlib
import io
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from apb.blocks.python.scripting import (
    PortSpec,
    ScriptError,
    check_signature,
    compile_inline,
    describe,
    error_details,
    inline_filename,
    loaded_script,
)
from apb.debug import collecting
from apb.engine.block import Block, Code, FilePath, RunContext, file_types
from apb.engine.types import Port, PortType
from apb.units import (
    ATTR_UNIT_SYSTEM,
    Quantity,
    UnitError,
    common_unit_system,
    get_units,
    set_units,
)


class PythonParams(BaseModel):
    """Parameters of :class:`PythonBlock`."""

    model_config = ConfigDict(extra="forbid")

    script: FilePath = Field(
        default="",
        description="Python file defining run(...), relative to the pipeline",
        json_schema_extra=file_types(".py"),
    )
    inline_code: Code = Field(
        default="", description="Alternative to a script file: code defining run(...)"
    )
    inputs: list[PortSpec] = Field(default_factory=list, description="Input ports")
    outputs: list[PortSpec] = Field(default_factory=list, description="Output ports")

    @model_validator(mode="after")
    def _check(self) -> PythonParams:
        if bool(self.script) == bool(self.inline_code.strip()):
            raise ValueError("set either 'script' or 'inline_code' (exactly one)")
        for side, ports in (("input", self.inputs), ("output", self.outputs)):
            names = [p.name for p in ports]
            dupes = sorted({n for n in names if names.count(n) > 1})
            if dupes:
                raise ValueError(f"duplicate {side} port name(s): {', '.join(dupes)}")
        return self


class _LogWriter(io.TextIOBase):
    """File-like object sending complete lines to the block log.

    While a line is forwarded, the real stdout/stderr are restored: log consumers such as
    the CLI print to stdout, which would otherwise feed back into this writer.
    """

    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log
        self._buffer = ""
        self._stdout = sys.stdout
        self._stderr = sys.stderr

    def _forward(self, line: str) -> None:
        with contextlib.redirect_stdout(self._stdout), contextlib.redirect_stderr(self._stderr):
            self._log(line)

    def write(self, text: str) -> int:
        self._buffer += text
        *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            self._forward(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._forward(self._buffer)
            self._buffer = ""


def _headless_matplotlib() -> None:
    """Make matplotlib draw off-screen (no windows from scripts; works in a server)."""
    import matplotlib

    if matplotlib.get_backend().lower() != "agg":
        matplotlib.use("Agg")


def _prepare_input(value: Any) -> Any:
    """Pass scalars with unit as plain numbers (the unit is logged)."""
    if isinstance(value, Quantity):
        return value.value
    if isinstance(value, list):
        return [_prepare_input(v) for v in value]
    return value


def _normalise_output(value: Any, port_type: PortType) -> Any:
    """Convert NumPy scalars to Python numbers for scalar outputs."""
    if port_type is PortType.SCALAR and isinstance(value, np.integer | np.floating):
        return value.item()
    return value


class PythonBlock(Block[PythonParams]):
    """Run a user-written Python function."""

    type_id = "python.script"
    category = "Python"
    title = "Python"
    description = (
        "Custom calculation in Python: run(...) gets one argument per input port and "
        "returns a dict with one entry per output port."
    )
    Params = PythonParams

    @classmethod
    def input_ports(cls, params: BaseModel | None) -> tuple[Port, ...]:
        """Ports declared by the user."""
        if not isinstance(params, PythonParams):
            return ()
        return tuple(Port(p.name, p.type, optional=p.optional) for p in params.inputs)

    @classmethod
    def output_ports(cls, params: BaseModel | None) -> tuple[Port, ...]:
        """Ports declared by the user."""
        if not isinstance(params, PythonParams):
            return ()
        return tuple(Port(p.name, p.type) for p in params.outputs)

    def input_files(self, params: PythonParams, ctx: RunContext) -> list[Path]:
        """The script and the other ``.py`` files in its folder (helper modules)."""
        if not params.script:
            return []
        script = ctx.resolve_path(params.script)
        helpers = sorted(script.parent.glob("*.py")) if script.parent.is_dir() else []
        return [script, *(h for h in helpers if h != script)]

    def run(self, params: PythonParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Load the code, call ``run`` and check the result."""
        _headless_matplotlib()
        tables = [v for v in inputs.values() if isinstance(v, pd.DataFrame)]
        try:
            system = common_unit_system(tables)
        except UnitError as exc:
            raise ScriptError(str(exc)) from exc
        for name, value in inputs.items():
            if isinstance(value, Quantity) and value.unit:
                ctx.log(f"{name} = {value.value:g} {value.unit} (passed as a number)")
        kwargs = {name: _prepare_input(value) for name, value in inputs.items()}

        if params.script:
            path = ctx.resolve_path(params.script)
            with loaded_script(path) as func:
                result = self._call(func, params, kwargs, ctx, path)
        else:
            func = compile_inline(params.inline_code, ctx.block_id)
            result = self._call(func, params, kwargs, ctx, inline_filename(ctx.block_id))
        return self._finish(result, params, system)

    @staticmethod
    def _call(
        func: Callable[..., Any],
        params: PythonParams,
        kwargs: dict[str, Any],
        ctx: RunContext,
        location: Path | str,
    ) -> Any:
        """Call the user function with stdout/stderr captured into the log."""
        check_signature(func, params.inputs)
        writer = _LogWriter(ctx.log)
        try:
            with (
                collecting(ctx.shown.__setitem__),
                contextlib.redirect_stdout(writer),
                contextlib.redirect_stderr(writer),
            ):
                return func(**kwargs)
        except Exception as exc:
            writer.flush()
            for line in error_details(exc, location):  # where, and the variables (PY-19)
                ctx.log(line)
            raise ScriptError(describe(exc, location)) from exc
        finally:
            writer.flush()

    @staticmethod
    def _finish(result: Any, params: PythonParams, system: Any) -> dict[str, Any]:
        """Check the returned value and attach units metadata to returned tables."""
        if len(params.outputs) == 1 and not isinstance(result, dict):
            result = {params.outputs[0].name: result}
        if not isinstance(result, dict):
            raise ScriptError(f"run() must return a dict, got {type(result).__name__}")
        declared = [p.name for p in params.outputs]
        extra = [str(k) for k in result if k not in declared]
        missing = [n for n in declared if n not in result]
        problems = [
            f"run() returned '{k}', which is not an output port: add an output port '{k}' "
            "(Ports) or leave it out of the returned dict"
            for k in extra
        ] + [f"the output port '{n}' is missing from the dict run() returned" for n in missing]
        if problems:
            raise ScriptError("; ".join(problems))
        types = {p.name: p.type for p in params.outputs}
        out = {k: _normalise_output(v, types.get(k, PortType.ANY)) for k, v in result.items()}
        for value in out.values():
            if isinstance(value, pd.DataFrame) and system and ATTR_UNIT_SYSTEM not in value.attrs:
                set_units(value, system, get_units(value))
            elif isinstance(value, pd.DataFrame):
                set_units(value, None, get_units(value))  # drop units of removed columns
        return out


BLOCKS: tuple[type[Block[Any]], ...] = (PythonBlock,)
