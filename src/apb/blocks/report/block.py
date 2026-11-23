"""The ``report.word`` block (spec 05)."""

from __future__ import annotations

import contextlib
import datetime as dt
import string
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from apb.blocks.report.editing import check_sections, describe, item_info_from_value
from apb.blocks.report.sections import Section
from apb.blocks.report.writer import ReportBuilder, ReportError, default_base_document
from apb.engine.block import Block, FilePath, RunContext, file_types
from apb.engine.types import Port, PortType
from apb.fileutil import atomic_output


def referenced_items(sections: list[Section]) -> set[str]:
    """Return the item names used by sections, directly or as ``{placeholders}``."""
    names: set[str] = set()
    for section in sections:
        if item := getattr(section, "item", None):
            names.add(item)
        for text in (
            getattr(section, "text", ""),
            getattr(section, "caption", ""),
            getattr(section, "label", ""),
        ):
            with contextlib.suppress(ValueError):
                names.update(f for _, f, _, _ in string.Formatter().parse(text) if f)
    return names


class ReportMetadata(BaseModel):
    """Report metadata, also filled into ``{title}`` etc. in the base document."""

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    document_number: str = ""
    revision: str = ""
    author: str = ""
    date: str = Field(default="", description="Empty = date of the run (YYYY-MM-DD)")


class WordReportParams(BaseModel):
    """Parameters of :class:`WordReport`."""

    model_config = ConfigDict(extra="forbid")

    output_file: str = Field(description="Report to write (.docx), relative to the output folder")
    mode: Literal["generated"] = Field(
        default="generated", description="generated (template mode follows in a later version)"
    )
    base_document: FilePath = Field(
        default="",
        description="Word file providing styles, page setup, header/footer; "
        "empty = built-in test base document",
        json_schema_extra=file_types(".docx", ".dotx"),
    )
    metadata: ReportMetadata = Field(default_factory=ReportMetadata)
    sections: list[Section] = Field(default_factory=list, description="Report content, in order")


class WordReport(Block[WordReportParams]):
    """Write a Word report from connected tables, values, texts and figures."""

    type_id = "report.word"
    category = "Output"
    title = "Word report"
    description = (
        "Builds a .docx report from an ordered list of sections (headings, text with "
        "{placeholders}, tables, figures). Connected values are referred to by name."
    )
    inputs = (Port("items", PortType.ANY, label="Items", multiple=True),)
    outputs = (Port("file", PortType.TEXT, label="File"),)
    Params = WordReportParams
    always_run = True

    def output_files(
        self, params: WordReportParams, outputs: Mapping[str, Any], ctx: RunContext
    ) -> list[Path]:
        """The report (the block counts as done only while it exists)."""
        out = Path(params.output_file)
        return [out if out.is_absolute() else ctx.output_dir / out]

    def input_files(self, params: WordReportParams, ctx: RunContext) -> list[Path]:
        """The base document, if one is set."""
        return [ctx.resolve_path(params.base_document)] if params.base_document else []

    @staticmethod
    def _check(params: WordReportParams, items: dict[str, Any]) -> None:
        """Refuse to run if the sections do not fit the connected items (REP-18).

        The same check as the section editor shows before the run.
        """
        infos = {name: item_info_from_value(name, value) for name, value in items.items()}
        problems = check_sections(params.sections, infos)
        if not problems:
            return
        lines = []
        for p in problems:
            if p.index < 0:
                lines.append(f"- {p.message}")
            else:
                what = describe(params.sections[p.index].model_dump())
                lines.append(f"- Section {p.index + 1} ({what}): {p.message}")
        raise ReportError(
            "The report sections do not fit the connected items:\n" + "\n".join(lines)
        )

    def run(
        self, params: WordReportParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Build and write the report."""
        out = Path(params.output_file)
        if out.suffix.lower() != ".docx":
            raise ReportError(f"Report file must end with .docx, got {params.output_file!r}")
        target = out if out.is_absolute() else ctx.output_dir / out

        base = ctx.resolve_path(params.base_document) if params.base_document else None
        if base is not None and not base.is_file():
            raise ReportError(f"Base document not found: {base}")
        if base is None:
            ctx.log(f"Using the built-in test base document ({default_base_document().name})")

        names = ctx.input_names.get("items", [])
        items = dict(zip(names, inputs["items"], strict=True))
        metadata = params.metadata.model_dump()
        metadata["date"] = metadata["date"] or dt.date.today().isoformat()
        self._check(params, items)

        builder = ReportBuilder(items, metadata, base, ctx.default_unit_system, ctx.log)
        builder.pipeline_file = ctx.pipeline_file
        builder.source_files = ctx.upstream_files
        builder.build(params.sections)
        unused = [n for n in items if n not in referenced_items(params.sections)]
        if unused:
            ctx.log(f"Connected but not used in the report: {', '.join(unused)}")
        with atomic_output(target) as tmp:
            builder.save(tmp)
        ctx.log(f"Wrote {target}")
        return {"file": str(target)}


BLOCKS: tuple[type[Block[Any]], ...] = (WordReport,)
