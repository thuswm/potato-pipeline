"""Builds a Word document from report sections (generated mode, spec 05 §3.2)."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
from collections.abc import Callable, Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

import pandas as pd
from docx import Document
from docx.document import Document as DocumentObject
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.shared import Cm, Pt
from docx.table import Table
from docx.text.paragraph import Paragraph

from apb import __version__
from apb.blocks.report.docxutil import (
    add_field,
    all_paragraphs,
    repeat_header_row,
    replace_in_paragraph,
    shade_cell,
)
from apb.blocks.report.sections import (
    FigureSection,
    HeadingSection,
    PageBreakSection,
    ParagraphSection,
    Section,
    TableSection,
    TitleSection,
    TraceabilitySection,
    UnitsSection,
    ValueSection,
    fill_text,
    format_value,
    python_format,
)
from apb.units import (
    MASS_IN_KG,
    Quantity,
    QuantityKind,
    UnitSystem,
    get_units,
    header_with_unit,
    mass_in_kg,
    unit_label,
)

HEADER_FILL = "D9D9D9"
HIGHLIGHT_FILL = "FFC7CE"
TABLE_FONT_SIZE = Pt(8.5)
METADATA_KEYS = ("title", "document_number", "revision", "author", "date")


class ReportError(ValueError):
    """Raised for invalid report content (unknown item, wrong item type, ...)."""


def default_base_document() -> Path:
    """Return the path of the test base document shipped with the tool."""
    return Path(str(resources.files("apb.blocks.report") / "resources" / "test_base.docx"))


def _is_scalar(value: Any) -> bool:
    return isinstance(value, int | float | Quantity) and not isinstance(value, bool)


class ReportBuilder:
    """Builds one report.

    Args:
        items: Connected values by name (tables, scalars, texts, figures).
        metadata: Title, document number, revision, author, date.
        base_document: ``.docx`` whose styles, page setup and header/footer are used.
        default_unit_system: Unit system stated when no item declares one.
        log: Receives notes for the block log.
    """

    def __init__(
        self,
        items: Mapping[str, Any],
        metadata: Mapping[str, str],
        base_document: Path | None = None,
        default_unit_system: UnitSystem | None = None,
        log: Callable[[str], None] = lambda _m: None,
    ) -> None:
        self.items = dict(items)
        self.metadata = dict(metadata)
        clash = set(self.items) & set(self.metadata)
        if clash:
            raise ReportError(f"Item name(s) {sorted(clash)} clash with report metadata names")
        self.doc: DocumentObject = Document(str(base_document or default_base_document()))
        self.default_unit_system = default_unit_system
        self.log = log
        self._tables = 0
        self._figures = 0
        self._mass_in_kg = False
        self.pipeline_file: Path | None = None
        """Pipeline file, listed by the traceability section."""
        self.source_files: list[Path] = []
        """Input files of the pipeline (Nastran results, scripts ...), for traceability."""
        self._fill_metadata()

    # ---- public -------------------------------------------------------------------------

    def build(self, sections: Sequence[Section]) -> DocumentObject:
        """Append all sections to the document and return it."""
        self._mass_in_kg = any(
            isinstance(s, TableSection)
            and s.mass_in_kg
            and isinstance(table := self.items.get(s.item), pd.DataFrame)
            and any(u in MASS_IN_KG for u in get_units(table).values())
            for s in sections
        )
        for section in sections:
            self._add(section)
        if not self.doc.paragraphs and not self.doc.tables:
            self.doc.add_paragraph()  # a .docx body needs at least one paragraph
        return self.doc

    def save(self, path: Path) -> None:
        """Save the document."""
        self.doc.save(str(path))

    # ---- dispatch -------------------------------------------------------------------------

    def _add(self, section: Section) -> None:
        match section:
            case TitleSection():
                self._title()
            case HeadingSection():
                self.doc.add_heading(self._text(section.text), level=section.level)
            case ParagraphSection():
                self.doc.add_paragraph(self._text(section.text))
            case ValueSection():
                self._value(section)
            case TableSection():
                self._table(section)
            case FigureSection():
                self._figure(section)
            case PageBreakSection():
                self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            case UnitsSection():
                self._units()
            case TraceabilitySection():
                self._traceability(section)

    # ---- helpers --------------------------------------------------------------------------

    def _text_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {
            k: v for k, v in self.items.items() if _is_scalar(v) or isinstance(v, str)
        }
        values.update(self.metadata)
        return values

    def _text(self, text: str) -> str:
        return fill_text(text, self._text_values())

    def _item(self, name: str, kind: str, check: Callable[[Any], bool]) -> Any:
        if name not in self.items:
            available = ", ".join(sorted(self.items)) or "none"
            raise ReportError(f"No connected item {name!r}. Available: {available}")
        value = self.items[name]
        if not check(value):
            raise ReportError(f"Item {name!r} is a {type(value).__name__}, not a {kind}")
        return value

    def _fill_metadata(self) -> None:
        """Replace ``{title}`` etc. in the base document (header, footer, front page)."""
        pattern = re.compile(r"\{(" + "|".join(METADATA_KEYS) + r")\}")

        def replace(text: str) -> str:
            return pattern.sub(lambda m: self.metadata.get(m.group(1), ""), text)

        for paragraph in all_paragraphs(self.doc):
            replace_in_paragraph(paragraph, replace)
        if self.metadata.get("title"):
            self.doc.core_properties.title = self.metadata["title"]
        if self.metadata.get("author"):
            self.doc.core_properties.author = self.metadata["author"]

    def _caption(self, label: str, number: int, text: str) -> Paragraph:
        """Add a numbered caption ("Table 3: ...") using a Word SEQ field."""
        try:
            paragraph = self.doc.add_paragraph(style="Caption")
        except KeyError:
            paragraph = self.doc.add_paragraph()
        paragraph.add_run(f"{label} ")
        add_field(paragraph, f"SEQ {label} \\* ARABIC", str(number))
        if text:
            paragraph.add_run(f": {self._text(text)}")
        return paragraph

    # ---- section kinds --------------------------------------------------------------------

    def _title(self) -> None:
        md = self.metadata
        try:
            self.doc.add_paragraph(md.get("title", ""), style="Title")
        except KeyError:
            self.doc.add_heading(md.get("title", ""), level=0)
        rows = [
            ("Document", md.get("document_number", "")),
            ("Revision", md.get("revision", "")),
            ("Author", md.get("author", "")),
            ("Date", md.get("date", "")),
        ]
        rows = [(k, v) for k, v in rows if v]
        if rows:
            table = self.doc.add_table(rows=len(rows), cols=2)
            for (key, value), row in zip(rows, table.rows, strict=True):
                row.cells[0].text, row.cells[1].text = key, value
                row.cells[0].paragraphs[0].runs[0].bold = True
            self.doc.add_paragraph()

    def _value(self, section: ValueSection) -> None:
        value = self._item(
            section.item, "scalar or text", lambda v: _is_scalar(v) or isinstance(v, str)
        )
        spec = python_format(section.format)
        text = value if isinstance(value, str) else format(value, spec)
        paragraph = self.doc.add_paragraph()
        if section.label:
            paragraph.add_run(f"{self._text(section.label)}: ").bold = True
        paragraph.add_run(text)

    def _table(self, section: TableSection) -> None:
        df: pd.DataFrame = self._item(section.item, "table", lambda v: isinstance(v, pd.DataFrame))
        if section.mass_in_kg:
            df = mass_in_kg(df)
        multi = isinstance(df.columns, pd.MultiIndex)
        units = get_units(df) if section.units_in_header else {}
        if section.columns:
            top = [c[0] for c in df.columns] if multi else list(df.columns)
            missing = [c for c in section.columns if c not in top]
            if missing:
                raise ReportError(
                    f"Column(s) {missing} not in item {section.item!r}. "
                    f"Available: {', '.join(dict.fromkeys(map(str, top)))}"
                )
            df = (
                df[[c for c in df.columns if (c[0] if multi else c) in section.columns]]
                if multi
                else df[section.columns]
            )
        mask = pd.Series(False, index=df.index)
        if section.highlight:
            if multi:
                raise ReportError("Highlighting is not supported for two-level tables")
            try:
                mask = pd.Series(df.eval(section.highlight), index=df.index).astype(bool)
            except Exception as exc:
                raise ReportError(
                    f"Invalid highlight condition {section.highlight!r}: {exc}"
                ) from exc
        total = len(df)
        if section.max_rows and total > section.max_rows:
            df, mask = df.head(section.max_rows), mask.head(section.max_rows)

        self._tables += 1
        self._caption("Table", self._tables, section.caption)
        header_rows = 2 if multi else 1
        table = self.doc.add_table(rows=header_rows + len(df), cols=len(df.columns))
        try:
            table.style = self.doc.styles["Table Grid"]
        except KeyError:
            self.log("Base document has no 'Table Grid' style; table drawn without borders")
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        self._table_header(table, df, units, multi)

        formats = {name: python_format(spec) for name, spec in section.formats.items()}
        default = python_format(section.default_format)
        numeric = [pd.api.types.is_numeric_dtype(df[c]) for c in df.columns]
        is_float = [pd.api.types.is_float_dtype(df[c]) for c in df.columns]
        keys = [c[0] if multi else c for c in df.columns]
        for r, (row_values, highlight) in enumerate(
            zip(df.itertuples(index=False), mask, strict=True)
        ):
            cells = table.rows[header_rows + r].cells
            for c, value in enumerate(row_values):
                spec = formats.get(str(keys[c]), default if is_float[c] else "auto")
                cell = cells[c]
                cell.text = format_value(value, spec)
                para = cell.paragraphs[0]
                para.runs[0].font.size = TABLE_FONT_SIZE if para.runs else None
                if numeric[c]:
                    para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                if highlight:
                    shade_cell(cell, HIGHLIGHT_FILL)
        if section.max_rows and total > section.max_rows:
            self.doc.add_paragraph(f"(First {section.max_rows} of {total} rows shown.)")
        if section.highlight and mask.any():
            self.log(
                f"Table {section.item!r}: {int(mask.sum())} row(s) highlighted "
                f"({section.highlight})"
            )
        self.doc.add_paragraph()

    @staticmethod
    def _table_header(
        table: Table, df: pd.DataFrame, units: Mapping[str, str], multi: bool
    ) -> None:
        def put(row: int, col: int, text: str) -> None:
            cell = table.cell(row, col)
            cell.text = text
            run = cell.paragraphs[0].runs[0] if cell.paragraphs[0].runs else None
            if run is not None:
                run.bold = True
                run.font.size = TABLE_FONT_SIZE
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            shade_cell(cell, HEADER_FILL)

        if not multi:
            for c, name in enumerate(df.columns):
                put(0, c, header_with_unit(str(name), units))
            repeat_header_row(table.rows[0])
            return
        tops = [str(c[0]) for c in df.columns]
        subs = [str(c[1]) for c in df.columns]
        c = 0
        while c < len(tops):
            if not subs[c]:
                merged = table.cell(0, c).merge(table.cell(1, c))
                merged.text = ""
                put(0, c, header_with_unit(tops[c], units))
                c += 1
                continue
            end = c
            while end + 1 < len(tops) and tops[end + 1] == tops[c] and subs[end + 1]:
                end += 1
            if end > c:
                table.cell(0, c).merge(table.cell(0, end))
            put(0, c, header_with_unit(tops[c], units))
            for k in range(c, end + 1):
                put(1, k, subs[k])
            c = end + 1
        repeat_header_row(table.rows[0])
        repeat_header_row(table.rows[1])

    def _figure(self, section: FigureSection) -> None:
        fig = self._item(section.item, "figure", lambda v: hasattr(v, "savefig"))
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=section.dpi, bbox_inches="tight")
        buffer.seek(0)
        self.doc.add_picture(buffer, width=Cm(section.width_cm))
        self.doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        self._figures += 1
        self._caption(
            "Figure", self._figures, section.caption
        ).alignment = WD_ALIGN_PARAGRAPH.CENTER

    def _units(self) -> None:
        systems = {
            str(v.attrs["unit_system"])
            for v in self.items.values()
            if isinstance(v, pd.DataFrame) and v.attrs.get("unit_system")
        }
        if not systems and self.default_unit_system is not None:
            systems = {str(self.default_unit_system)}
        if not systems:
            self.doc.add_paragraph("Units: not specified.")
            return
        for name in sorted(systems):
            system = UnitSystem(name)
            parts = [
                f"{label} in {unit_label(kind, system)}"
                for label, kind in (
                    ("lengths", QuantityKind.LENGTH),
                    ("forces", QuantityKind.FORCE),
                    ("moments", QuantityKind.MOMENT),
                    ("stresses", QuantityKind.STRESS),
                    ("masses", QuantityKind.MASS),
                )
            ]
            if system is UnitSystem.SI_MM and self._mass_in_kg:
                parts[-1] += " (kg in the tables)"
            self.doc.add_paragraph(f"Unit system {name}: {', '.join(parts)}.")
        if len(systems) > 1:
            self.log("WARNING: report items use more than one unit system")

    def _traceability(self, section: TraceabilitySection) -> None:
        self.doc.add_heading(section.heading, level=1)
        now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        lines = [f"Generated {now} with Potato Pipeline {__version__}."]
        base_dir = self.pipeline_file.parent if self.pipeline_file else None
        if self.pipeline_file is not None:
            lines.append("Pipeline: " + self._describe_file(self.pipeline_file, base_dir))
        files = {Path(f) for f in self.source_files}
        files |= {
            Path(str(v.attrs["source_file"]))
            for v in self.items.values()
            if isinstance(v, pd.DataFrame) and v.attrs.get("source_file")
        }
        for path in sorted(files):
            lines.append("Input: " + self._describe_file(path, base_dir))
        if not files:
            lines.append("No input files recorded.")
        for line in lines:
            try:
                self.doc.add_paragraph(line, style="List Bullet")
            except KeyError:
                self.doc.add_paragraph(f"- {line}")

    @staticmethod
    def _describe_file(path: Path, base_dir: Path | None) -> str:
        """Name (relative to the pipeline), modification time, short hash, Nastran info."""
        try:
            text = path.relative_to(base_dir).as_posix() if base_dir else path.name
        except ValueError:
            text = path.name
        try:
            stat = path.stat()
            modified = dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            text += f" (modified {modified}, SHA-256 {digest}…)"
        except OSError:
            return text + " (file not accessible)"
        if path.suffix.lower() == ".h5":
            from apb.blocks.nastran import h5reader

            try:
                version = h5reader.nastran_version(path)
                synthetic = h5reader.is_synthetic(path)
            except h5reader.NastranH5Error:
                return text
            if version:
                text += f", MSC Nastran {version}"
            if synthetic:
                text += ", SYNTHETIC TEST DATA"
        return text
