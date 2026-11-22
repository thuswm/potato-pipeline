"""CBUSH margins of safety per load case, element and location.

Script for an Potato Pipeline Python block. The block calls ``run`` with one
argument per input port and expects a dict with one entry per output port. Tables arrive
as pandas DataFrames (units in ``df.attrs["units"]``), scalars as plain numbers.
Use print() for messages: they appear in the block's log.

A *location* is a place where the same set of loads is assessed, for example several joints
that use the same CBUSH forces, each with its own allowables. The locations come in as a
table, so the same script serves any number of joints.

EXAMPLE ONLY: the criterion below (linear interaction of tension and shear) is a placeholder
that shows the mechanics. It is not a validated fastener criterion, and the allowables in
``examples/test_case/locations.csv`` are made up.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

AXES = ("X", "Y", "Z")


def _require(table: pd.DataFrame, columns: list[str], name: str) -> None:
    """Fail with a clear message if columns are missing."""
    missing = [c for c in columns if c not in table.columns]
    if missing:
        raise ValueError(f"Table '{name}' lacks column(s) {missing}. Has: {list(table.columns)}")


def run(forces: pd.DataFrame, locations: pd.DataFrame, safety_factor: float) -> dict[str, object]:
    """Margins of safety of every element at every location.

    Args:
        forces: CBUSH forces: EID, SUBCASE, FX, FY, FZ (one row per element and load case).
        locations: One row per location. LOCATION (name), TENSION_ALLOWABLE and
            SHEAR_ALLOWABLE (in the unit of the forces) are required; EID (restrict the
            location to one element; empty = all elements) and AXIS (X, Y or Z: the force
            component along the fastener axis; empty = Z) are optional.
        safety_factor: Factor applied to the loads.

    Returns:
        ``margins``: SUBCASE, EID, LOCATION, TENSION, SHEAR, MS per load case, element and
        location (MS is empty where the load is zero); ``min_ms``: the smallest margin
        (NaN if there is none).
    """
    _require(forces, ["EID", "SUBCASE", "FX", "FY", "FZ"], "forces")
    _require(locations, ["LOCATION", "TENSION_ALLOWABLE", "SHEAR_ALLOWABLE"], "locations")
    if forces.empty:
        raise ValueError("The forces table is empty")
    if safety_factor <= 0:
        raise ValueError(f"The safety factor must be positive, got {safety_factor}")

    parts: list[pd.DataFrame] = []
    for _, loc in locations.iterrows():
        name = str(loc["LOCATION"])
        t_allow, s_allow = float(loc["TENSION_ALLOWABLE"]), float(loc["SHEAR_ALLOWABLE"])
        if t_allow <= 0 or s_allow <= 0:
            raise ValueError(f"Location {name!r}: the allowables must be positive")
        axis = str(loc["AXIS"]).strip().upper() if "AXIS" in loc and pd.notna(loc["AXIS"]) else "Z"
        if axis not in AXES:
            raise ValueError(f"Location {name!r}: AXIS must be X, Y or Z, got {axis!r}")

        rows = forces
        if "EID" in loc and pd.notna(loc["EID"]):
            eid = int(loc["EID"])
            rows = forces[forces["EID"] == eid]
            if rows.empty:
                raise ValueError(f"Location {name!r}: element {eid} is not in the forces table")

        along = rows[f"F{axis}"].to_numpy(dtype=float)
        across = [rows[f"F{a}"].to_numpy(dtype=float) for a in AXES if a != axis]
        tension = np.clip(along, 0.0, None)  # compression carries no tension
        shear = np.hypot(across[0], across[1])
        ratio = tension / t_allow + shear / s_allow
        with np.errstate(divide="ignore"):
            ms = np.where(ratio > 0, 1.0 / (safety_factor * ratio) - 1.0, np.nan)
        parts.append(
            pd.DataFrame(
                {
                    "SUBCASE": rows["SUBCASE"].to_numpy(),
                    "EID": rows["EID"].to_numpy(),
                    "LOCATION": name,
                    "TENSION": tension,
                    "SHEAR": shear,
                    "MS": ms,
                }
            )
        )

    margins = (
        pd.concat(parts, ignore_index=True)
        .sort_values(["SUBCASE", "EID", "LOCATION"], kind="stable")
        .reset_index(drop=True)
    )
    unit = forces.attrs.get("units", {}).get("FX", "")
    margins.attrs["units"] = {"TENSION": unit, "SHEAR": unit, "MS": "-"} if unit else {"MS": "-"}

    known = margins["MS"].dropna()
    if known.empty:
        min_ms = float("nan")
        print("All loads are zero: no margin of safety is defined")
    else:
        worst = margins.loc[known.idxmin()]
        min_ms = float(known.min())
        print(
            f"Minimum margin {min_ms:.3f} at element {int(worst['EID'])}, "
            f"{worst['LOCATION']}, subcase {int(worst['SUBCASE'])}"
        )
    return {"margins": margins, "min_ms": min_ms}
