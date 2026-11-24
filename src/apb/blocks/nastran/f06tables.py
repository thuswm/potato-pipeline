"""Tables printed in an MSC Nastran ``.f06`` (spec 02 §3.11). Pure functions.

Some results are only, or more conveniently, in the ``.f06``: a modal run is often made
without HDF5 output. Three tables are read, with the same column names as the HDF5 results
(spec 02), so the f06 and HDF5 blocks can replace each other downstream:

* **Modal effective mass fraction** — the translational (T1, T2, T3) and the rotational
  (R1, R2, R3) table are combined into one table ``MODE, FREQUENCY, T1_FRAC, ... R3_FRAC``
  (and ``T1_SUM ... R3_SUM``, the running sums), as ``MODAL/MODAL_MASS_FRACTION``.
* **Grid point weight generator** — mass and centre of gravity per mass direction,
  ``DIRECTION, MASS, X_CG, Y_CG, Z_CG``, as ``NODAL/GRID_WEIGHT``.
* **Real eigenvalues** — ``MODE, EIGENVALUE, ORDER, OMEGA, FREQ, MASS, STIFF``, as
  ``SUMMARY/EIGENVALUE`` (spec 02 §3.12).

A table that is printed over several pages is read as one. The values are as printed.
"""

from __future__ import annotations

import re

import pandas as pd

from apb.blocks.nastran.mapping import DOF_NAMES

#: A number as Nastran prints it: ``2.571186E+01``, ``-3.1E-14``, ``0.0``.
_NUM = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[EeDd][-+]?\d+)?"

_MASS_FRACTION = re.compile(r"MODAL EFFECTIVE MASS FRACTION")
_FRACTION_KIND = re.compile(r"\(FOR (TRANSLATIONAL|ROTATIONAL) DEGREES OF FREEDOM\)")
_FRACTION_ROW = re.compile(rf"^\s*(\d+)\s+({_NUM})((?:\s+{_NUM}){{6}})\s*$")

_GPWG = re.compile(r"G R I D\s+P O I N T\s+W E I G H T\s+G E N E R A T O R")
_REFERENCE = re.compile(r"REFERENCE POINT\s*=\s*(\d+)")
_CG_HEADER = re.compile(r"MASS AXIS SYSTEM \(S\)\s+MASS")
_CG_ROW = re.compile(rf"^\s*([XYZ])\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s*$")

_EIGENVALUES = re.compile(r"R E A L\s+E I G E N V A L U E S")
_EIGEN_ROW = re.compile(rf"^\s*(\d+)\s+(\d+)((?:\s+{_NUM}){{5}})\s*$")

#: Columns of the eigenvalue table, as the HDF5 result ``SUMMARY/EIGENVALUE``.
EIGENVALUE_COLUMNS = ("MODE", "EIGENVALUE", "ORDER", "OMEGA", "FREQ", "MASS", "STIFF")

#: Lines between a table title and its first data row that are skipped at most.
_HEADER_LINES = 12


class F06TableError(ValueError):
    """A table is missing from the ``.f06`` or cannot be read; the message is for the user."""


def _float(text: str) -> float:
    return float(text.replace("D", "E").replace("d", "e"))


def _rows_after(lines: list[str], start: int, pattern: re.Pattern[str]) -> list[re.Match[str]]:
    """Return the data rows that follow line ``start``: the first run of matching lines.

    Up to :data:`_HEADER_LINES` non-matching lines (column headings, blank lines) are
    skipped before the first row; the first non-matching line after it ends the table.
    """
    rows: list[re.Match[str]] = []
    i = start + 1
    skipped = 0
    while i < len(lines):
        match = pattern.match(lines[i])
        if match is not None:
            rows.append(match)
        elif rows or skipped >= _HEADER_LINES:
            break
        else:
            skipped += 1
        i += 1
    return rows


def read_mass_fractions(text: str, sums: bool = False) -> pd.DataFrame:
    """Read the modal effective mass fractions of an ``.f06`` into one table.

    Args:
        text: Content of the ``.f06``.
        sums: Also return the running sums ``T1_SUM ... R3_SUM``.

    Returns:
        ``MODE, FREQUENCY, T1_FRAC, T2_FRAC, T3_FRAC, R1_FRAC, R2_FRAC, R3_FRAC`` (and the
        sums), one row per mode, sorted by mode.

    Raises:
        F06TableError: If the tables are not in the file (no ``MEFFMASS`` request), only
            one of the two is there, or a mode appears twice (several subcases).
    """
    lines = text.splitlines()
    found: dict[str, dict[int, dict[str, float]]] = {"T": {}, "R": {}}
    for i, line in enumerate(lines):
        if not _MASS_FRACTION.search(line):
            continue
        kind = next(
            (m.group(1) for m in map(_FRACTION_KIND.search, lines[i + 1 : i + 3]) if m), None
        )
        if kind is None:
            continue
        prefix = "T" if kind == "TRANSLATIONAL" else "R"
        names = [n for n in DOF_NAMES if n.startswith(prefix)]
        for match in _rows_after(lines, i + 1, _FRACTION_ROW):
            mode = int(match.group(1))
            if mode in found[prefix]:
                raise F06TableError(
                    f"Mode {mode} appears twice in the modal effective mass fractions "
                    "(several subcases?); this is not supported yet"
                )
            values = [_float(v) for v in match.group(3).split()]
            row: dict[str, float] = {"FREQUENCY": _float(match.group(2))}
            for n, name in enumerate(names):
                row[f"{name}_FRAC"] = values[2 * n]
                row[f"{name}_SUM"] = values[2 * n + 1]
            found[prefix][mode] = row
    if not found["T"] and not found["R"]:
        raise F06TableError(
            "No MODAL EFFECTIVE MASS FRACTION table in the .f06 "
            "(request it with MEFFMASS in the case control)"
        )
    missing = [k for k, rows in found.items() if not rows]
    if missing:
        which = "translational" if missing[0] == "T" else "rotational"
        raise F06TableError(f"The {which} modal effective mass fraction table is missing")
    if set(found["T"]) != set(found["R"]):
        raise F06TableError(
            "The translational and rotational mass fraction tables have different modes"
        )
    records = [
        {"MODE": mode, **found["R"][mode], **found["T"][mode]} for mode in sorted(found["T"])
    ]
    columns = ["MODE", "FREQUENCY", *(f"{n}_FRAC" for n in DOF_NAMES)]
    if sums:
        columns += [f"{n}_SUM" for n in DOF_NAMES]
    table = pd.DataFrame(records)[columns]
    table.attrs["title"] = "Modal effective mass fractions"
    return table


def read_grid_weight(text: str) -> tuple[pd.DataFrame, int]:
    """Read mass and centre of gravity from the grid point weight generator output.

    Args:
        text: Content of the ``.f06``.

    Returns:
        The table ``DIRECTION, MASS, X_CG, Y_CG, Z_CG`` (rows X, Y, Z) of the **first**
        output in the file, and the number of outputs found. The reference point is in
        ``attrs["reference_point"]``.

    Raises:
        F06TableError: If there is no output (no ``PARAM,GRDPNT``) or it cannot be read.
    """
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if _GPWG.search(line)]
    if not starts:
        raise F06TableError(
            "No GRID POINT WEIGHT GENERATOR output in the .f06 (request it with PARAM,GRDPNT)"
        )
    first = starts[0]
    end = starts[1] if len(starts) > 1 else len(lines)
    reference = None
    for line in lines[first : first + 4]:
        match = _REFERENCE.search(line)
        if match:
            reference = int(match.group(1))
            break
    header = next((i for i in range(first, end) if _CG_HEADER.search(lines[i])), None)
    rows = _rows_after(lines, header, _CG_ROW) if header is not None else []
    if [m.group(1) for m in rows] != ["X", "Y", "Z"]:
        raise F06TableError(
            "The mass and C.G. table of the grid point weight generator cannot be read"
        )
    table = pd.DataFrame(
        [
            {
                "DIRECTION": m.group(1),
                "MASS": _float(m.group(2)),
                "X_CG": _float(m.group(3)),
                "Y_CG": _float(m.group(4)),
                "Z_CG": _float(m.group(5)),
            }
            for m in rows
        ]
    )
    table.attrs["title"] = "Grid point weight (mass and centre of gravity)"
    if reference is not None:
        table.attrs["reference_point"] = reference
    return table, len(starts)


def read_eigenvalues(text: str) -> pd.DataFrame:
    """Read the real eigenvalue table of an ``.f06`` (spec 02 §3.12).

    Args:
        text: Content of the ``.f06``.

    Returns:
        ``MODE, EIGENVALUE, ORDER, OMEGA, FREQ, MASS, STIFF``, one row per mode, sorted by
        mode; the columns of the HDF5 result ``SUMMARY/EIGENVALUE`` without its flags.

    Raises:
        F06TableError: If there is no eigenvalue table or a mode appears twice (several
            subcases).
    """
    lines = text.splitlines()
    rows: dict[int, dict[str, float | int]] = {}
    for i, line in enumerate(lines):
        if not _EIGENVALUES.search(line):
            continue
        for match in _rows_after(lines, i, _EIGEN_ROW):
            mode = int(match.group(1))
            if mode in rows:
                raise F06TableError(
                    f"Mode {mode} appears twice in the real eigenvalue tables "
                    "(several subcases?); this is not supported yet"
                )
            eigen, omega, freq, mass, stiff = (_float(v) for v in match.group(3).split())
            rows[mode] = {
                "MODE": mode,
                "EIGENVALUE": eigen,
                "ORDER": int(match.group(2)),
                "OMEGA": omega,
                "FREQ": freq,
                "MASS": mass,
                "STIFF": stiff,
            }
    if not rows:
        raise F06TableError("No REAL EIGENVALUES table in the .f06 (not a normal modes run?)")
    table = pd.DataFrame([rows[m] for m in sorted(rows)], columns=list(EIGENVALUE_COLUMNS))
    table.attrs["title"] = "Eigenvalues"
    return table
