"""House style and figure size (spec 13 §3.4)."""

from __future__ import annotations

import io
import subprocess
import sys

import matplotlib as mpl
import pytest
from PIL import Image

from apb.figures import (
    COLORS,
    DEFAULT_DPI,
    figure_dpi,
    figure_width_cm,
    house_style,
    new_figure,
)


def test_new_figure_has_its_report_size():
    fig = new_figure(16, 9, dpi=100)
    assert figure_width_cm(fig) == 16.0 and figure_dpi(fig) == 100
    w, h = fig.get_size_inches()
    assert (round(w * 2.54, 6), round(h * 2.54, 6)) == (16.0, 9.0)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")  # no pyplot, no backend needed
    with Image.open(io.BytesIO(buffer.getvalue())) as image:
        assert image.size == (629, 354)  # 16 x 9 cm at 100 dpi (6.299 x 3.543 in)
    assert figure_dpi(new_figure()) == DEFAULT_DPI == 300


def test_new_figure_refuses_bad_sizes():
    with pytest.raises(ValueError, match="must be positive"):
        new_figure(0, 9)


def test_house_style_applies_only_inside_the_block():
    size_before = mpl.rcParams["font.size"]
    with house_style():
        fig = new_figure()
        ax = fig.subplots()
        (line,) = ax.plot([0, 1], [0, 1])
        assert mpl.rcParams["font.size"] == 9
        assert not ax.spines["top"].get_visible()
    assert line.get_color() == COLORS[0]
    assert mpl.rcParams["font.size"] == size_before


def test_size_metadata_of_other_objects():
    class Plain:
        pass

    assert figure_width_cm(Plain()) is None and figure_dpi(Plain()) is None


def test_reading_the_size_does_not_import_matplotlib():
    code = (
        "import sys, apb.figures.size, apb.blocks.report.writer; print('matplotlib' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
