"""The side panel summary of the Word report (spec 11 §4.4)."""

from __future__ import annotations

from apb.blocks.report import editing as ed
from apb.engine.types import PortType
from apb.gui.report_editor import problem_lines, summary_text


def _info(name: str, kind: PortType = PortType.TABLE) -> ed.ItemInfo:
    return ed.ItemInfo(name=name, type=kind, source="x")


def test_summary_text():
    assert summary_text([], {}) == "0 sections · 0 items"
    assert summary_text([{"kind": "title"}], {"a": _info("a")}) == "1 section · 1 item"


def test_problem_lines_list_sections_and_unused_items():
    sections = [{"kind": "table", "item": "loads"}, {"kind": "table"}]
    infos = {"loads": _info("loads"), "extra": _info("extra")}
    lines = problem_lines(sections, infos)
    assert any(line.startswith("Section 2:") for line in lines)
    assert "Item 'extra' is used nowhere" in lines
    assert not any("loads" in line and "nowhere" in line for line in lines)
