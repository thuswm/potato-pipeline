"""Pipeline data model and the ``*.apb.json`` file format (spec 01 §3.2, §3.5)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from apb.units import UnitSystem

SCHEMA_VERSION: Final = 1
PIPELINE_SUFFIX: Final = ".apb.json"


def new_id(prefix: str = "b") -> str:
    """Return a short unique ID, e.g. ``"b3f9a1c2"``."""
    return f"{prefix}{uuid.uuid4().hex[:8]}"


class _Model(BaseModel):
    """Base model: unknown keys are rejected so that typos in files are found."""

    model_config = ConfigDict(extra="forbid")


class PortRef(_Model):
    """Reference to a port of a block instance."""

    block: str
    port: str

    def __str__(self) -> str:
        return f"{self.block}.{self.port}"


class BlockInstance(_Model):
    """A block placed in a pipeline."""

    id: str = Field(default_factory=new_id)
    type_id: str
    type_version: int = 1
    label: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    position: tuple[float, float] = (0.0, 0.0)
    notes: str = ""


class Connection(_Model):
    """A connector from an output port to an input port."""

    id: str = Field(default_factory=lambda: new_id("c"))
    source: PortRef
    target: PortRef


class PipelineMetadata(_Model):
    """Descriptive data of a pipeline."""

    title: str = ""
    author: str = ""
    created: str = ""
    description: str = ""


class PipelineSettings(_Model):
    """Pipeline-wide settings."""

    default_unit_system: UnitSystem = UnitSystem.SI_MM


class PipelineModel(_Model):
    """A complete pipeline: blocks, connections, variables and settings."""

    schema_version: Literal[1] = SCHEMA_VERSION
    metadata: PipelineMetadata = Field(default_factory=PipelineMetadata)
    settings: PipelineSettings = Field(default_factory=PipelineSettings)
    variables: dict[str, Any] = Field(default_factory=dict)
    blocks: list[BlockInstance] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)
    macros: list[dict[str, Any]] = Field(
        default_factory=list, description="Reserved for macro blocks (later version)."
    )

    def block(self, block_id: str) -> BlockInstance:
        """Return the block instance with the given ID.

        Raises:
            KeyError: If there is no such block.
        """
        for b in self.blocks:
            if b.id == block_id:
                return b
        raise KeyError(f"No block with id {block_id!r}")

    def add_block(
        self, type_id: str, *, label: str = "", block_id: str | None = None, **params: Any
    ) -> BlockInstance:
        """Append a block instance (convenience for scripts and tests).

        Args:
            type_id: Block type.
            label: Display label.
            block_id: Explicit ID (default: generated).
            **params: Block parameters.

        Returns:
            The new block instance.
        """
        inst = BlockInstance(
            type_id=type_id, label=label, params=params, **({"id": block_id} if block_id else {})
        )
        self.blocks.append(inst)
        return inst

    def connect(self, source: str, target: str) -> Connection:
        """Append a connection given as ``"block.port"`` strings.

        Args:
            source: Output port, e.g. ``"b1.table"``.
            target: Input port, e.g. ``"b2.table"``.

        Returns:
            The new connection.
        """
        s_block, s_port = source.split(".", 1)
        t_block, t_port = target.split(".", 1)
        conn = Connection(
            source=PortRef(block=s_block, port=s_port), target=PortRef(block=t_block, port=t_port)
        )
        self.connections.append(conn)
        return conn

    def to_json(self) -> str:
        """Serialise to pretty-printed JSON (stable key order, diff-friendly)."""
        data = self.model_dump(mode="json")
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> PipelineModel:
        """Parse a pipeline from JSON text.

        Raises:
            pydantic.ValidationError: If the content does not match the schema.
        """
        return cls.model_validate_json(text)


def load_pipeline(path: Path) -> PipelineModel:
    """Read a pipeline file.

    Args:
        path: ``*.apb.json`` file.

    Returns:
        The pipeline.
    """
    return PipelineModel.from_json(path.read_text(encoding="utf-8"))


def save_pipeline(pipeline: PipelineModel, path: Path) -> None:
    """Write a pipeline file (UTF-8, LF line endings).

    Args:
        pipeline: Pipeline to save.
        path: Target file.
    """
    path.write_text(pipeline.to_json(), encoding="utf-8", newline="\n")
