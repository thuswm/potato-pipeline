"""Fastener margins.

Script for an Analysis Pipeline Builder Python block. The block calls ``run`` with one
argument per input port and expects a dict with one entry per output port. Tables arrive
as pandas DataFrames (units in ``df.attrs["units"]``), scalars as plain numbers.
Use print() for messages: they appear in the block's log.

EXAMPLE ONLY: the interaction criterion below (Rt^2 + Rs^2 <= 1) is a placeholder to show
the mechanics, not a validated fastener criterion.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def run(
    loads: pd.DataFrame,
    allowable_tension: float,
    allowable_shear: float,
    safety_factor: float,
) -> dict[str, object]:
    """Margins of safety per fastener and load case.

    Args:
        loads: EID, SUBCASE, TENSION, SHEAR per fastener and load case.
        allowable_tension: Tension allowable of the fastener.
        allowable_shear: Shear allowable of the fastener.
        safety_factor: Ultimate factor applied to the loads.

    Returns:
        Dict with: margins (Table), min_ms (Scalar), plot (Figure).
    """
    r_t = safety_factor * loads["TENSION"].clip(lower=0.0) / allowable_tension
    r_s = safety_factor * loads["SHEAR"] / allowable_shear
    ms = 1.0 / np.sqrt(r_t**2 + r_s**2) - 1.0

    margins = loads[["EID", "SUBCASE", "TENSION", "SHEAR"]].assign(R_T=r_t, R_S=r_s, MS=ms)
    units = {k: v for k, v in loads.attrs.get("units", {}).items() if k in margins.columns}
    margins.attrs["units"] = {**units, "R_T": "-", "R_S": "-", "MS": "-"}

    min_ms = float(ms.min())
    worst = margins.loc[ms.idxmin()]
    print(f"Minimum margin {min_ms:.2f} at fastener {int(worst.EID)}, subcase {int(worst.SUBCASE)}")

    per_fastener = margins.groupby("EID")["MS"].min()
    fig, ax = plt.subplots(figsize=(6, 3))
    colors = ["tab:red" if v < 0 else "tab:blue" for v in per_fastener]
    ax.bar(per_fastener.index.astype(str), per_fastener.to_numpy(), color=colors)
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_xlabel("Fastener EID")
    ax.set_ylabel("Minimum margin of safety")
    fig.tight_layout()

    return {"margins": margins, "min_ms": min_ms, "plot": fig}
