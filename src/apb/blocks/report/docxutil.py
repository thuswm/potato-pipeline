"""Low-level python-docx helpers: fields, shading, placeholder replacement."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from docx.document import Document as DocumentObject
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import _Cell
from docx.text.paragraph import Paragraph


def add_field(paragraph: Paragraph, instruction: str, placeholder: str = "1") -> None:
    r"""Append a Word field (e.g. ``PAGE`` or ``SEQ Table \* ARABIC``) to a paragraph.

    Args:
        paragraph: Target paragraph.
        instruction: Field code.
        placeholder: Result shown until Word updates the field.
    """
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), instruction)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = placeholder
    run.append(text)
    field.append(run)
    paragraph._p.append(field)


def shade_cell(cell: _Cell, fill_hex: str) -> None:
    """Set the background colour of a table cell, e.g. ``"FFC7CE"``."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill_hex)
    tc_pr.append(shading)


def repeat_header_row(row_element: object) -> None:
    """Mark a table row as header row, repeated on every page."""
    tr_pr = row_element._tr.get_or_add_trPr()  # type: ignore[attr-defined]
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def bottom_border(paragraph: Paragraph, size_eighths: int = 6) -> None:
    """Draw a line under a paragraph (used in headers)."""
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size_eighths))
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "auto")
    borders.append(bottom)
    p_pr.append(borders)


def all_paragraphs(doc: DocumentObject) -> Iterable[Paragraph]:
    """Yield the paragraphs of the body, tables, and all headers and footers."""

    def from_container(container: object) -> Iterable[Paragraph]:
        yield from container.paragraphs  # type: ignore[attr-defined]
        for table in container.tables:  # type: ignore[attr-defined]
            for row in table.rows:
                for cell in row.cells:
                    yield from from_container(cell)

    yield from from_container(doc)
    for section in doc.sections:
        for part in (
            section.header,
            section.footer,
            section.first_page_header,
            section.first_page_footer,
            section.even_page_header,
            section.even_page_footer,
        ):
            if not part.is_linked_to_previous:
                yield from from_container(part)


def replace_in_paragraph(paragraph: Paragraph, replace: Callable[[str], str]) -> None:
    """Apply a text replacement to a paragraph, keeping the first run's formatting.

    Word often splits text into several runs; the replacement works on the joined text.
    Paragraphs whose text does not change are left untouched (formatting preserved).
    """
    runs = paragraph.runs
    if not runs:
        return
    old = "".join(r.text for r in runs)
    new = replace(old)
    if new == old:
        return
    runs[0].text = new
    for run in runs[1:]:
        run.text = ""
