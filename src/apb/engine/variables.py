"""Pipeline variables: ``${name}`` references in block parameters (ENG-14)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ESCAPE = "$${"


class VariableError(KeyError):
    """Raised when a parameter references an undefined variable."""

    def __str__(self) -> str:
        return str(self.args[0])


def resolve(value: Any, variables: Mapping[str, Any]) -> Any:
    """Replace ``${name}`` references in a parameter value.

    A string that consists of exactly one reference is replaced by the variable's value
    with its type (e.g. a float stays a float). References inside longer strings are
    substituted as text. ``$${`` produces a literal ``${``. Lists and dicts are processed
    recursively.

    Args:
        value: Parameter value (any JSON-compatible type).
        variables: Variable name -> value.

    Returns:
        The value with references resolved.

    Raises:
        VariableError: If a referenced variable is not defined.
    """
    if isinstance(value, str):
        return _resolve_str(value, variables)
    if isinstance(value, list):
        return [resolve(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, variables) for k, v in value.items()}
    return value


def _resolve_str(text: str, variables: Mapping[str, Any]) -> Any:
    """Resolve references in one string (see :func:`resolve`)."""
    placeholder = "\x00"
    text = text.replace(_ESCAPE, placeholder)

    def lookup(name: str) -> Any:
        if name not in variables:
            raise VariableError(f"Undefined variable ${{{name}}}")
        return variables[name]

    full = _REF.fullmatch(text)
    if full:
        return lookup(full.group(1))
    replaced = _REF.sub(lambda m: str(lookup(m.group(1))), text)
    return replaced.replace(placeholder, "${")


def referenced_names(value: Any) -> set[str]:
    """Return the variable names referenced anywhere in a parameter value."""
    if isinstance(value, str):
        return set(_REF.findall(value.replace(_ESCAPE, "")))
    if isinstance(value, list):
        return set().union(*(referenced_names(v) for v in value)) if value else set()
    if isinstance(value, dict):
        return referenced_names(list(value.values()))
    return set()


def parse_assignment(text: str) -> tuple[str, Any]:
    """Parse a command line override ``name=value``.

    The value is interpreted as JSON if possible (``3.5``, ``true``, ``[1, 2]``), otherwise
    as a plain string (``results/new.h5``).

    Args:
        text: Assignment text.

    Returns:
        ``(name, value)``.

    Raises:
        ValueError: If the text has no ``=``.
    """
    name, sep, raw = text.partition("=")
    if not sep or not name.strip():
        raise ValueError(f"Expected name=value, got {text!r}")
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return name.strip(), value


def rename_reference(value: Any, old: str, new: str) -> Any:
    """Return a parameter value with every ``${old}`` replaced by ``${new}``.

    Escaped ``$${old}`` (a literal text) is left alone. Lists and dicts are processed
    recursively; other values are returned unchanged.

    Args:
        value: Parameter value.
        old: Current name.
        new: New name.
    """
    if isinstance(value, str):
        placeholder = "\x00"
        text = value.replace(_ESCAPE, placeholder)
        text = text.replace("${" + old + "}", "${" + new + "}")
        return text.replace(placeholder, _ESCAPE)
    if isinstance(value, list):
        return [rename_reference(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: rename_reference(v, old, new) for k, v in value.items()}
    return value
