"""Loading and checking user scripts for the Python block (spec 04 §3).

Kept separate from the block so that the GUI and the CLI (``apb new-script``) can reuse the
skeleton generator and the signature check.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import keyword
import math
import sys
import traceback
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apb.engine.types import PortType

#: Name of the function every script must define.
ENTRY_FUNCTION = "run"

_module_counter = itertools.count(1)


class ScriptError(Exception):
    """Raised for problems with a user script (missing file, wrong signature, ...)."""


class PortSpec(BaseModel):
    """A port declared by the user on a Python block."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Port name = argument / output key in the script")
    type: PortType = Field(default=PortType.ANY, description="Value type")
    optional: bool = Field(default=False, description="Input may be unconnected (gets None)")

    @field_validator("name")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not value.isidentifier() or keyword.iskeyword(value):
            raise ValueError(f"{value!r} is not a valid Python name")
        return value


# --------------------------------------------------------------------------------------
# Skeleton
# --------------------------------------------------------------------------------------

_HINTS: dict[PortType, str] = {
    PortType.TABLE: "pd.DataFrame",
    PortType.SCALAR: "float",
    PortType.TEXT: "str",
    PortType.FIGURE: "Figure",
    PortType.ANY: "object",
}

_PLACEHOLDERS: dict[PortType, str] = {
    PortType.TABLE: "pd.DataFrame()",
    PortType.SCALAR: "0.0",
    PortType.TEXT: '""',
    PortType.FIGURE: "plt.figure()",
    PortType.ANY: "None",
}


#: Explains the script contract; at the top of every generated script.
SCRIPT_NOTE = """Script for an Potato Pipeline Python block. The block calls ``run``
with one argument per input port and expects a dict with one entry per output port. Tables
arrive as pandas DataFrames (units in ``df.attrs["units"]``), scalars as plain numbers.
Use print() for messages: they appear in the block's log."""


def port_hint(port_type: PortType) -> str:
    """Return the type hint used for an input argument of this port type."""
    return _HINTS[port_type]


def port_placeholder(port_type: PortType) -> str:
    """Return the Python expression used as placeholder value of an output."""
    return _PLACEHOLDERS[port_type]


def ports_to_params(ports: Sequence[PortSpec]) -> list[dict[str, Any]]:
    """Return ports in the form stored in the pipeline file (``optional`` only if true)."""
    out: list[dict[str, Any]] = []
    for p in ports:
        item: dict[str, Any] = {"name": p.name, "type": p.type.value}
        if p.optional:
            item["optional"] = True
        out.append(item)
    return out


def script_skeleton(
    inputs: Sequence[PortSpec], outputs: Sequence[PortSpec], title: str = "Custom calculation"
) -> str:
    """Return the source of a new script matching the declared ports (PY-05b).

    Args:
        inputs: Declared input ports (become the arguments of ``run``).
        outputs: Declared output ports (become the keys of the returned dict).
        title: First line of the module docstring.

    Returns:
        Python source code.
    """
    uses_fig = any(p.type is PortType.FIGURE for p in [*inputs, *outputs])
    imports = ["import numpy as np", "import pandas as pd"]
    if uses_fig:
        imports = [
            "import matplotlib.pyplot as plt",
            *imports,
            "from matplotlib.figure import Figure",
        ]
    args = ", ".join(
        f"{p.name}: {_HINTS[p.type]}{' | None = None' if p.optional else ''}" for p in inputs
    )
    arg_docs = "\n".join(f"        {p.name}: {p.type} input." for p in inputs) or "        -"
    out_docs = ", ".join(f"{p.name} ({p.type})" for p in outputs) or "none"
    assigns = "\n".join(f"    {p.name} = {_PLACEHOLDERS[p.type]}" for p in outputs)
    result = ", ".join(f'"{p.name}": {p.name}' for p in outputs)
    return f'''"""{title}.

{SCRIPT_NOTE}
"""

from __future__ import annotations

{chr(10).join(imports)}


def run({args}) -> dict[str, object]:
    """Compute the outputs.

    Args:
{arg_docs}

    Returns:
        Dict with: {out_docs}.
    """
    # TODO: replace with your calculation
{assigns or "    pass"}
    return {{{result}}}
'''


def write_skeleton(
    path: Path, inputs: Sequence[PortSpec], outputs: Sequence[PortSpec], *, overwrite: bool = False
) -> None:
    """Create a new script file with :func:`script_skeleton`.

    Args:
        path: Script file to create.
        inputs: Declared input ports.
        outputs: Declared output ports.
        overwrite: Replace an existing file.

    Raises:
        ScriptError: If the file exists and ``overwrite`` is false.
    """
    title = path.stem.replace("_", " ").capitalize()
    write_new_file(path, script_skeleton(inputs, outputs, title), overwrite=overwrite)


def write_new_file(path: Path, text: str, *, overwrite: bool = False) -> None:
    """Write a script file; an existing file is never replaced unless ``overwrite`` (PY-13).

    The file is created in exclusive mode, so a file that appeared after the caller last
    looked is not replaced either.

    Args:
        path: File to create (missing folders are created).
        text: File content.
        overwrite: Replace an existing file (command line ``--force`` only).

    Raises:
        ScriptError: If the file exists and ``overwrite`` is false.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except FileExistsError:
        raise ScriptError(f"{path} already exists") from None


# --------------------------------------------------------------------------------------
# Static check (no execution)
# --------------------------------------------------------------------------------------


def read_script(path: Path) -> str:
    """Read a script file as text.

    Raises:
        ScriptError: If the file is missing or cannot be read as UTF-8 text.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ScriptError(f"File not found: {path}") from None
    except UnicodeDecodeError:
        raise ScriptError(f"{path.name} is not a UTF-8 text file") from None
    except OSError as exc:
        raise ScriptError(f"Cannot read {path.name}: {exc.strerror or exc}") from None


def modified_text(path: Path) -> str:
    """Return the modification time of a file as ``2026-11-15 14:20`` (``""`` if unknown)."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


@dataclass(frozen=True)
class ScriptCheck:
    """Result of the static check of a script (PY-14).

    Attributes:
        problems: Readable problems; empty if the script looks right.
    """

    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """``True`` if no problem was found."""
        return not self.problems


def check_script_source(
    source: str, inputs: Sequence[PortSpec], name: str = "script"
) -> ScriptCheck:
    """Check a script against the declared input ports **without running it** (PY-14).

    Looks for syntax errors, a top-level ``run`` function, and arguments of ``run`` that do
    not match the declared inputs (same rules as :func:`check_signature`). Which keys the
    returned dict has is not checked.

    Args:
        source: Script text.
        inputs: Declared input ports.
        name: File name used in messages.

    Returns:
        The problems found.
    """
    try:
        tree = ast.parse(source, filename=name)
    except SyntaxError as exc:
        line = f", line {exc.lineno}" if exc.lineno else ""
        return ScriptCheck((f"Syntax error in {name}{line}: {exc.msg}",))
    funcs = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == ENTRY_FUNCTION
    ]
    if not funcs:
        return ScriptCheck((f"{name} defines no {ENTRY_FUNCTION}() function",))
    args = funcs[-1].args
    positional = [*args.posonlyargs, *args.args]
    required = {a.arg for a in positional[: len(positional) - len(args.defaults)]}
    required |= {a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is None}
    accepted = {a.arg for a in [*positional, *args.kwonlyargs]}
    declared = {p.name for p in inputs}
    problems = []
    for port in inputs:
        if port.name not in accepted and args.kwarg is None:
            problems.append(f"run() has no argument '{port.name}' for the input port '{port.name}'")
    problems += [
        f"run() argument '{arg}' has no matching input port" for arg in sorted(required - declared)
    ]
    return ScriptCheck(tuple(problems))


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------


@contextmanager
def _script_dir_on_path(folder: Path) -> Iterator[None]:
    """Make modules next to the script importable, and forget them afterwards.

    Helper modules imported from the script's folder are removed from ``sys.modules``
    after the run, so that edits to them are picked up by the next run. No bytecode
    (``__pycache__``) is written meanwhile: its validity check has a resolution of one
    second, so a helper edited and re-run quickly could otherwise run stale code. (A
    ``__pycache__`` created by running the helper outside the tool can still be stale
    in that same second.)
    """
    folder_str = str(folder)
    sys.path.insert(0, folder_str)
    before = set(sys.modules)
    old_flag = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = old_flag
        if folder_str in sys.path:
            sys.path.remove(folder_str)
        for name in set(sys.modules) - before:
            file = getattr(sys.modules[name], "__file__", None)
            if file and Path(file).resolve().parent == folder:
                del sys.modules[name]


@contextmanager
def loaded_script(path: Path) -> Iterator[Callable[..., Any]]:
    """Load a script fresh from disk and yield its ``run`` function.

    The script's folder stays importable while the ``with`` block runs, so ``run`` may
    import helper modules lazily.

    Args:
        path: Script file.

    Yields:
        The ``run`` function.

    Raises:
        ScriptError: If the file is missing, cannot be imported or has no ``run``.
    """
    if not path.is_file():
        raise ScriptError(
            f"Script not found: {path}. Create it with: apb new-script <pipeline> <block id>"
        )
    with _script_dir_on_path(path.resolve().parent):
        module = ModuleType(f"apb_script_{path.stem}_{next(_module_counter)}")
        module.__file__ = str(path)
        sys.modules[module.__name__] = module  # needed by e.g. dataclasses; removed after run
        try:
            # Compiled from source on every run (never from __pycache__), so edits count.
            code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
            exec(code, module.__dict__)
        except SyntaxError as exc:
            raise ScriptError(f"Syntax error in {path.name}, line {exc.lineno}: {exc.msg}") from exc
        except Exception as exc:
            raise ScriptError(f"Error while loading {path.name}: {describe(exc, path)}") from exc
        func = getattr(module, ENTRY_FUNCTION, None)
        if not callable(func):
            raise ScriptError(f"{path.name} does not define a function '{ENTRY_FUNCTION}'")
        yield func


def compile_inline(code: str, block_id: str) -> Callable[..., Any]:
    """Compile inline code (stored in the pipeline) and return its ``run`` function.

    The namespace has ``np``, ``pd``, ``plt`` and ``math`` pre-imported (PY-08).

    Args:
        code: Source defining ``run``.
        block_id: Used in the pseudo file name for tracebacks.

    Raises:
        ScriptError: On syntax errors or if ``run`` is not defined.
    """
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd

    filename = inline_filename(block_id)
    try:
        compiled = compile(code, filename, "exec")
    except SyntaxError as exc:
        raise ScriptError(f"Syntax error in inline code, line {exc.lineno}: {exc.msg}") from exc
    namespace: dict[str, Any] = {
        "np": np,
        "pd": pd,
        "plt": plt,
        "math": math,
        "__name__": f"apb_inline_{block_id}",
    }
    exec(compiled, namespace)
    func = namespace.get(ENTRY_FUNCTION)
    if not callable(func):
        raise ScriptError(f"Inline code does not define a function '{ENTRY_FUNCTION}'")
    return func  # type: ignore[no-any-return]


def inline_filename(block_id: str) -> str:
    """Pseudo file name used for inline code in tracebacks."""
    return f"<block {block_id}>"


def check_signature(func: Callable[..., Any], inputs: Sequence[PortSpec]) -> None:
    """Check that ``run`` accepts exactly the declared inputs (PY-05c).

    Args:
        func: The script's ``run`` function.
        inputs: Declared input ports.

    Raises:
        ScriptError: If a declared input is not accepted or ``run`` requires an argument
            that is not a declared input.
    """
    sig = inspect.signature(func)
    params = sig.parameters
    has_kwargs = any(p.kind is p.VAR_KEYWORD for p in params.values())
    declared = {p.name for p in inputs}
    problems = []
    for name in declared:
        if name not in params and not has_kwargs:
            problems.append(f"run() has no argument '{name}' for the input port '{name}'")
    for name, p in params.items():
        if p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
            continue
        if name not in declared and p.default is p.empty:
            problems.append(f"run() argument '{name}' has no matching input port")
    if problems:
        raise ScriptError("Script does not match the block's ports: " + "; ".join(problems))


def describe(exc: BaseException, path: Path | str) -> str:
    """Describe an exception raised in user code, with the line in the user's file.

    Args:
        exc: The exception.
        path: Script file, or the pseudo file name of inline code.

    Returns:
        E.g. ``"ZeroDivisionError: division by zero (margins.py, line 12)"``.
    """
    target = str(Path(path).resolve()) if isinstance(path, Path) else path
    line = None
    for frame in traceback.extract_tb(exc.__traceback__):
        fname = (
            frame.filename
            if frame.filename.startswith("<")
            else str(Path(frame.filename).resolve())
        )
        if fname == target:
            line = frame.lineno
    where = f" ({Path(path).name if isinstance(path, Path) else path}, line {line})" if line else ""
    return f"{type(exc).__name__}: {exc}{where}"
