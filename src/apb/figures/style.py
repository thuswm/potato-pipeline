"""The house style of all figures and figures created at their size in the report.

A figure created with :func:`new_figure` at 16 x 9 cm and placed 16 cm wide in Word shows
its text at the size set here (9 pt), because the figure is drawn at its real size. The
figure remembers its width and resolution (``apb_width_cm``, ``apb_dpi``), which the report
uses as defaults (spec 13 §3.4).

Figures are made with :class:`matplotlib.figure.Figure` directly, not with ``pyplot``, so
no global figure list is kept (the GUI server runs many blocks in one process).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import matplotlib as mpl
from cycler import cycler
from matplotlib.figure import Figure

from apb.figures.size import CM_PER_INCH, DEFAULT_DPI, DEFAULT_HEIGHT_CM, DEFAULT_WIDTH_CM

#: Okabe-Ito colours: distinguishable for colour-blind readers and in grey print.
#: Yellow is last, it is weak on white paper.
COLORS: tuple[str, ...] = (
    "#0072B2",  # blue
    "#E69F00",  # orange
    "#009E73",  # bluish green
    "#D55E00",  # vermillion
    "#56B4E9",  # sky blue
    "#CC79A7",  # reddish purple
    "#000000",  # black
    "#F0E442",  # yellow
)

#: matplotlib settings of the house style.
HOUSE_RC: dict[str, Any] = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"],
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "legend.frameon": False,
    "axes.prop_cycle": cycler(color=list(COLORS)),
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.linewidth": 0.8,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": "#cccccc",
    "grid.linewidth": 0.5,
    "lines.linewidth": 1.5,
    "lines.markersize": 4,
    "patch.linewidth": 0.5,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
    "figure.constrained_layout.use": True,
}


@contextmanager
def house_style() -> Iterator[None]:
    """Apply the house style while figures are created and drawn into.

    matplotlib reads most settings when an artist is created, so the figure, its axes and
    what is drawn must be made inside the ``with`` block; saving can happen later.
    """
    with mpl.rc_context(HOUSE_RC):  # type: ignore[arg-type]
        yield


def new_figure(
    width_cm: float = DEFAULT_WIDTH_CM,
    height_cm: float = DEFAULT_HEIGHT_CM,
    dpi: int = DEFAULT_DPI,
) -> Figure:
    """Create a figure at its size in the report.

    Args:
        width_cm: Width in cm.
        height_cm: Height in cm.
        dpi: Resolution for the report image.

    Returns:
        A figure without axes, carrying ``apb_width_cm`` and ``apb_dpi``.

    Raises:
        ValueError: For a size or resolution that is not positive.
    """
    if width_cm <= 0 or height_cm <= 0 or dpi <= 0:
        raise ValueError(f"Figure size and dpi must be positive, got {width_cm} x {height_cm} cm")
    fig = Figure(figsize=(width_cm / CM_PER_INCH, height_cm / CM_PER_INCH), dpi=dpi)
    fig.apb_width_cm = float(width_cm)  # type: ignore[attr-defined]
    fig.apb_dpi = int(dpi)  # type: ignore[attr-defined]
    return fig
