"""Debugging help for Python block scripts (spec 04 §3.10).

``show(value, name)`` puts a table or value into an extra tab of the block's preview, also
when the script fails later::

    from apb import show

    def run(forces):
        joined = forces.merge(locations, on="EID")
        show(joined, "joined")          # tab "show: joined" in the preview
        ...

Outside the tool (a script run on its own) ``show`` prints a summary instead. The value is
copied when shown, so later changes in the script do not change what is shown.

:func:`summarize` describes a value in a few lines (tables: size, columns and first rows);
it is also used for the variables listed when a script fails.
"""

from __future__ import annotations

import copy
import itertools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

#: Receives ``(name, value)`` from :func:`show` while a Python block runs.
_SINK: ContextVar[Callable[[str, Any], None] | None] = ContextVar("apb_show_sink", default=None)

#: Rows of a table shown in a summary.
SUMMARY_ROWS = 5

#: Longest text of a plain value in a summary.
MAX_TEXT = 200

_counter = itertools.count(1)


def show(value: Any, name: str = "") -> Any:
    """Show a value in the block's preview (an extra tab ``show: <name>``) and the log.

    Args:
        value: A table, number, text, figure or anything else.
        name: Tab name; default ``value1``, ``value2`` ...

    Returns:
        ``value`` itself, so ``df = show(df.merge(...), "merged")`` works.
    """
    label = name or f"value{next(_counter)}"
    sink = _SINK.get()
    summary = summarize(value)
    if sink is None:
        print(f"show: {label} = " + "\n    ".join(summary))
        return value
    try:
        snapshot = value.copy() if hasattr(value, "copy") and not callable(value) else value
    except Exception:  # an object whose copy() needs arguments
        snapshot = copy.copy(value)
    sink(label, snapshot)
    print(f"show: {label} = {summary[0]}")
    return value


@contextmanager
def collecting(sink: Callable[[str, Any], None]) -> Iterator[None]:
    """Send :func:`show` calls to ``sink`` while the ``with`` block runs (the Python block)."""
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


def _short(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def summarize(value: Any) -> list[str]:
    """Describe a value in a few lines.

    Tables: ``DataFrame 120 rows x 4 columns: EID, SUBCASE, ...`` plus the first rows;
    arrays: shape and type; other values: their ``repr``, shortened.
    """
    import numpy as np
    import pandas as pd

    if isinstance(value, pd.DataFrame):
        columns = [str(c) for c in value.columns]
        shown = ", ".join(columns[:12]) + (", …" if len(columns) > 12 else "")
        head = value.head(SUMMARY_ROWS).to_string(max_cols=12, max_colwidth=20)
        more = [f"… {len(value) - SUMMARY_ROWS} more rows"] if len(value) > SUMMARY_ROWS else []
        return [
            f"DataFrame {len(value)} rows x {len(columns)} columns: {shown}",
            *head.splitlines(),
            *more,
        ]
    if isinstance(value, pd.Series):
        head = value.head(SUMMARY_ROWS).to_string()
        return [f"Series '{value.name}' with {len(value)} values", *head.splitlines()]
    if isinstance(value, np.ndarray):
        return [f"array shape {value.shape} {value.dtype}: {_short(np.array2string(value))}"]
    if isinstance(value, dict | list | tuple | set):
        return [f"{type(value).__name__} of {len(value)}: {_short(repr(value))}"]
    return [_short(repr(value))]
