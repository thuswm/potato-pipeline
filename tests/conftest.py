"""Shared fixtures: small test blocks and paths to the Nastran test files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import pytest
from pydantic import BaseModel

from apb.engine import Block, BlockRegistry, NoParams, Port, PortType, RunContext
from apb.engine.block import FilePath

DATA_DIR = Path(__file__).resolve().parents[1] / "examples" / "data"
PLATE_H5 = DATA_DIR / "synthetic_plate.h5"
JOINT_H5 = DATA_DIR / "synthetic_fastener_joint.h5"

#: Real MSC Nastran 2023.3 reference files (spec 02 §3.9); unit system SI.
FEM_DIR = Path(__file__).resolve().parents[1] / "examples" / "fem"
MODAL_H5 = FEM_DIR / "modal.h5"
GLOBAL_H5 = FEM_DIR / "global_loads.h5"
LOCAL_H5 = FEM_DIR / "local_loads.h5"


class SourceParams(BaseModel):
    values: list[float] = [1.0, 2.0, 3.0]


class Source(Block[SourceParams]):
    """Produces a table with column A."""

    type_id = "test.source"
    category = "Test"
    title = "Source"
    outputs = (Port("table", PortType.TABLE),)
    Params = SourceParams
    runs: ClassVar[int] = 0

    def run(self, params: SourceParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        type(self).runs += 1
        ctx.log(f"{len(params.values)} values")
        return {"table": pd.DataFrame({"A": params.values})}


class ScaleParams(BaseModel):
    factor: float = 2.0


class Scale(Block[ScaleParams]):
    """Multiplies column A; deliberately modifies its input in place (copy test)."""

    type_id = "test.scale"
    category = "Test"
    title = "Scale"
    inputs = (Port("table", PortType.TABLE),)
    outputs = (Port("table", PortType.TABLE),)
    Params = ScaleParams
    runs: ClassVar[int] = 0

    def run(self, params: ScaleParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        type(self).runs += 1
        table = inputs["table"]
        table["A"] = table["A"] * params.factor  # in place on purpose
        return {"table": table}


class Total(Block[NoParams]):
    """Sum of column A as scalar."""

    type_id = "test.total"
    category = "Test"
    title = "Total"
    inputs = (Port("table", PortType.TABLE),)
    outputs = (Port("value", PortType.SCALAR),)

    def run(self, params: NoParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        return {"value": float(inputs["table"]["A"].sum())}


class Stack(Block[NoParams]):
    """Concatenates several tables (multiple input) plus an optional extra table."""

    type_id = "test.stack"
    category = "Test"
    title = "Stack"
    inputs = (
        Port("tables", PortType.TABLE, multiple=True),
        Port("extra", PortType.TABLE, optional=True),
    )
    outputs = (Port("table", PortType.TABLE),)

    def run(self, params: NoParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        tables = list(inputs["tables"])
        if inputs["extra"] is not None:
            tables.append(inputs["extra"])
        return {"table": pd.concat(tables, ignore_index=True)}


class Fail(Block[NoParams]):
    """Always raises."""

    type_id = "test.fail"
    category = "Test"
    title = "Fail"
    inputs = (Port("table", PortType.TABLE, optional=True),)
    outputs = (Port("table", PortType.TABLE),)

    def run(self, params: NoParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        raise ValueError("boom")


class BadOutput(Block[NoParams]):
    """Returns a wrongly typed output."""

    type_id = "test.bad_output"
    category = "Test"
    title = "Bad output"
    outputs = (Port("value", PortType.SCALAR),)

    def run(self, params: NoParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        return {"value": "not a number"}


class TextFileParams(BaseModel):
    file: FilePath


class TextFile(Block[TextFileParams]):
    """Reads a text file (tests input_files cache invalidation)."""

    type_id = "test.text_file"
    category = "Test"
    title = "Text file"
    outputs = (Port("text", PortType.TEXT),)
    Params = TextFileParams
    runs: ClassVar[int] = 0

    def run(
        self, params: TextFileParams, inputs: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        type(self).runs += 1
        return {"text": ctx.resolve_path(params.file).read_text(encoding="utf-8")}

    def input_files(self, params: TextFileParams, ctx: RunContext) -> list[Path]:
        return [ctx.resolve_path(params.file)]


TEST_BLOCKS: list[type[Block[Any]]] = [Source, Scale, Total, Stack, Fail, BadOutput, TextFile]


@pytest.fixture
def registry() -> BlockRegistry:
    """Registry with the test blocks; run counters reset."""
    Source.runs = Scale.runs = TextFile.runs = 0
    return BlockRegistry(TEST_BLOCKS)


def run_block(
    cls: type[Block[Any]],
    values: dict[str, Any] | None = None,
    tmp: Path | None = None,
    **params: Any,
) -> dict[str, Any]:
    """Run one block directly (no pipeline) and return its outputs."""
    base = tmp or Path.cwd()
    ctx = RunContext(block_id="t", pipeline_dir=base, output_dir=base)
    parsed = cls.Params.model_validate(params)
    full_inputs = {p.name: ([] if p.multiple else None) for p in cls.input_ports(parsed)}
    full_inputs.update(values or {})
    return cls().run(parsed, full_inputs, ctx)


# ---- fake Nastran (spec 09 §3.11) -------------------------------------------------------

FAKE_NASTRAN = Path(__file__).resolve().parent / "fake_nastran.py"


class FakeNastran:
    """A ``nastran`` command on a folder that the block's ``path`` setting points to."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import os
        import stat
        import sys

        self.bin = tmp_path / "fakebin"
        self.bin.mkdir()
        self.log = tmp_path / "nastran_calls.txt"
        if os.name == "nt":
            (self.bin / "nastran.bat").write_text(
                f'@echo off\r\n"{sys.executable}" "{FAKE_NASTRAN}" %*\r\n', encoding="utf-8"
            )
        else:
            launcher = self.bin / "nastran"
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_NASTRAN}" "$@"\n')
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        monkeypatch.setenv("FAKE_NASTRAN_LOG", str(self.log))
        monkeypatch.setenv("APB_CONFIG", str(tmp_path / "no_config.toml"))  # no user config
        self._monkeypatch = monkeypatch

    def behave(self, mode: str) -> None:
        """Select the behaviour of the fake (see ``tests/fake_nastran.py``)."""
        self._monkeypatch.setenv("FAKE_NASTRAN", mode)

    def calls(self) -> list[tuple[str, str]]:
        """Return ``(working folder, arguments)`` of every call so far."""
        if not self.log.exists():
            return []
        return [tuple(line.split("|", 1)) for line in self.log.read_text().splitlines()]  # type: ignore[misc]


@pytest.fixture
def fake_nastran(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeNastran:
    """A fake ``nastran`` program; blocks find it with ``path=str(fake_nastran.bin)``."""
    return FakeNastran(tmp_path, monkeypatch)


def write_deck(
    folder: Path, job: str = "job", *, hdf5: bool = True, include: str | None = None
) -> Path:
    """Write a minimal input deck ``<job>.bdf`` (optionally including another file)."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["SOL SESTATIC", "CEND", "  TITLE = test", "BEGIN BULK"]
    if hdf5:
        lines.append("HDF5OUT CMPRMTHDNONE")
    if include:
        lines.append(f"INCLUDE '{include}'")
    lines += ["GRID,1,,0.,0.,0.", "ENDDATA"]
    deck = folder / f"{job}.bdf"
    deck.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return deck
