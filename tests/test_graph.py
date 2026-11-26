"""Graph block (spec 13): every plot type, labels, series, reference lines, errors."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest

from apb.blocks.figures.graph import (
    HIGHLIGHT_COLOR,
    Graph,
    GraphError,
    GraphParams,
    draw_graph,
)
from apb.blocks.table.common import TableError
from apb.engine import RunContext, default_registry
from apb.figures import COLORS, figure_dpi, figure_width_cm
from apb.units import Quantity, UnitSystem, set_units


def margins() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "EID": [1001, 1002, 1003, 1001, 1002, 1003],
            "SUBCASE": [1, 1, 1, 2, 2, 2],
            "TENSION": [100.0, 250.0, 80.0, 120.0, 300.0, 60.0],
            "SHEAR": [50.0, 70.0, 40.0, 55.0, 90.0, 20.0],
            "MS": [0.5, -0.1, 1.2, 0.3, -0.2, 2.0],
        }
    )
    set_units(df, UnitSystem.SI_MM, {"TENSION": "N", "SHEAR": "N", "MS": "-"})
    return df


def modes() -> pd.DataFrame:
    df = pd.DataFrame(
        {"MODE": [3, 1, 2], "FREQUENCY": [40.0, 12.5, 25.7], "MASS": [0.002, 0.01, 0.005]}
    )
    set_units(df, UnitSystem.SI_MM, {"FREQUENCY": "Hz", "MASS": "t"})
    return df


def draw(table: pd.DataFrame, **params) -> object:
    refs = params.pop("refs", None)
    return draw_graph(table, GraphParams.model_validate(params), refs)


def png(fig) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    return buffer.getvalue()


@pytest.mark.parametrize(
    "kind", ["line", "scatter", "bar", "barh", "step", "area", "histogram", "box"]
)
def test_every_kind_draws_a_figure_of_the_set_size(kind: str):
    params = {"kind": kind, "x": "EID", "y": ["TENSION"], "series_by": "SUBCASE"}
    fig = draw(margins(), **params, width_cm=12, height_cm=6, dpi=100)
    assert figure_width_cm(fig) == 12 and figure_dpi(fig) == 100
    assert png(fig).startswith(b"\x89PNG")
    ax = fig.axes[0]
    assert ax.get_xlabel() or ax.get_ylabel()


def test_line_defaults_labels_from_units_and_sorts_x():
    fig = draw(modes(), kind="line", x="MODE", y=["FREQUENCY"])
    ax = fig.axes[0]
    assert (ax.get_xlabel(), ax.get_ylabel()) == ("MODE", "FREQUENCY [Hz]")
    (line,) = ax.get_lines()
    assert list(line.get_xdata()) == [1, 2, 3]  # sorted by X
    assert list(line.get_ydata()) == [12.5, 25.7, 40.0]
    assert line.get_marker() == "o"  # few points: markers
    assert line.get_color() == COLORS[0]
    assert ax.get_legend() is None  # one series
    fig.canvas.draw()
    assert all(float(t).is_integer() for t in ax.get_xticks())  # MODE: whole numbers


def test_defaults_without_x_and_y():
    ax = draw(modes()).axes[0]
    assert ax.get_xlabel() == "MODE" and ax.get_ylabel() == "FREQUENCY [Hz]"


def test_mass_in_kg_by_default_and_off():
    assert draw(modes(), x="MODE", y=["MASS"]).axes[0].get_ylabel() == "MASS [kg]"
    kg = draw(modes(), x="MODE", y=["MASS"], sort_x=False).axes[0].get_lines()[0].get_ydata()
    assert list(kg) == [2.0, 10.0, 5.0]
    off = draw(modes(), x="MODE", y=["MASS"], mass_in_kg=False).axes[0]
    assert off.get_ylabel() == "MASS [t]"


def test_series_by_gives_one_series_per_value_with_legend():
    ax = draw(margins(), kind="line", x="EID", y=["TENSION"], series_by="SUBCASE").axes[0]
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["1", "2"]
    two = draw(margins(), x="EID", y=["TENSION", "SHEAR"], series_by="SUBCASE").axes[0]
    texts = [t.get_text() for t in two.get_legend().get_texts()]
    assert texts == ["1 - TENSION", "1 - SHEAR", "2 - TENSION", "2 - SHEAR"]
    assert two.get_ylabel() == "TENSION, SHEAR [N]"


def test_grouped_and_stacked_bars():
    grouped = draw(margins(), kind="bar", x="EID", y=["TENSION"], series_by="SUBCASE").axes[0]
    bars = grouped.patches
    assert len(bars) == 6
    assert bars[0].get_x() != bars[3].get_x()  # side by side
    assert [t.get_text() for t in grouped.get_xticklabels()] == ["1001", "1002", "1003"]
    stacked = draw(
        margins(), kind="bar", x="EID", y=["TENSION"], series_by="SUBCASE", stacked=True
    ).axes[0]
    first, second = stacked.patches[0], stacked.patches[3]
    assert first.get_x() == second.get_x() and second.get_y() == 100.0  # on top of the first


def test_horizontal_bars_put_values_on_x():
    ax = draw(margins().head(3), kind="barh", x="EID", y=["MS"]).axes[0]
    assert ax.get_xlabel() == "MS" and ax.get_ylabel() == "EID"
    assert [round(p.get_width(), 3) for p in ax.patches] == [0.5, -0.1, 1.2]


def test_repeated_categories_without_series_are_refused():
    with pytest.raises(GraphError, match=r"repeated values.*Series by"):
        draw(margins(), kind="bar", x="EID", y=["TENSION"])


def test_histogram_and_box():
    hist = draw(margins(), kind="histogram", y=["TENSION"], bins=4).axes[0]
    assert hist.get_xlabel() == "TENSION [N]" and hist.get_ylabel() == "Count"
    assert len(hist.patches) == 4
    box = draw(margins(), kind="box", y=["TENSION"], series_by="SUBCASE").axes[0]
    assert [t.get_text() for t in box.get_xticklabels()] == ["1", "2"]


def test_log_axis_needs_positive_values():
    with pytest.raises(GraphError, match=r"log y axis needs values > 0.*'MS' has -0.1 in row 1"):
        draw(margins().head(3), kind="bar", x="EID", y=["MS"], y_scale="log")
    ax = draw(modes(), x="MODE", y=["FREQUENCY"], y_scale="log").axes[0]
    assert ax.get_yscale() == "log"


def test_reference_lines_typed_and_connected():
    fig = draw(
        margins().head(3),
        kind="bar",
        x="EID",
        y=["MS"],
        reference_lines=[
            {"value": 0, "label": "MS = 0"},
            {"value": "limit", "label": "limit", "style": "solid"},
        ],
        refs={"limit": Quantity(0.25, "-")},
    )
    ax = fig.axes[0]
    ys = sorted(line.get_ydata()[0] for line in ax.get_lines())
    assert ys == [0.0, 0.25]
    assert {t.get_text() for t in ax.texts} == {"MS = 0", "limit"}
    with pytest.raises(GraphError, match=r"'other' is not connected\. Connected: none"):
        draw(margins().head(3), kind="bar", x="EID", y=["MS"], reference_lines=[{"value": "other"}])


def test_bar_labels_and_highlight_below():
    ax = draw(
        margins().head(3), kind="bar", x="EID", y=["MS"], bar_labels="0.00", highlight_below=0
    ).axes[0]
    assert [t.get_text() for t in ax.texts] == ["0.50", "-0.10", "1.20"]
    colors = [p.get_facecolor() for p in ax.patches]
    from matplotlib.colors import to_rgba

    assert colors[1] == to_rgba(HIGHLIGHT_COLOR) and colors[0] != colors[1]
    with pytest.raises(GraphError, match="Bar labels"):
        draw(margins().head(3), kind="bar", x="EID", y=["MS"], bar_labels="0.0.0")


def test_limits_title_legend_below_and_labels_set():
    fig = draw(
        margins(),
        x="EID",
        y=["TENSION"],
        series_by="SUBCASE",
        title="Tension",
        x_label="Fastener",
        y_label="F",
        legend="below",
        y_min=0,
        y_max=500,
    )
    ax = fig.axes[0]
    assert ax.get_title() == "Tension" and ax.get_xlabel() == "Fastener" and ax.get_ylabel() == "F"
    assert ax.get_ylim() == (0.0, 500.0)
    assert fig.legends and ax.get_legend() is None


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"y": ["NOPE"]}, r"Column\(s\) \['NOPE'\] not found"),
        ({"x": "SUBCASE", "y": ["TENSION"], "series_by": "EID"}, "24 series are too many"),
    ],
)
def test_clear_errors(params, message):
    many = pd.DataFrame({"EID": range(21), "SUBCASE": 1, "TENSION": 1.0})
    with pytest.raises(TableError, match=message):  # GraphError is a TableError
        draw(pd.concat([margins(), many]) if "many" in message else margins(), **params)


def test_text_y_column_and_empty_table_are_refused():
    df = margins().assign(NAME="a")
    with pytest.raises(GraphError, match="'NAME' does not hold numbers"):
        draw(df, y=["NAME"])
    with pytest.raises(GraphError, match="empty"):
        draw(margins().iloc[0:0])


def test_block_is_registered_and_runs_with_connected_references(tmp_path: Path):
    assert default_registry().get("figure.graph") is Graph
    ctx = RunContext(
        block_id="g",
        pipeline_dir=tmp_path,
        output_dir=tmp_path,
        input_names={"reference": ["limit"]},
    )
    params = GraphParams(
        kind="bar", x="EID", y=["MS"], reference_lines=[{"value": "limit", "label": "L"}]
    )
    out = Graph().run(params, {"table": margins().head(3), "reference": [0.2]}, ctx)
    assert png(out["figure"]).startswith(b"\x89PNG")
