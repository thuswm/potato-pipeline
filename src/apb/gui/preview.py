"""Preview of a block's outputs, log and error (GUI-10, TAB-01)."""

from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from nicegui import ui

from apb.engine.events import BlockState
from apb.engine.executor import BlockResult
from apb.engine.store import StoreError
from apb.units import Quantity, get_units, header_with_unit

#: Files a text output can be opened from (Word, Excel, Nastran text files).
OPENABLE_SUFFIXES = {".docx", ".xlsx", ".f06", ".f04", ".log", ".bdf", ".dat"}

#: Rows shown in a table preview (the full table is used by the pipeline).
PREVIEW_ROWS = 500


def open_with_system(path: Path) -> None:
    """Open a file with the program registered for it (Word, Excel ...)."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def table_rows(
    df: pd.DataFrame, limit: int = PREVIEW_ROWS
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert a table to Quasar table columns and rows (two-level headers flattened)."""
    units = get_units(df)
    head = df.head(limit)
    columns: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = [{} for _ in range(len(head))]
    for i, col in enumerate(df.columns):
        if isinstance(col, tuple):
            top, sub = str(col[0]), str(col[1])
            label = header_with_unit(top, units) + (f" / {sub}" if sub else "")
        else:
            label = header_with_unit(str(col), units)
        key = f"c{i}"
        numeric = pd.api.types.is_numeric_dtype(head.iloc[:, i])
        columns.append(
            {
                "name": key,
                "label": label,
                "field": key,
                "sortable": True,
                "align": "right" if numeric else "left",
            }
        )
        for r, value in enumerate(head.iloc[:, i].tolist()):
            rows[r][key] = _cell(value)
    return columns, rows


def _cell(value: Any) -> Any:
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        return float(f"{value:.6g}")
    if isinstance(value, int | str | bool) or value is None:
        return value
    return str(value)


def show_value(name: str, value: Any) -> None:
    """Render one output value in the current context."""
    if isinstance(value, pd.DataFrame):
        ui.label(
            f"{len(value)} rows x {len(value.columns)} columns"
            + (f" (first {PREVIEW_ROWS} shown)" if len(value) > PREVIEW_ROWS else "")
            + (f" · units {value.attrs['unit_system']}" if value.attrs.get("unit_system") else "")
        ).classes("text-xs text-slate-500")
        columns, rows = table_rows(value)
        ui.table(columns=columns, rows=rows, pagination=15).props(
            "dense flat bordered wrap-cells"
        ).classes("w-full text-xs")
    elif hasattr(value, "savefig"):
        buffer = io.BytesIO()
        value.savefig(buffer, format="png", dpi=100, bbox_inches="tight")
        data = base64.b64encode(buffer.getvalue()).decode("ascii")
        ui.image(f"data:image/png;base64,{data}").classes("max-w-xl")
    elif isinstance(value, Quantity | int | float):
        text = format(value, ".6g") if not isinstance(value, bool) else str(value)
        ui.label(text).classes("text-2xl font-medium")
    elif isinstance(value, str):
        path = Path(value)
        if len(value) < 400 and path.suffix.lower() in OPENABLE_SUFFIXES and path.is_file():
            with ui.row().classes("items-center gap-2"):
                ui.label(value).classes("text-sm break-all")
                ui.button(
                    "Open", icon="open_in_new", on_click=lambda p=path: open_with_system(p)
                ).props("dense")
        else:
            ui.label(value).classes("text-sm whitespace-pre-wrap")
    else:
        ui.label(repr(value)[:2000]).classes("text-sm font-mono")


def stored_time(finished: str) -> str:
    """Return ``2026-11-15T17:08:12+01:00`` as ``2026-11-15 17:08`` for display."""
    return finished[:16].replace("T", " ")


def result_origin(result: BlockResult, outdated: bool = False) -> str:
    """Say where a result comes from (spec 08 §3.9).

    Args:
        result: The block's result.
        outdated: The block's inputs or parameters changed since the result was made.

    Returns:
        E.g. ``"Done in 0.19 s"``, ``"Done · stored 2026-11-15 17:08"`` or ``"Outdated · …"``.
    """
    when = f"stored {stored_time(result.finished)}" if result.finished else "stored"
    if result.state is not BlockState.DONE:
        return {BlockState.ERROR: "Error", BlockState.SKIPPED: "Skipped"}.get(
            result.state, str(result.state)
        )
    if outdated:
        source = (
            when
            if result.restored
            else (f"made {stored_time(result.finished)}" if result.finished else "earlier")
        )
        return f"Outdated · {source} — parameters or inputs changed since"
    if result.restored:
        return f"Done · {when}"
    return "Done (cached)" if result.cached else f"Done in {result.duration:.2f} s"


def show_result(result: BlockResult | None, outdated: bool = False) -> None:
    """Render a block's latest result: error, outputs (one tab each) and log.

    Args:
        result: The result, or ``None`` if the block has none.
        outdated: The block's inputs or parameters changed since the result was made.
    """
    if result is None:
        ui.label("Not run yet. Use Run, or Run to here for this block.").classes(
            "text-sm text-slate-500"
        )
        return
    origin = ui.label(result_origin(result, outdated)).classes(
        "text-xs apb-origin " + ("text-amber-700" if outdated else "text-slate-500")
    )
    origin.tooltip("Where this result comes from")
    if result.state is BlockState.ERROR:
        ui.label(result.error).classes("text-sm text-red-700 whitespace-pre-wrap")
        if result.traceback:
            with ui.expansion("Details (traceback)").classes("w-full").props("dense"):
                ui.label(result.traceback).classes("text-xs font-mono whitespace-pre-wrap")
    elif result.state is BlockState.SKIPPED:
        ui.label(result.error).classes("text-sm text-amber-700")
    if result.outputs:
        names = list(result.outputs)
        with ui.tabs().props("dense align=left").classes("text-xs") as tabs:
            tab_items = {n: ui.tab(n) for n in names}
        with ui.tab_panels(tabs, value=tab_items[names[0]]).classes("w-full"):
            for n in names:
                with ui.tab_panel(tab_items[n]).classes("p-1"):
                    try:
                        show_value(n, result.outputs[n])
                    except StoreError as exc:
                        ui.label(str(exc)).classes("text-sm text-red-700 apb-store-error")
    if result.log:
        with ui.expansion(f"Block log ({len(result.log)} lines)").classes("w-full").props("dense"):
            ui.label("\n".join(result.log)).classes("text-xs font-mono whitespace-pre-wrap")
