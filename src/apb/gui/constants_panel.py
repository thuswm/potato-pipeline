"""Property panel of the Constants block: an editable table of constants (spec 03 §3.5.3).

Each row is edited in place. A row is stored only when it is valid, so a half-typed name
never removes a port (and with it its connectors). Renaming a stored constant goes through
the editor, which also moves its connectors and updates ``${name}`` references.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nicegui import ui

from apb.blocks.table.constants import name_problem, unit_problem
from apb.units import known_units


@dataclass
class DraftRow:
    """One row as typed in the panel (may be invalid).

    Attributes:
        name: Name as typed.
        value: Value as typed (text, so ``2e5`` and half-typed numbers are possible).
        unit: Unit label.
        description: Description.
        stored: Name of the stored constant this row edits (``""`` for a new row).
        error: Problem shown under the row.
    """

    name: str = ""
    value: str = ""
    unit: str = ""
    description: str = ""
    stored: str = ""
    error: str = field(default="", compare=False)

    @classmethod
    def from_stored(cls, row: dict[str, Any]) -> DraftRow:
        """Create a draft from a stored row."""
        value = row.get("value", "")
        return cls(
            name=str(row.get("name", "")),
            value=f"{value:g}" if isinstance(value, int | float) else str(value),
            unit=str(row.get("unit", "")),
            description=str(row.get("description", "")),
            stored=str(row.get("name", "")),
        )


def parse_value(text: str) -> float | None:
    """Return the number typed, or ``None`` if it is not a finite number (CON-02)."""
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def row_problem(row: DraftRow, others: list[DraftRow], taken: set[str]) -> str:
    """Return what is wrong with a row, or ``""`` (CON-01 … CON-03, CON-08).

    Args:
        row: The row.
        others: The other rows of the block.
        taken: Names used elsewhere (pipeline variables, constants of other blocks).
    """
    problem = name_problem(row.name)
    if problem:
        return problem
    if any(o.name == row.name for o in others) or row.name in taken:
        return f"The name {row.name!r} is already used"
    if parse_value(row.value) is None:
        return "Enter a number, e.g. 1.5 or 2e5"
    return unit_problem(row.unit)


def to_stored(row: DraftRow) -> dict[str, Any]:
    """Return the row as stored in the block parameters."""
    value = parse_value(row.value)
    stored: dict[str, Any] = {"name": row.name, "value": value}
    if row.unit:
        stored["unit"] = row.unit
    if row.description:
        stored["description"] = row.description
    return stored


class ConstantsPanel:
    """Builds the constants editor for one Constants block (CON-10).

    Args:
        params: Stored parameters of the block.
        on_change: Stores a parameter (``"constants"``, list of rows).
        rename: Renames a stored constant everywhere; returns the change lines; raises
            ``ValueError`` with a message if not possible.
        users: Returns where a constant is used.
        taken: Returns the names used outside this block (variables, other constants).
        confirm: Asks a yes/no question.
    """

    def __init__(
        self,
        params: dict[str, Any],
        *,
        on_change: Callable[[str, Any], None],
        rename: Callable[[str, str], list[str]],
        users: Callable[[str], list[str]],
        taken: Callable[[], set[str]],
        confirm: Callable[[str], Awaitable[bool]],
    ) -> None:
        self.params = params
        self.on_change = on_change
        self.rename = rename
        self.users = users
        self.taken = taken
        self.confirm = confirm
        self.rows = [DraftRow.from_stored(r) for r in self.stored_rows()]

    def stored_rows(self) -> list[dict[str, Any]]:
        """The rows currently stored in the block."""
        rows = self.params.get("constants") or []
        return [dict(r) for r in rows if isinstance(r, dict)]

    # ---- widgets --------------------------------------------------------------------------

    def build(self) -> None:
        """Create the widgets in the current NiceGUI context."""
        self.box = ui.column().classes("w-full gap-1 apb-constants")
        self._render()

    def _show_status(self, index: int) -> None:
        """Show a row's error, else where it is used (updated in place, no rebuild)."""
        row, label = self.rows[index], self.status[index]
        if row.error:
            label.set_text(row.error)
            label.classes(replace="text-xs text-red-700 apb-constant-error")
        elif row.stored:
            used = self.users(row.stored)
            label.set_text("Used by: " + ", ".join(used) if used else "Not used yet")
            label.classes(replace="text-[10px] text-slate-500 apb-constant-users")
        else:
            label.set_text("")
            label.classes(replace="")

    def _render(self) -> None:
        self.box.clear()
        self.status: list[ui.label] = []
        units = {"": "(none)", **{u: u for u in known_units() if u != "-"}}
        with self.box:
            if not self.rows:
                ui.label("No constants yet.").classes("text-xs text-slate-500")
            for index, row in enumerate(self.rows):
                with ui.column().classes("w-full gap-0 border rounded p-1 apb-constant-row"):
                    with ui.row().classes("w-full no-wrap items-start gap-1"):
                        name = (
                            ui.input("Name", value=row.name)
                            .props("dense")
                            .classes("w-28 apb-constant-name")
                        )
                        value = (
                            ui.input("Value", value=row.value)
                            .props("dense")
                            .classes("w-20 apb-constant-value")
                        )
                        unit = (
                            ui.select(
                                units, value=row.unit if row.unit in units else "", label="Unit"
                            )
                            .props("dense options-dense")
                            .classes("w-20 apb-constant-unit")
                        )
                        with ui.column().classes("gap-0"):
                            ui.button(
                                icon="arrow_upward", on_click=lambda i=index: self._move(i, -1)
                            ).props("flat dense size=xs").tooltip("Move up")
                            ui.button(
                                icon="arrow_downward", on_click=lambda i=index: self._move(i, 1)
                            ).props("flat dense size=xs").tooltip("Move down")
                        ui.button(icon="delete", on_click=lambda i=index: self._delete(i)).props(
                            f'flat dense size=sm aria-label="Delete {row.name}"'
                        ).classes("apb-constant-delete").tooltip("Delete this constant")
                    description = (
                        ui.input("Description", value=row.description)
                        .props("dense")
                        .classes("w-full apb-constant-description")
                    )
                    self.status.append(ui.label())
                    self._show_status(index)

                    def commit(
                        i: int = index,
                        n: Any = name,
                        v: Any = value,
                        u: Any = unit,
                        d: Any = description,
                    ) -> None:
                        self._commit(i, n.value or "", v.value or "", u.value or "", d.value or "")

                    for widget in (name, value, description):
                        widget.on("keydown.enter", lambda c=commit: c())
                        widget.on("blur", lambda c=commit: c())
                    unit.on_value_change(lambda _e, c=commit: c())
            ui.button("Add constant", icon="add", on_click=self._add).props(
                "flat dense no-caps size=sm"
            ).classes("apb-constant-add")

    # ---- editing --------------------------------------------------------------------------

    def _commit(self, index: int, name: str, value: str, unit: str, description: str) -> None:
        """Take the typed values of a row; store them if the row is valid."""
        row = self.rows[index]
        typed = DraftRow(name.strip(), value.strip(), unit, description.strip(), row.stored)
        if typed == row and not row.error:
            return
        others = [r for i, r in enumerate(self.rows) if i != index]
        typed.error = row_problem(typed, others, self.taken())
        self.rows[index] = typed
        if typed.error:
            self._show_status(index)
            return
        if typed.stored and typed.stored != typed.name:
            try:
                changes = self.rename(typed.stored, typed.name)
            except ValueError as exc:
                typed.error = str(exc)
                self._show_status(index)
                return
            if changes:
                ui.notify(
                    f"Renamed {typed.stored} to {typed.name}: " + "; ".join(changes),
                    multi_line=True,
                    position="bottom-right",
                )
        typed.stored = typed.name
        self._save()
        for i in range(len(self.rows)):
            self._show_status(i)

    def _store(self) -> None:
        """Save and rebuild the list (after adding, deleting or moving rows)."""
        self._save()
        self._render()

    def _save(self) -> None:
        """Write the valid rows (with their last valid version for rows being fixed)."""
        stored = {r["name"]: r for r in self.stored_rows()}
        rows: list[dict[str, Any]] = []
        for row in self.rows:
            if not row.error and row.stored:
                rows.append(to_stored(row))
            elif row.stored in stored:  # keep the stored version until the row is fixed
                rows.append(stored[row.stored])
        if rows != self.stored_rows():
            self.on_change("constants", rows)

    def _add(self) -> None:
        self.rows.append(DraftRow())
        self._render()

    def _move(self, index: int, step: int) -> None:
        target = index + step
        if 0 <= target < len(self.rows):
            self.rows[index], self.rows[target] = self.rows[target], self.rows[index]
            self._store()

    async def _delete(self, index: int) -> None:
        row = self.rows[index]
        used = self.users(row.stored) if row.stored else []
        if used and not await self.confirm(
            f"{row.stored} is used by {', '.join(used)}. Delete it anyway? Its connectors are "
            "removed and ${" + row.stored + "} becomes undefined."
        ):
            return
        del self.rows[index]
        self._store()
