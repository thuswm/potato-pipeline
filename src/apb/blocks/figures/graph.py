"""Graph block: a plot from a table, for the report (spec 13).

:func:`draw_graph` does the drawing and can be used and tested without the engine; the
block :class:`Graph` wraps it. Figures use the house style and are made at their size in
the report (:mod:`apb.figures`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from apb.blocks.report.sections import FormatError, format_value, python_format
from apb.blocks.table.common import TableError, require_columns
from apb.engine.block import Block, RunContext
from apb.engine.types import Port, PortType
from apb.figures.size import DEFAULT_DPI, DEFAULT_HEIGHT_CM, DEFAULT_WIDTH_CM
from apb.units import Quantity, get_units, header_with_unit, mass_in_kg

Kind = Literal["line", "scatter", "bar", "barh", "step", "area", "histogram", "box"]

KIND_LABELS: dict[str, str] = {
    "line": "Line",
    "scatter": "Scatter",
    "bar": "Bar",
    "barh": "Horizontal bar",
    "step": "Step",
    "area": "Area",
    "histogram": "Histogram",
    "box": "Box",
}

#: Plot types with categories along one axis and values along the other.
BAR_KINDS = frozenset({"bar", "barh"})
#: Plot types without an X column.
NO_X_KINDS = frozenset({"histogram", "box"})
#: Plot types that can stack their series.
STACK_KINDS = frozenset({"bar", "barh", "area"})

MAX_SERIES = 20
"""More series than this make a plot unreadable (spec 13 §3.3)."""

HIGHLIGHT_COLOR = "#D55E00"
"""Bars below the limit (vermillion of the house colours)."""


class GraphError(TableError):
    """Raised for a graph that cannot be drawn from the table and parameters."""


class ReferenceLine(BaseModel):
    """A horizontal or vertical line with a label (GR-07)."""

    model_config = ConfigDict(extra="forbid")

    axis: Literal["x", "y"] = Field(
        default="y", description="y: horizontal line at a y value; x: vertical line"
    )
    value: float | str = Field(
        description="A number, or the name of a value connected to the 'reference' input"
    )
    label: str = Field(default="", description="Text at the line, e.g. MS = 0")
    style: Literal["dashed", "solid", "dotted"] = "dashed"


class GraphParams(BaseModel):
    """Parameters of :class:`Graph` (spec 13 §3.2)."""

    model_config = ConfigDict(extra="forbid")

    kind: Kind = Field(
        default="line",
        title="Plot type",
        json_schema_extra={"apb_labels": dict(KIND_LABELS)},
    )
    x: str = Field(
        default="",
        title="X column",
        description="Values or categories along the x axis; empty = first column",
        json_schema_extra={"apb_columns": "table"},
    )
    y: list[str] = Field(
        default_factory=list,
        title="Y columns",
        description="The plotted values; empty = first number column after X",
        json_schema_extra={"apb_columns": "table"},
    )
    series_by: str = Field(
        default="",
        title="Series by",
        description="One series per value of this column, e.g. SUBCASE; empty = none",
        json_schema_extra={"apb_columns": "table"},
    )
    stacked: bool = Field(default=False, description="Stack the series (bar, area)")
    bins: int | None = Field(
        default=None, ge=1, le=500, description="Number of bins (histogram); empty = automatic"
    )
    markers: Literal["auto", "on", "off"] = Field(
        default="auto", description="Point markers on lines; auto = on up to 30 points"
    )
    sort_x: bool = Field(default=True, title="Sort by X", description="Line, step, area")
    title: str = Field(default="", description="Usually empty: the report caption is the title")
    x_label: str = Field(
        default="", title="X axis label", description="Horizontal axis; empty = column [unit]"
    )
    y_label: str = Field(
        default="", title="Y axis label", description="Vertical axis; empty = column [unit]"
    )
    legend: Literal["auto", "off", "inside", "below"] = Field(
        default="auto", description="auto: shown when there is more than one series"
    )
    x_scale: Literal["linear", "log"] = "linear"
    y_scale: Literal["linear", "log"] = "linear"
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    grid: bool = True
    reference_lines: list[ReferenceLine] = Field(default_factory=list)
    bar_labels: str = Field(
        default="",
        description="Write the value on each bar with this number format, e.g. 0.00; empty = off",
    )
    highlight_below: float | None = Field(
        default=None, description="Bars with a value below this are drawn in red"
    )
    width_cm: float = Field(default=DEFAULT_WIDTH_CM, gt=0, le=30, title="Width [cm]")
    height_cm: float = Field(default=DEFAULT_HEIGHT_CM, gt=0, le=30, title="Height [cm]")
    dpi: int = Field(default=DEFAULT_DPI, ge=50, le=600, title="DPI")
    mass_in_kg: bool = Field(
        default=True, title="Mass in kg", description="Plot mass in kg instead of t (tonne)"
    )


# --------------------------------------------------------------------------------------
# Data preparation
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Series:
    """One plotted series.

    Attributes:
        label: Legend text.
        x: X values (empty for histogram and box).
        y: Values.
    """

    label: str
    x: pd.Series
    y: pd.Series


def _columns(df: pd.DataFrame, params: GraphParams) -> tuple[str | None, list[str]]:
    """Return the X column (``None`` for histogram/box) and the Y columns."""
    x: str | None = None
    if params.kind not in NO_X_KINDS:
        x = params.x or str(df.columns[0])
    if params.y:
        ys = list(params.y)
    else:
        numeric = [
            str(c)
            for c in df.columns
            if c != x and c != params.series_by and pd.api.types.is_numeric_dtype(df[c])
        ]
        if not numeric:
            raise GraphError("The table has no number column to plot; choose the Y columns")
        ys = numeric[:1]
    wanted = [c for c in (x, params.series_by, *ys) if c]
    require_columns(df, wanted)
    for column in ys:
        if not pd.api.types.is_numeric_dtype(df[column]):
            raise GraphError(f"Y column {column!r} does not hold numbers")
    return x, ys


def _check_positive(df: pd.DataFrame, columns: Sequence[str], axis: str) -> None:
    for column in columns:
        values = df[column]
        if not pd.api.types.is_numeric_dtype(values):
            raise GraphError(f"A log {axis} axis needs numbers; column {column!r} has text")
        bad = values[values <= 0]
        if not bad.empty:
            raise GraphError(
                f"A log {axis} axis needs values > 0; column {column!r} has "
                f"{bad.iloc[0]:g} in row {bad.index[0]}"
            )


def build_series(df: pd.DataFrame, params: GraphParams) -> list[Series]:
    """Split the table into the plotted series (spec 13 §3.3).

    Raises:
        GraphError: For unknown or unsuitable columns, or too many series.
    """
    x, ys = _columns(df, params)
    if params.kind in BAR_KINDS and x is not None and not params.series_by:
        repeated = df[x][df[x].duplicated()]
        if not repeated.empty:
            raise GraphError(
                f"X column {x!r} has repeated values (e.g. {repeated.iloc[0]}): the bars would "
                "lie on top of each other. Set 'Series by' (e.g. SUBCASE) or filter the rows."
            )
    groups: list[tuple[str, pd.DataFrame]]
    if params.series_by:
        groups = [(str(k), g) for k, g in df.groupby(params.series_by, sort=False)]
    else:
        groups = [("", df)]
    if len(groups) * len(ys) > MAX_SERIES:
        raise GraphError(
            f"{len(groups) * len(ys)} series are too many for one plot (at most {MAX_SERIES}); "
            "filter the rows or choose fewer Y columns"
        )
    sort = params.sort_x and params.kind in {"line", "step", "area"} and x is not None
    out: list[Series] = []
    for name, group in groups:
        if sort and x is not None and pd.api.types.is_numeric_dtype(group[x]):
            group = group.sort_values(x, kind="stable")
        for column in ys:
            label = f"{name} - {column}" if name and len(ys) > 1 else name or column
            xs = group[x] if x is not None else pd.Series(dtype=float)
            out.append(Series(label, xs, group[column]))
    return out


def _value_label(df: pd.DataFrame, ys: Sequence[str]) -> str:
    units = get_units(df)
    if len(ys) == 1:
        return header_with_unit(ys[0], units)
    found = {units.get(c, "") for c in ys}
    unit = found.pop() if len(found) == 1 else ""
    names = ", ".join(ys) if len(ys) <= 3 else "Value"
    return f"{names} [{unit}]" if unit and unit != "-" else names


# --------------------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------------------


def _reference_value(value: float | str, references: Mapping[str, Any]) -> float:
    if isinstance(value, int | float):
        return float(value)
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    if text not in references:
        known = ", ".join(sorted(references)) or "none connected"
        raise GraphError(f"Reference value {text!r} is not connected. Connected: {known}")
    found = references[text]
    if isinstance(found, Quantity):
        return float(found.value)
    if isinstance(found, int | float) and not isinstance(found, bool):
        return float(found)
    raise GraphError(f"Reference value {text!r} is not a number")


def _categories(series: Sequence[Series]) -> list[Any]:
    seen: dict[Any, None] = {}
    for s in series:
        for value in s.x.tolist():
            seen.setdefault(value, None)
    return list(seen)


def _draw_bars(ax: Any, series: Sequence[Series], params: GraphParams) -> list[Any]:
    """Grouped or stacked bars; returns the bar containers."""
    horizontal = params.kind == "barh"
    categories = _categories(series)
    positions = np.arange(len(categories), dtype=float)
    containers = []
    n = len(series)
    width = 0.8 if params.stacked or n == 1 else 0.8 / n
    bottom_pos = np.zeros(len(categories))
    bottom_neg = np.zeros(len(categories))
    for i, s in enumerate(series):
        values = (
            pd.Series(s.y.to_numpy(), index=s.x.to_numpy())
            .groupby(level=0, sort=False)
            .sum()
            .reindex(categories)
            .to_numpy(dtype=float)
        )
        if params.stacked:
            filled = np.nan_to_num(values)
            base = np.where(filled >= 0, bottom_pos, bottom_neg)
            offset = positions
            bottom_pos = bottom_pos + np.where(filled >= 0, filled, 0)
            bottom_neg = bottom_neg + np.where(filled < 0, filled, 0)
            values = filled
        else:
            base = np.zeros(len(categories))
            offset = positions + (i - (n - 1) / 2) * width
        if horizontal:
            bars = ax.barh(offset, values, height=width, left=base, label=s.label)
        else:
            bars = ax.bar(offset, values, width=width, bottom=base, label=s.label)
        containers.append(bars)
        if params.highlight_below is not None:
            for patch, value in zip(bars.patches, values, strict=True):
                if np.isfinite(value) and value < params.highlight_below:
                    patch.set_facecolor(HIGHLIGHT_COLOR)
    labels = [str(c) for c in categories]
    if horizontal:
        ax.set_yticks(positions, labels)
        ax.invert_yaxis()  # first category on top, as in the table
    else:
        ax.set_xticks(positions, labels)
        if len(labels) > 8 and max(len(t) for t in labels) > 3:
            ax.tick_params(axis="x", labelrotation=45)
            for tick in ax.get_xticklabels():
                tick.set_horizontalalignment("right")
    return containers


def _draw(ax: Any, series: Sequence[Series], params: GraphParams) -> list[Any]:
    """Draw the series on ``ax``; returns bar containers (for bar labels)."""
    kind = params.kind
    if kind in BAR_KINDS:
        return _draw_bars(ax, series, params)
    if kind == "histogram":
        ax.hist(
            [s.y.dropna().to_numpy() for s in series],
            bins=params.bins or "auto",
            stacked=params.stacked,
            label=[s.label for s in series],
        )
        return []
    if kind == "box":
        ax.boxplot([s.y.dropna().to_numpy() for s in series], tick_labels=[s.label for s in series])
        return []
    if kind == "area" and params.stacked:
        categories = _categories(series)
        stack = [
            pd.Series(s.y.to_numpy(), index=s.x.to_numpy())
            .groupby(level=0, sort=False)
            .sum()
            .reindex(categories)
            .fillna(0.0)
            .to_numpy()
            for s in series
        ]
        ax.stackplot(categories, *stack, labels=[s.label for s in series], alpha=0.85)
        return []
    for s in series:
        show_markers = params.markers == "on" or (params.markers == "auto" and len(s.y) <= 30)
        marker = "o" if show_markers else None
        if kind == "line":
            ax.plot(s.x, s.y, marker=marker, label=s.label)
        elif kind == "step":
            ax.step(s.x, s.y, where="mid", marker=marker, label=s.label)
        elif kind == "scatter":
            ax.scatter(s.x, s.y, label=s.label, s=14)
        elif kind == "area":
            (line,) = ax.plot(s.x, s.y, label=s.label)
            ax.fill_between(s.x, s.y, alpha=0.25, color=line.get_color())
    return []


def _reference_lines(ax: Any, params: GraphParams, references: Mapping[str, Any]) -> None:
    for ref in params.reference_lines:
        value = _reference_value(ref.value, references)
        style = {"dashed": "--", "solid": "-", "dotted": ":"}[ref.style]
        if ref.axis == "y":
            ax.axhline(value, color="#333333", linestyle=style, linewidth=1.0)
            if ref.label:
                ax.annotate(
                    ref.label,
                    (1.0, value),
                    xycoords=("axes fraction", "data"),
                    xytext=(-2, 2),
                    textcoords="offset points",
                    ha="right",
                    va="bottom",
                    fontsize="small",
                )
        else:
            ax.axvline(value, color="#333333", linestyle=style, linewidth=1.0)
            if ref.label:
                ax.annotate(
                    ref.label,
                    (value, 1.0),
                    xycoords=("data", "axes fraction"),
                    xytext=(2, -2),
                    textcoords="offset points",
                    ha="left",
                    va="top",
                    fontsize="small",
                )


def draw_graph(
    table: pd.DataFrame, params: GraphParams, references: Mapping[str, Any] | None = None
) -> Any:
    """Draw the graph of a table.

    Args:
        table: The data (not modified).
        params: Graph parameters.
        references: Connected scalar values by name, for reference lines.

    Returns:
        A matplotlib figure made at ``width_cm x height_cm`` in the house style.

    Raises:
        GraphError: If the graph cannot be drawn (unknown columns, text where numbers are
            needed, values <= 0 on a log axis, too many series, ...).
    """
    from apb.figures.style import house_style, new_figure

    if isinstance(table.columns, pd.MultiIndex):
        raise GraphError(
            "The table has two-level column headers; use the flat layout of the load case "
            "table, or select single columns first"
        )
    if table.empty:
        raise GraphError("The table is empty: nothing to plot")
    if params.bar_labels:
        try:
            python_format(params.bar_labels)
        except FormatError as exc:
            raise GraphError(f"Bar labels: {exc}") from exc
    df = mass_in_kg(table) if params.mass_in_kg else table
    x, ys = _columns(df, params)
    value_axis = "x" if params.kind == "barh" else "y"
    value_scale = params.x_scale if params.kind == "barh" else params.y_scale
    if value_scale == "log":
        _check_positive(df, ys, value_axis)
    category_scale = params.y_scale if params.kind == "barh" else params.x_scale
    if category_scale == "log" and x is not None and params.kind not in BAR_KINDS:
        _check_positive(df, [x], "x")
    series = build_series(df, params)
    units = get_units(df)

    with house_style():
        fig = new_figure(params.width_cm, params.height_cm, params.dpi)
        ax = fig.subplots()
        containers = _draw(ax, series, params)

        # Axis labels: the value axis is horizontal for horizontal bars.
        value_label = _value_label(df, ys)
        if params.kind == "histogram":
            auto_x, auto_y = value_label, "Count"
        elif params.kind == "box":
            auto_x, auto_y = (params.series_by or ""), value_label
        elif params.kind == "barh":
            auto_x, auto_y = value_label, header_with_unit(str(x), units)
        else:
            auto_x, auto_y = header_with_unit(str(x), units), value_label
        ax.set_xlabel(params.x_label or auto_x)
        ax.set_ylabel(params.y_label or auto_y)
        if params.title:
            ax.set_title(params.title)

        # Only log is set: setting "linear" again would reset category tick labels.
        if params.x_scale == "log" and params.kind not in ("bar", "box"):
            ax.set_xscale("log")
        if params.y_scale == "log" and params.kind != "barh":
            ax.set_yscale("log")
        # Whole-number data (MODE, SUBCASE, counts) gets whole-number ticks.
        from matplotlib.ticker import MaxNLocator

        whole_x = x is not None and pd.api.types.is_integer_dtype(df[x])
        if whole_x and params.kind not in BAR_KINDS and params.x_scale == "linear":
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        if params.kind == "histogram":
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.set_xlim(params.x_min, params.x_max)
        ax.set_ylim(params.y_min, params.y_max)
        if not params.grid:
            ax.grid(False)
        elif params.kind in BAR_KINDS:
            ax.grid(False, axis="y" if params.kind == "barh" else "x")

        if params.bar_labels:
            for bars in containers:
                values = [
                    p.get_width() if params.kind == "barh" else p.get_height() for p in bars.patches
                ]
                ax.bar_label(
                    bars,
                    labels=[format_value(float(v), params.bar_labels) for v in values],
                    fontsize="x-small",
                    padding=1,
                )
        _reference_lines(ax, params, references or {})

        show_legend = params.legend in ("inside", "below") or (
            params.legend == "auto" and len(series) > 1 and params.kind != "box"
        )
        if show_legend:
            if params.legend == "below":
                fig.legend(loc="outside lower center", ncols=min(len(series), 4))
            else:
                ax.legend(loc="best")
    return fig


# --------------------------------------------------------------------------------------
# Block
# --------------------------------------------------------------------------------------


class Graph(Block[GraphParams]):
    """A plot of a table: line, scatter, bar, histogram, box ... (spec 13)."""

    type_id = "figure.graph"
    category = "Figures"
    title = "Graph"
    description = (
        "A plot of a table for the report: line, scatter, bar, horizontal bar, step, area, "
        "histogram or box; one series per column or per value of a column."
    )
    inputs = (
        Port("table", PortType.TABLE, label="Table"),
        Port("reference", PortType.SCALAR, label="Reference", optional=True, multiple=True),
    )
    outputs = (Port("figure", PortType.FIGURE, label="Figure"),)
    Params = GraphParams
    advanced_params = (
        "sort_x",
        "markers",
        "legend",
        "x_min",
        "x_max",
        "y_min",
        "y_max",
        "grid",
        "width_cm",
        "height_cm",
        "dpi",
        "mass_in_kg",
    )

    def run(self, params: GraphParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Draw the graph."""
        values = list(inputs.get("reference") or [])
        names = ctx.input_names.get("reference", [])
        references = dict(zip(names, values, strict=False))
        fig = draw_graph(inputs["table"], params, references)
        ctx.log(
            f"{KIND_LABELS[params.kind]} plot of {len(inputs['table'])} rows, "
            f"{params.width_cm:g} x {params.height_cm:g} cm"
        )
        return {"figure": fig}


BLOCKS: tuple[type[Block[Any]], ...] = (Graph,)
