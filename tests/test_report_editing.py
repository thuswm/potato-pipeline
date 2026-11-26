"""Pure editing functions of the report sections (spec 05 §3.4)."""

from __future__ import annotations

import copy

import pandas as pd
import pytest

from apb.blocks.report import editing as ed
from apb.blocks.report.block import WordReport
from apb.blocks.report.writer import ReportError
from apb.engine import RunContext
from apb.engine.types import PortType
from apb.units import Quantity

H = {"kind": "heading", "text": "Intro", "level": 2}
P = {"kind": "paragraph", "text": "The minimum is {min_ms:.2f}."}
T = {"kind": "table", "item": "margins", "caption": "Margins {min_ms}", "columns": ["EID", "MS"]}


def items(**extra) -> dict[str, ed.ItemInfo]:
    base = {
        "margins": ed.ItemInfo("margins", PortType.TABLE, columns=("EID", "SUBCASE", "MS")),
        "min_ms": ed.ItemInfo("min_ms", PortType.SCALAR, value=0.4667),
        "plot": ed.ItemInfo("plot", PortType.FIGURE),
        "note": ed.ItemInfo("note", PortType.TEXT, value="PASS"),
    }
    return base | extra


# ---- list operations --------------------------------------------------------------------


def test_new_section_defaults():
    assert ed.new_section("heading") == {"kind": "heading", "text": "New heading", "level": 1}
    assert ed.new_section("table") == {"kind": "table", "item": ""}
    assert ed.new_section("page_break") == {"kind": "page_break"}
    assert [k for k, _ in ed.SECTION_KINDS][:3] == ["title", "heading", "paragraph"]
    with pytest.raises(ValueError, match="Unknown section kind"):
        ed.new_section("chart")
    first, second = ed.new_section("heading"), ed.new_section("heading")
    first["text"] = "changed"
    assert second["text"] == "New heading"  # no shared state


def test_insert_move_duplicate_delete_do_not_modify_the_input():
    original = [H, P, T]
    snapshot = copy.deepcopy(original)
    assert [s["kind"] for s in ed.insert(original, 1, {"kind": "units"})] == [
        "heading",
        "units",
        "paragraph",
        "table",
    ]
    assert [s["kind"] for s in ed.insert(original, 99, {"kind": "units"})][-1] == "units"
    assert [s["kind"] for s in ed.insert(original, -5, {"kind": "units"})][:1] == ["units"]
    assert [s["kind"] for s in ed.move(original, 2, -1)] == ["heading", "table", "paragraph"]
    assert [s["kind"] for s in ed.move(original, 0, 1)] == ["paragraph", "heading", "table"]
    assert [s["kind"] for s in ed.move(original, 0, -1)] == ["heading", "paragraph", "table"]
    assert [s["kind"] for s in ed.move(original, 2, 5)] == ["heading", "paragraph", "table"]
    dup = ed.duplicate(original, 1)
    assert dup[1] == dup[2] == P
    dup[2]["text"] = "other"
    assert dup[1]["text"] == P["text"]  # a real copy
    assert [s["kind"] for s in ed.delete(original, 1)] == ["heading", "table"]
    assert original == snapshot
    for call in (ed.move, ed.duplicate, ed.delete):
        with pytest.raises(IndexError):
            call(original, 3, *([1] if call is ed.move else []))


def test_describe():
    assert ed.describe(H) == "H2 · Intro"
    assert ed.describe({"kind": "paragraph", "text": "a\nb"}) == "a b"
    assert ed.describe({"kind": "paragraph", "text": ""}) == "(empty)"
    assert ed.describe(T) == "Table · margins · “Margins {min_ms}”"
    assert ed.describe({"kind": "table", "item": ""}) == "Table · (no item)"
    assert ed.describe({"kind": "figure", "item": "plot"}) == "Figure · plot"
    assert ed.describe({"kind": "value", "item": "min_ms", "label": "Minimum"}) == (
        "Value · min_ms · Minimum"
    )
    assert ed.describe({"kind": "units"}) == "Units statement"
    assert ed.describe({"kind": "traceability", "heading": "Sources"}) == "Sources"
    assert ed.describe({"kind": "paragraph", "text": "x" * 200}, limit=20).endswith("…")
    assert len(ed.describe({"kind": "paragraph", "text": "x" * 200}, limit=20)) == 20


# ---- references -------------------------------------------------------------------------


def test_referenced_names_and_counts():
    assert ed.referenced_names([H, P, T]) == {"min_ms", "margins"}
    assert ed.count_references([H, P, T], "min_ms") == 2  # one in the text, one in the caption
    assert ed.count_references([H, P, T], "margins") == 1
    assert ed.count_references([H, P, T], "nothing") == 0
    assert ed.placeholder_names("{a} {b:.2f} {{literal}} {c!r}") == ["a", "b", "c"]
    assert ed.placeholder_names("broken {") == []


def test_rename_item_references():
    out = ed.rename_item_references([H, P, T], "min_ms", "min_margin")
    assert out[1]["text"] == "The minimum is {min_margin:.2f}."
    assert out[2]["caption"] == "Margins {min_margin}"
    out = ed.rename_item_references([H, P, T], "margins", "global_margins")
    assert out[2]["item"] == "global_margins"
    assert out[2]["caption"] == "Margins {min_ms}"
    # Escaped braces, longer names and other fields stay as they are.
    other = {"kind": "paragraph", "text": "{{min_ms}} {min_ms_2} {min_ms!s}"}
    assert ed.rename_item_references([other], "min_ms", "x")[0]["text"] == (
        "{{min_ms}} {min_ms_2} {x!s}"
    )
    assert ed.rename_item_references([H], "Intro", "x") == [H]  # only item fields and texts


def test_rename_items_all_at_once_so_names_can_swap():
    sections = [
        {"kind": "table", "item": "margins"},
        {"kind": "table", "item": "margins_2", "caption": "{margins} and {margins_2:.1f}"},
        {"kind": "paragraph", "text": "{margins_20} {{margins}}"},
    ]
    out = ed.rename_items(sections, {"margins": "margins_2", "margins_2": "margins"})
    assert [s.get("item") for s in out[:2]] == ["margins_2", "margins"]
    assert out[1]["caption"] == "{margins_2} and {margins:.1f}"
    assert out[2]["text"] == "{margins_20} {{margins}}"  # other names and escaped braces
    assert ed.rename_items(sections, {}) == sections
    assert sections[0]["item"] == "margins"  # the input is not modified


def test_fill_preview():
    meta = {"title": "Report"}
    assert ed.fill_preview("{title}: {min_ms:.2f}, {note}", items(), meta) == "Report: 0.47, PASS"
    assert ed.fill_preview("{margins}", items(), meta) is None  # not a value
    assert ed.fill_preview("{unknown}", items(), meta) is None
    assert ed.fill_preview("{x}", {"x": ed.ItemInfo("x", PortType.SCALAR)}, {}) is None  # no value
    unit = {"x": ed.ItemInfo("x", PortType.SCALAR, value=Quantity(25.7, "Hz"))}
    assert ed.fill_preview("{x:.1f}", unit, {}) == "25.7 Hz"


# ---- checking ---------------------------------------------------------------------------


def messages(sections, **extra):
    return [(p.index, p.message) for p in ed.check_sections(sections, items(**extra))]


def test_valid_sections_have_no_problems():
    sections = [
        {"kind": "title"},
        H,
        P,
        T,
        {"kind": "figure", "item": "plot"},
        {"kind": "value", "item": "min_ms"},
        {"kind": "value", "item": "note"},
        {"kind": "units"},
        {"kind": "traceability"},
        {"kind": "paragraph", "text": "{title} rev {revision}: {{braces}}"},
    ]
    assert messages(sections) == []


def test_item_problems():
    assert messages([{"kind": "table", "item": ""}]) == [(0, "Choose an item")]
    assert messages([{"kind": "table", "item": "nope"}]) == [
        (0, "No connected item 'nope'. Available: margins, min_ms, note, plot")
    ]
    assert messages([{"kind": "figure", "item": "margins"}]) == [
        (0, "Item 'margins' is a Table, not a Figure")
    ]
    assert messages([{"kind": "value", "item": "margins"}]) == [
        (0, "Item 'margins' is a Table, not a Scalar or Text")
    ]
    assert messages([{"kind": "table", "item": "x"}], x=ed.ItemInfo("x", PortType.ANY)) == []


def test_column_problems_only_when_the_columns_are_known():
    bad = {"kind": "table", "item": "margins", "columns": ["EID", "NOPE"], "formats": {"X": "0.0"}}
    found = messages([bad])
    assert found[0][1].startswith(
        "Column(s) ['NOPE'] not in item 'margins'. Available: EID, SUBCASE"
    )
    assert found[1][1] == "Format given for column(s) ['X'] that are not in item 'margins'"
    unknown = {"margins": ed.ItemInfo("margins", PortType.TABLE)}  # never run: columns unknown
    assert ed.check_sections([bad], unknown) == []


def test_placeholder_problems():
    assert messages([{"kind": "paragraph", "text": "{nope}"}]) == [
        (
            0,
            "Unknown placeholder {nope}. Available: author, date, document_number, min_ms, "
            "note, revision, title",
        )
    ]
    assert messages([{"kind": "paragraph", "text": "{margins}"}]) == [
        (0, "Placeholder {margins} is a Table, not a value")
    ]
    assert messages([{"kind": "table", "item": "margins", "caption": "{x.y}"}])[0][1].startswith(
        "Unknown placeholder {x.y}"
    )
    assert (
        "Cannot read the placeholders" in messages([{"kind": "paragraph", "text": "a } b"}])[0][1]
    )


def test_model_validation_problems():
    found = messages(
        [
            {"kind": "heading", "text": "x", "level": 7},
            {"kind": "table", "item": "margins", "default_format": "0.0.0"},
            {"kind": "nonsense"},
            {"kind": "heading"},
        ]
    )
    assert [i for i, _ in found] == [0, 1, 2, 3]
    assert found[0][1].startswith("level:")
    assert "default_format: Invalid number format" in found[1][1]


def test_metadata_clash_is_a_problem_of_the_items():
    clash = {"title": ed.ItemInfo("title", PortType.TEXT)}
    found = ed.check_sections([], clash)
    assert [(p.index, "metadata name" in p.message) for p in found] == [(-1, True)]


def test_item_info_from_value():
    assert ed.item_info_from_value("a", pd.DataFrame({"X": [1]})).columns == ("X",)
    multi = pd.DataFrame([[1, 2]], columns=pd.MultiIndex.from_tuples([("T", "LC1"), ("T", "LC2")]))
    assert ed.item_info_from_value("m", multi).columns == ("T",)
    kinds = {
        "s": (1.5, PortType.SCALAR),
        "q": (Quantity(1.0, "N"), PortType.SCALAR),
        "t": ("x", PortType.TEXT),
        "b": (True, PortType.ANY),
        "o": (object(), PortType.ANY),
    }
    for name, (value, kind) in kinds.items():
        assert ed.item_info_from_value(name, value).type is kind, name


# ---- the block refuses what the editor flags -------------------------------------------


def test_block_run_refuses_sections_that_do_not_fit(tmp_path):
    params = WordReport.Params.model_validate(
        {
            "output_file": "r.docx",
            "sections": [
                {"kind": "table", "item": "margins", "columns": ["NOPE"]},
                {"kind": "paragraph", "text": "{nope}"},
            ],
        }
    )
    ctx = RunContext(block_id="r", pipeline_dir=tmp_path, output_dir=tmp_path)
    ctx.input_names = {"items": ["margins"]}
    table = pd.DataFrame({"EID": [1], "MS": [0.5]})
    with pytest.raises(ReportError) as err:
        WordReport().run(params, {"items": [table]}, ctx)
    text = str(err.value)
    assert text.startswith("The report sections do not fit the connected items:")
    assert "- Section 1 (Table · margins): Column(s) ['NOPE'] not in item 'margins'" in text
    assert "- Section 2 ({nope}): Unknown placeholder {nope}" in text
    assert not (tmp_path / "r.docx").exists()


# ---- number formats per column (spec 05 §3.4, 2026-11-26) ------------------------------


def test_empty_format_text_names_what_applies() -> None:
    floats = pd.Series([0.123456, 2.0])
    ints = pd.Series([1, 2])
    assert ed.empty_format_text(floats, "") == "default: auto"
    assert ed.empty_format_text(floats, "0.00") == "default: 0.00"
    assert ed.empty_format_text(ints, "0.00") == "as is"
    assert ed.empty_format_text(None, "0.0") == "default: 0.0"


def test_format_example_follows_the_writer_rule() -> None:
    floats = pd.Series([float("nan"), 0.123456])
    assert ed.format_example(floats, "0.00", "") == "0.123456 → 0.12"
    assert ed.format_example(floats, "", "0.000") == "0.123456 → 0.123"
    assert ed.format_example(floats, "", "") == "0.123456 → 0.1235"
    assert ed.format_example(floats, "0.0E+00", "") == "0.123456 → 1.2E-01"
    assert ed.format_example(pd.Series([1001, 2]), "", "0.00") == "1001 → 1001"
    assert ed.format_example(pd.Series([1001, 2]), "#,##0", "") == "1001 → 1,001"


def test_format_example_empty_when_unknown_or_invalid() -> None:
    assert ed.format_example(None, "0.00", "") == ""
    assert ed.format_example(pd.Series([], dtype=float), "0.00", "") == ""
    assert ed.format_example(pd.Series(["a"]), "0.00", "") == ""
    assert ed.format_example(pd.Series([1.5]), "0.0.0", "") == ""
