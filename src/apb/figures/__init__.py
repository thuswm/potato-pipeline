"""Figures for reports: the house style and figures sized in cm (spec 13 §3.4).

Scripts can use the same look as the Graph block::

    from apb.figures import house_style, new_figure

    with house_style():
        fig = new_figure(16, 9)
        ax = fig.subplots()
        ax.plot(x, y)
"""

from typing import TYPE_CHECKING

from apb.figures.size import (
    DEFAULT_DPI,
    DEFAULT_HEIGHT_CM,
    DEFAULT_WIDTH_CM,
    figure_dpi,
    figure_width_cm,
)

if TYPE_CHECKING:
    from apb.figures.style import COLORS, house_style, new_figure

_STYLE_NAMES = ("COLORS", "house_style", "new_figure")


def __getattr__(name: str) -> object:
    """Import the style (and matplotlib) only when it is used."""
    if name in _STYLE_NAMES:
        from apb.figures import style

        return getattr(style, name)
    raise AttributeError(f"module 'apb.figures' has no attribute {name!r}")


__all__ = [
    "COLORS",
    "DEFAULT_DPI",
    "DEFAULT_HEIGHT_CM",
    "DEFAULT_WIDTH_CM",
    "figure_dpi",
    "figure_width_cm",
    "house_style",
    "new_figure",
]
