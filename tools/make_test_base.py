"""Generate the test base Word document used by the report block (spec 05 §3.2).

FOR DEVELOPMENT ONLY. It stands in for the company template, which replaces it before
deployment. Contents: A4 page, styles (Normal, Heading 1-3, Title, Caption), a header with
``{title}``, ``{document_number}`` and ``{revision}`` placeholders, and a footer with
"Page X of Y". The body is empty.

Usage::

    uv run python tools/make_test_base.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from apb.blocks.report.docxutil import add_field, bottom_border

TARGET = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "apb"
    / "blocks"
    / "report"
    / "resources"
    / "test_base.docx"
)
FONT = "Arial"
DARK_BLUE = RGBColor(0x1F, 0x3A, 0x5F)


def _use_font(style: Any, font: str) -> None:
    """Set a style's font and drop theme-font references that would override it."""
    fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        fonts.attrib.pop(qn(attr), None)
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        fonts.set(qn(attr), font)


def build(path: Path) -> None:
    """Build the test base document.

    Args:
        path: Output ``.docx`` file.
    """
    doc = Document()
    doc.core_properties.title = "APB test base document"
    doc.core_properties.comments = "Development stand-in for the company template"

    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.2)
    section.top_margin, section.bottom_margin = Cm(2.5), Cm(2.0)

    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(10)
    for name, size in (("Heading 1", 14), ("Heading 2", 12), ("Heading 3", 11)):
        style = doc.styles[name]
        style.font.name = FONT
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = DARK_BLUE
    title = doc.styles["Title"]
    title.font.name = FONT
    title.font.size = Pt(22)
    title.font.color.rgb = DARK_BLUE
    for style in (
        normal,
        doc.styles["Heading 1"],
        doc.styles["Heading 2"],
        doc.styles["Heading 3"],
        title,
    ):
        _use_font(style, FONT)
    caption = doc.styles["Caption"]
    caption.font.name = FONT
    caption.font.size = Pt(9)
    caption.font.italic = True
    caption.font.bold = False
    caption.font.color.rgb = RGBColor(0x40, 0x40, 0x40)

    usable = section.page_width - section.left_margin - section.right_margin
    header = section.header.paragraphs[0]
    # The Header style has centre/right tab stops for Letter paper; replace them for A4.
    for style_name in ("Header", "Footer"):
        tabs = doc.styles[style_name].paragraph_format.tab_stops
        tabs.clear_all()
        tabs.add_tab_stop(usable // 2, WD_TAB_ALIGNMENT.CENTER)
        tabs.add_tab_stop(usable, WD_TAB_ALIGNMENT.RIGHT)
    header.add_run("{title}\t\t{document_number}  Rev. {revision}").font.size = Pt(8)
    bottom_border(header)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("Page ").font.size = Pt(8)
    add_field(footer, "PAGE")
    footer.add_run(" of ").font.size = Pt(8)
    add_field(footer, "NUMPAGES")

    # Remove the empty first body paragraph so the report starts with its own content.
    body = doc.element.body
    for p in list(
        body.iterchildren(tag="{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")
    ):
        body.remove(p)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


if __name__ == "__main__":
    build(TARGET)
    print(f"Wrote {TARGET}")
