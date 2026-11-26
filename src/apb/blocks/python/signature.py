"""Keep a script's ``run()`` signature and the block's input ports in step (spec 04 §3.9).

The ports are the definition of the block. When the input ports change, only the
``def run(...)`` line of the script is rewritten:

* arguments are matched to ports by position when the number of ports stays the same, so
  renaming a port renames its argument (the body is not changed);
* existing type hints and defaults of kept arguments are kept; new ports get the hint of
  their type (``pd.DataFrame``, ``float`` ...);
* arguments of removed ports are dropped; other arguments (not ports) are kept;
* ``*args`` / ``**kwargs`` and the return annotation are kept.

The other way round, :func:`ports_from_signature` proposes input ports for ``run()``
arguments that have none, :func:`ports_missing_from_run` names the ports ``run()`` does not
take, and :func:`output_differences` compares the returned keys with the output ports (the
user confirms each fix in the panel, spec 04 §3.9.1).
"""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Sequence
from dataclasses import dataclass, field

from apb.blocks.python.scripting import (
    ENTRY_FUNCTION,
    PortSpec,
    ScriptError,
    port_hint,
    returned_keys,
)
from apb.engine.types import PortType

#: Longest ``def run(...)`` line before the arguments are put one per line.
MAX_LINE = 99


@dataclass(frozen=True)
class SignatureChange:
    """The result of :func:`rewrite_run_signature`.

    Attributes:
        source: The new script text (the same text if nothing changed).
        added: Arguments added.
        removed: Arguments removed.
        renamed: ``(old, new)`` argument names.
    """

    source: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """``True`` if the ``run()`` line was changed."""
        return bool(self.added or self.removed or self.renamed)

    def summary(self) -> str:
        """Describe the change for the user, e.g. ``"added forces; renamed a → b"``."""
        parts = []
        if self.added:
            parts.append("added " + ", ".join(self.added))
        if self.removed:
            parts.append("removed " + ", ".join(self.removed))
        if self.renamed:
            parts.append("renamed " + ", ".join(f"{a} → {b}" for a, b in self.renamed))
        return "; ".join(parts)


def _find_run(source: str) -> ast.FunctionDef:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        line = f" (line {exc.lineno})" if exc.lineno else ""
        raise ScriptError(f"The script has a syntax error{line}: {exc.msg}") from exc
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == ENTRY_FUNCTION]
    if not funcs:
        raise ScriptError(f"The script defines no {ENTRY_FUNCTION}() function")
    return funcs[-1]


def _header_end(source: str, func: ast.FunctionDef) -> tuple[int, int]:
    """Return ``(line, column)`` just after the ``:`` that ends the ``def`` header."""
    lines = source.splitlines(keepends=True)
    tail = "".join(lines[func.lineno - 1 :])
    depth = 0
    seen_def = False
    for tok in tokenize.generate_tokens(io.StringIO(tail).readline):
        if tok.type == tokenize.NAME and tok.string == "def":
            seen_def = True
        elif tok.type == tokenize.OP and seen_def:
            if tok.string in "([{":
                depth += 1
            elif tok.string in ")]}":
                depth -= 1
            elif tok.string == ":" and depth == 0:
                return tok.end[0] + func.lineno - 1, tok.end[1]
    raise ScriptError(f"Cannot find the end of the {ENTRY_FUNCTION}() line")


def _argument_texts(source: str, func: ast.FunctionDef) -> dict[str, tuple[str, bool]]:
    """Return argument name -> (source text with annotation and default, has default)."""
    args = func.args
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    defaults += list(args.defaults)
    pairs = [
        *zip(positional, defaults, strict=True),
        *zip(args.kwonlyargs, args.kw_defaults, strict=True),
    ]
    out: dict[str, tuple[str, bool]] = {}
    for arg, default in pairs:
        text = ast.get_source_segment(source, arg) or arg.arg
        if default is not None:
            equals = " = " if arg.annotation is not None else "="  # PEP 8
            text += equals + (ast.get_source_segment(source, default) or "None")
        out[arg.arg] = (text, default is not None)
    return out


def _new_argument(port: PortSpec) -> tuple[str, bool]:
    hint = port_hint(port.type)
    if port.optional:
        return f"{port.name}: {hint} | None = None", True
    return f"{port.name}: {hint}", False


def _renamed_text(text: str, old: str, new: str) -> str:
    return new + text[len(old) :] if text.startswith(old) else text


def rewrite_run_signature(
    source: str, old_inputs: Sequence[PortSpec], new_inputs: Sequence[PortSpec]
) -> SignatureChange:
    """Rewrite the ``def run(...)`` line for new input ports (see the module docstring).

    Args:
        source: Script text.
        old_inputs: The input ports before the change.
        new_inputs: The input ports after the change.

    Returns:
        The new text and what changed. Nothing changes if the arguments already match.

    Raises:
        ScriptError: If the script cannot be parsed or has no ``run()``.
    """
    func = _find_run(source)
    existing = _argument_texts(source, func)
    old_names = [p.name for p in old_inputs]
    new_names = [p.name for p in new_inputs]
    rename: dict[str, str] = {}
    if len(old_names) == len(new_names):
        rename = {
            o: n
            for o, n in zip(old_names, new_names, strict=True)
            if o != n and o in existing and n not in existing
        }
    back = {n: o for o, n in rename.items()}

    entries: list[tuple[str, bool]] = []
    added: list[str] = []
    for port in new_inputs:
        source_name = back.get(port.name, port.name)
        if source_name in existing:
            text, has_default = existing[source_name]
            entries.append((_renamed_text(text, source_name, port.name), has_default))
        else:
            entries.append(_new_argument(port))
            added.append(port.name)
    used = set(new_names) | set(rename)
    removed = [n for n in existing if n in old_names and n not in used]
    extras = [n for n in existing if n not in used and n not in removed]
    entries += [existing[n] for n in extras]
    # Arguments without default first (Python requires it), otherwise in port order.
    ordered = [t for t, d in entries if not d] + [t for t, d in entries if d]
    if func.args.vararg is not None:
        ordered.append("*" + (ast.get_source_segment(source, func.args.vararg) or "args"))
    if func.args.kwarg is not None:
        ordered.append("**" + (ast.get_source_segment(source, func.args.kwarg) or "kwargs"))

    if not added and not removed and not rename:
        return SignatureChange(source)

    returns = ""
    if func.returns is not None:
        returns = " -> " + (ast.get_source_segment(source, func.returns) or "object")
    one_line = f"def {ENTRY_FUNCTION}({', '.join(ordered)}){returns}:"
    if len(one_line) <= MAX_LINE:
        header = one_line
    else:
        body = "".join(f"    {a},\n" for a in ordered)
        header = f"def {ENTRY_FUNCTION}(\n{body}){returns}:"

    lines = source.splitlines(keepends=True)
    end_line, end_col = _header_end(source, func)
    before = "".join(lines[: func.lineno - 1])
    after = lines[end_line - 1][end_col:] + "".join(lines[end_line:])
    return SignatureChange(
        before + header + after,
        added=added,
        removed=removed,
        renamed=list(rename.items()),
    )


def _type_from_annotation(annotation: ast.expr | None) -> PortType:
    text = ast.unparse(annotation) if annotation is not None else ""
    if "DataFrame" in text:
        return PortType.TABLE
    if "Figure" in text:
        return PortType.FIGURE
    if any(word in text for word in ("float", "int", "Quantity")):
        return PortType.SCALAR
    if "str" in text:
        return PortType.TEXT
    return PortType.ANY


def ports_from_signature(source: str, inputs: Sequence[PortSpec]) -> list[PortSpec]:
    """Propose input ports for ``run()`` arguments without a port (PY-17).

    Only arguments without a default are proposed (the others work without a port). The
    port type is taken from the annotation (``pd.DataFrame`` → Table, ``float`` → Scalar,
    ``str`` → Text, ``Figure`` → Figure, else Any).

    Returns:
        The proposed ports (empty if there are none or the script cannot be parsed).
    """
    try:
        func = _find_run(source)
    except ScriptError:
        return []
    declared = {p.name for p in inputs}
    args = func.args
    positional = [*args.posonlyargs, *args.args]
    required = positional[: len(positional) - len(args.defaults)]
    required += [a for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is None]
    return [
        PortSpec(name=a.arg, type=_type_from_annotation(a.annotation))
        for a in required
        if a.arg not in declared
    ]


def ports_missing_from_run(source: str, inputs: Sequence[PortSpec]) -> list[PortSpec]:
    """Return the input ports that ``run()`` has no argument for (PY-21).

    A ``run()`` with ``**kwargs`` takes every port. Empty if the script cannot be parsed.
    """
    try:
        func = _find_run(source)
    except ScriptError:
        return []
    args = func.args
    if args.kwarg is not None:
        return []
    accepted = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
    return [p for p in inputs if p.name not in accepted]


@dataclass(frozen=True)
class OutputDifferences:
    """Returned keys compared with the output ports (PY-22).

    Attributes:
        new: Keys ``run()`` returns that are not output ports.
        unused: Output ports that ``run()`` does not return.
    """

    new: list[str] = field(default_factory=list)
    unused: list[str] = field(default_factory=list)


def output_differences(source: str, outputs: Sequence[PortSpec]) -> OutputDifferences:
    """Compare the keys ``run()`` returns with the output ports, without running it.

    Returns:
        The differences; empty when they match or when the keys cannot be read from the
        code (see :func:`apb.blocks.python.scripting.returned_keys`).
    """
    try:
        keys = returned_keys(_find_run(source))
    except ScriptError:
        return OutputDifferences()
    if keys is None:
        return OutputDifferences()
    names = [p.name for p in outputs]
    return OutputDifferences(
        new=[k for k in keys if k not in names], unused=[n for n in names if n not in keys]
    )


#: Note shown when the output ports cannot be checked before the run (spec 04 §3.9.1).
OUTPUTS_AFTER_RUN = "Output ports are checked after the run: run() does not return a {...} literal."


def outputs_readable(source: str) -> bool:
    """Return whether the keys ``run()`` returns can be read from the code.

    ``True`` for a script that cannot be parsed or has no ``run()``: the check reports
    those itself, so no extra note is needed.
    """
    try:
        return returned_keys(_find_run(source)) is not None
    except ScriptError:
        return True
