"""Figure size metadata, without importing matplotlib (spec 13 §3.4).

Figures made by :func:`apb.figures.new_figure` carry ``apb_width_cm`` and ``apb_dpi``; the
report and the stored outputs read them with the functions here.
"""

from __future__ import annotations

from typing import Any

CM_PER_INCH = 2.54

DEFAULT_WIDTH_CM = 16.0
"""Default figure width (spec 13 §3.2)."""
DEFAULT_HEIGHT_CM = 9.0
"""Default figure height."""
DEFAULT_DPI = 300
"""Default resolution (decided 2026-11-26)."""


def figure_width_cm(figure: Any) -> float | None:
    """Return the width a figure was made for (``apb_width_cm``), or ``None``."""
    value = getattr(figure, "apb_width_cm", None)
    return float(value) if isinstance(value, int | float) and value > 0 else None


def figure_dpi(figure: Any) -> int | None:
    """Return the resolution a figure was made for (``apb_dpi``), or ``None``."""
    value = getattr(figure, "apb_dpi", None)
    return int(value) if isinstance(value, int | float) and value > 0 else None
