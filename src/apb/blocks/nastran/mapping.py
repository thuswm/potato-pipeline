"""Knowledge about MSC Nastran HDF5 result datasets (spec 02 §3.2).

Each supported dataset has a :class:`ResultMapping`: which field is the ID, how fields are
renamed into the output table, the quantity kind of each column (for unit labels), whether
array fields hold beam stations, and derived columns. Datasets without a mapping are read
"raw" (NAS-07). Adapting to another Nastran version only needs changes here.

Reference version: MSC Nastran 2023.3. Verified against the real files in ``examples/fem``
(spec 02 §3.9): nodal displacements, eigenvectors, SPC forces, applied loads, grid point
weight, CBUSH forces, element strain energy, eigenvalue summary and the modal effective mass
results. Not yet verified (synthetic test files only): CQUAD4, CBEAM, MPC forces, grid point
forces.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from apb.units import QuantityKind as Q

#: Function adding derived columns to a table (in place).
type Deriver = Callable[[pd.DataFrame], None]

#: Function building the table from the raw dataset rows, for datasets that are not one
#: table row per dataset row (matrices). Must keep a ``DOMAIN_ID`` column if the data has one.
type Transform = Callable[[npt.NDArray[Any]], pd.DataFrame]

#: Names of the six degrees of freedom in modal effective mass output.
DOF_NAMES = ("T1", "T2", "T3", "R1", "R2", "R3")


@dataclass(frozen=True)
class ResultMapping:
    """How to turn one HDF5 result dataset into a table.

    Attributes:
        title: Display name.
        id_field: Field holding the entity ID (``None`` for summary tables).
        id_column: Output column name of the ID (``GRID`` or ``EID``).
        rename: Field name -> output column name.
        kinds: Output column name -> quantity kind.
        stations: Array fields are per-station values (CBEAM) -> one row per station.
        derive: Functions adding derived columns.
        transform: Builds the table from the raw rows instead of the default conversion.
        size_label: Size shown in the result browser instead of the row count, e.g. ``6 x 6``.
    """

    title: str
    id_field: str | None
    id_column: str
    rename: dict[str, str] = field(default_factory=dict)
    kinds: dict[str, Q] = field(default_factory=dict)
    stations: bool = False
    derive: tuple[Deriver, ...] = ()
    transform: Transform | None = None
    size_label: str = ""


def _matrix6(data: npt.NDArray[Any]) -> pd.DataFrame:
    """Turn rows holding a 6 x 6 matrix (fields ``COL1 .. COL6``) into six table rows each.

    Args:
        data: Raw dataset rows; field ``COLj`` holds column ``j`` of the matrix.

    Returns:
        Table ``DOF, T1 .. R3`` (and ``DOMAIN_ID``) with one row per matrix row.
    """
    n = len(data)
    columns: dict[str, Any] = {"DOF": np.tile(np.array(DOF_NAMES, dtype=object), n)}
    for j, name in enumerate(DOF_NAMES):
        columns[name] = np.asarray(data[f"COL{j + 1}"], dtype=float).reshape(n, 6).ravel()
    if "DOMAIN_ID" in (data.dtype.names or ()):
        columns["DOMAIN_ID"] = np.repeat(data["DOMAIN_ID"], 6)
    return pd.DataFrame(columns)


def _grid_weight(data: npt.NDArray[Any]) -> pd.DataFrame:
    """Turn grid point weight generator output into a mass / centre of gravity table.

    Args:
        data: Raw dataset rows (fields ``MX, XX, YX, ZX, MY, ...``: mass and centre of
            gravity per mass direction).

    Returns:
        Table ``DIRECTION, MASS, X_CG, Y_CG, Z_CG`` (and ``DOMAIN_ID``), three rows per
        dataset row.
    """
    rows: list[dict[str, Any]] = []
    has_domain = "DOMAIN_ID" in (data.dtype.names or ())
    for rec in data:
        for d in "XYZ":
            row: dict[str, Any] = {
                "DIRECTION": d,
                "MASS": float(rec[f"M{d}"]),
                "X_CG": float(rec[f"X{d}"]),
                "Y_CG": float(rec[f"Y{d}"]),
                "Z_CG": float(rec[f"Z{d}"]),
            }
            if has_domain:
                row["DOMAIN_ID"] = int(rec["DOMAIN_ID"])
            rows.append(row)
    names = ["DIRECTION", "MASS", "X_CG", "Y_CG", "Z_CG"] + (["DOMAIN_ID"] if has_domain else [])
    return pd.DataFrame(rows, columns=names)


def _modal_kinds(translation: Q | None, rotation: Q | None) -> dict[str, Q]:
    """Return kinds for a modal table with ``FREQUENCY`` and the columns ``T1 .. R3``."""
    out: dict[str, Q] = {"FREQUENCY": Q.FREQUENCY}
    for name in DOF_NAMES:
        kind = translation if name.startswith("T") else rotation
        if kind is not None:
            out[name] = kind
    return out


def _von_mises_quad4(df: pd.DataFrame) -> None:
    """Add plane-stress von Mises columns for both fibres and their maximum."""
    for z in ("Z1", "Z2"):
        sx, sy, txy = df[f"SX_{z}"], df[f"SY_{z}"], df[f"TXY_{z}"]
        df[f"VON_MISES_{z}"] = np.sqrt(sx**2 - sx * sy + sy**2 + 3.0 * txy**2)
    df["VON_MISES"] = np.maximum(df["VON_MISES_Z1"], df["VON_MISES_Z2"])


def _six(
    prefix_f: str = "", prefix_m: str = "R", kind_f: Q = Q.FORCE, kind_m: Q = Q.MOMENT
) -> dict[str, Q]:
    """Return kinds for the six components X, Y, Z, RX, RY, RZ."""
    out = {f"{prefix_f}{c}": kind_f for c in "XYZ"}
    out.update({f"{prefix_m}{c}": kind_m for c in "XYZ"})
    return out


def _quad4_stress_rename() -> dict[str, str]:
    """Rename QUAD4 stress/strain fields to fibre-suffixed names."""
    out: dict[str, str] = {}
    for i, z in (("1", "Z1"), ("2", "Z2")):
        out.update(
            {f"FD{i}": f"FD_{z}", f"X{i}": f"SX_{z}", f"Y{i}": f"SY_{z}", f"XY{i}": f"TXY_{z}"}
        )
    return out


def _quad4_kinds(kind: Q) -> dict[str, Q]:
    """Return kinds for renamed QUAD4 stress/strain columns."""
    out: dict[str, Q] = {}
    for z in ("Z1", "Z2"):
        out[f"FD_{z}"] = Q.LENGTH
        out.update({f"SX_{z}": kind, f"SY_{z}": kind, f"TXY_{z}": kind})
    if kind is Q.STRESS:
        out.update({"VON_MISES_Z1": kind, "VON_MISES_Z2": kind, "VON_MISES": kind})
    return out


_NODAL_RENAME = {"ID": "GRID"}

#: Dataset path (relative to ``/NASTRAN/RESULT``) -> mapping.
RESULT_MAPPINGS: dict[str, ResultMapping] = {
    "NODAL/DISPLACEMENT": ResultMapping(
        "Displacements", "ID", "GRID", _NODAL_RENAME, _six(kind_f=Q.LENGTH, kind_m=Q.ROTATION)
    ),
    "NODAL/EIGENVECTOR": ResultMapping(
        "Eigenvectors (mode shapes)",
        "ID",
        "GRID",
        _NODAL_RENAME,
        _six(kind_f=Q.DIMENSIONLESS, kind_m=Q.DIMENSIONLESS),
    ),
    "NODAL/SPC_FORCE": ResultMapping("SPC forces", "ID", "GRID", _NODAL_RENAME, _six()),
    "NODAL/MPC_FORCE": ResultMapping("MPC forces", "ID", "GRID", _NODAL_RENAME, _six()),
    "NODAL/GRID_FORCE": ResultMapping(
        "Grid point forces",
        "ID",
        "GRID",
        _NODAL_RENAME,
        {**{f"F{i}": Q.FORCE for i in "123"}, **{f"M{i}": Q.MOMENT for i in "123"}},
    ),
    "ELEMENTAL/ELEMENT_FORCE/QUAD4": ResultMapping(
        "CQUAD4 forces",
        "EID",
        "EID",
        {},
        {
            **{c: Q.FORCE_PER_LENGTH for c in ("MX", "MY", "MXY", "TX", "TY")},
            **{c: Q.MOMENT_PER_LENGTH for c in ("BMX", "BMY", "BMXY")},
        },
    ),
    "ELEMENTAL/STRESS/QUAD4": ResultMapping(
        "CQUAD4 stresses",
        "EID",
        "EID",
        _quad4_stress_rename(),
        _quad4_kinds(Q.STRESS),
        derive=(_von_mises_quad4,),
    ),
    "ELEMENTAL/STRAIN/QUAD4": ResultMapping(
        "CQUAD4 strains", "EID", "EID", _quad4_stress_rename(), _quad4_kinds(Q.STRAIN)
    ),
    "ELEMENTAL/ELEMENT_FORCE/BEAM": ResultMapping(
        "CBEAM forces",
        "EID",
        "EID",
        {},
        {
            "SD": Q.DIMENSIONLESS,
            "BM1": Q.MOMENT,
            "BM2": Q.MOMENT,
            "TS1": Q.FORCE,
            "TS2": Q.FORCE,
            "AF": Q.FORCE,
            "TTRQ": Q.MOMENT,
            "WTRQ": Q.MOMENT,
        },
        stations=True,
    ),
    "ELEMENTAL/STRESS/BEAM": ResultMapping(
        "CBEAM stresses",
        "EID",
        "EID",
        {},
        {
            "SD": Q.DIMENSIONLESS,
            **{c: Q.STRESS for c in ("XC", "XD", "XE", "XF", "MAX", "MIN")},
            "MST": Q.DIMENSIONLESS,
            "MSC": Q.DIMENSIONLESS,
        },
        stations=True,
    ),
    "ELEMENTAL/ELEMENT_FORCE/BUSH": ResultMapping("CBUSH forces", "EID", "EID", {}, _six("F", "M")),
    "ELEMENTAL/ENERGY/STRAIN_ELEM": ResultMapping(
        "Element strain energy",
        "ID",
        "EID",
        {"ID": "EID", "DEN": "DENSITY"},
        {"ENERGY": Q.ENERGY, "PCT": Q.DIMENSIONLESS, "DENSITY": Q.ENERGY_DENSITY},
    ),
    "NODAL/APPLIED_LOAD": ResultMapping("Applied loads", "ID", "GRID", _NODAL_RENAME, _six()),
    "NODAL/GRID_WEIGHT": ResultMapping(
        "Grid point weight (mass and centre of gravity)",
        None,
        "",
        {},
        {"MASS": Q.MASS, "X_CG": Q.LENGTH, "Y_CG": Q.LENGTH, "Z_CG": Q.LENGTH},
        transform=_grid_weight,
        size_label="3 rows",
    ),
    "MODAL/MODAL_EFFECTIVE_MASS": ResultMapping(
        "Modal effective mass", None, "", {}, _modal_kinds(Q.MASS, Q.MASS_INERTIA)
    ),
    "MODAL/MODAL_EFFECTIVE_WEIGHT": ResultMapping(
        "Modal effective weight", None, "", {}, _modal_kinds(None, None)
    ),
    "MODAL/MODAL_PARTICIPATION_FACTOR": ResultMapping(
        "Modal participation factors", None, "", {}, _modal_kinds(None, None)
    ),
    "MODAL/MODAL_MASS_FRACTION": ResultMapping(
        "Modal effective mass fractions",
        None,
        "",
        {},
        {
            "FREQUENCY": Q.FREQUENCY,
            **{f"{n}_{s}": Q.DIMENSIONLESS for n in DOF_NAMES for s in ("FRAC", "SUM")},
        },
    ),
    "MODAL/TOTAL_MASS_FRACTION": ResultMapping(
        "Total effective mass fraction", None, "", {}, dict.fromkeys(DOF_NAMES, Q.DIMENSIONLESS)
    ),
    "MODAL/EFFECTIVE_MASS_MATRIX": ResultMapping(
        "Effective mass matrix", None, "", transform=_matrix6, size_label="6 x 6"
    ),
    "MODAL/RIGID_BODY_MASS_MATRIX": ResultMapping(
        "Rigid body mass matrix", None, "", transform=_matrix6, size_label="6 x 6"
    ),
    "SUMMARY/EIGENVALUE": ResultMapping(
        "Eigenvalues",
        None,
        "MODE",
        {"EIGEN": "EIGENVALUE"},
        {"EIGENVALUE": Q.EIGENVALUE, "OMEGA": Q.ANGULAR_FREQUENCY, "FREQ": Q.FREQUENCY},
    ),
}
