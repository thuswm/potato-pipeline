"""Editor state of the GUI: the pipeline being edited and all edit rules (spec 06).

Pure Python without NiceGUI imports, so the rules are unit-testable. The canvas only shows
this state: every change the user makes on the canvas is sent here and accepted or
rejected (GUI-04), and the canvas is corrected if rejected.
"""

from __future__ import annotations

import graphlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from apb.engine.block import AnyBlockType
from apb.engine.model import (
    BlockInstance,
    Connection,
    PipelineModel,
    PortRef,
    load_pipeline,
    save_pipeline,
)
from apb.engine.registry import BlockRegistry
from apb.engine.types import Port, types_compatible

PIPELINE_SUFFIX = ".apb.json"


class _Unset:
    """Marker: remove a stored parameter (use the default)."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()


class EditError(ValueError):
    """An edit that is not allowed; the message is shown to the user."""


@dataclass(frozen=True)
class ConnectionKey:
    """Identifies a connection by its ports."""

    source_block: str
    source_port: str
    target_block: str
    target_port: str

    @classmethod
    def of(cls, conn: Connection) -> ConnectionKey:
        """Return the key of a connection."""
        return cls(conn.source.block, conn.source.port, conn.target.block, conn.target.port)


class EditorState:
    """The pipeline being edited, its file and its unsaved-changes flag."""

    def __init__(self, registry: BlockRegistry) -> None:
        """Create an editor with an empty pipeline.

        Args:
            registry: Available block types.
        """
        self.registry = registry
        self.pipeline = PipelineModel()
        self.path: Path | None = None
        self.dirty = False
        self._listeners: list[Callable[[], None]] = []

    # ---- change notification ------------------------------------------------------------

    def on_change(self, callback: Callable[[], None]) -> None:
        """Register a callback run after every change (title, dirty marker ...)."""
        self._listeners.append(callback)

    def _changed(self) -> None:
        self.dirty = True
        self._notify()

    def _notify(self) -> None:
        for callback in self._listeners:
            callback()

    @property
    def display_name(self) -> str:
        """File name for the title bar, with ``*`` if there are unsaved changes."""
        name = self.path.name if self.path else "untitled.apb.json"
        return f"{name} *" if self.dirty else name

    # ---- files ------------------------------------------------------------------------

    def new(self) -> None:
        """Start a new, empty pipeline."""
        self.pipeline = PipelineModel()
        self.path = None
        self.dirty = False
        self._notify()

    def open(self, path: Path) -> None:
        """Load a pipeline file.

        Raises:
            EditError: If the file cannot be read or is not a valid pipeline.
        """
        try:
            pipeline = load_pipeline(path)
        except OSError as exc:
            raise EditError(f"Cannot open {path}: {exc.strerror or exc}") from exc
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            raise EditError(
                f"{path.name} is not a valid pipeline file ({where}: {first['msg']})"
            ) from exc
        self.pipeline = pipeline
        self.path = path.resolve()
        self.dirty = False
        self._notify()

    def save(self, path: Path | None = None) -> Path:
        """Save the pipeline (to ``path``, or to its current file).

        Returns:
            The file written.

        Raises:
            EditError: If no file name is known or writing fails.
        """
        target = path or self.path
        if target is None:
            raise EditError("No file name: use Save as")
        if not target.name.endswith(PIPELINE_SUFFIX):
            target = target.with_name(target.name.removesuffix(".json") + PIPELINE_SUFFIX)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            save_pipeline(self.pipeline, target)
        except OSError as exc:
            raise EditError(f"Cannot save {target}: {exc.strerror or exc}") from exc
        self.path = target.resolve()
        self.dirty = False
        self._notify()
        return self.path

    # ---- blocks -----------------------------------------------------------------------

    def block_class(self, block: BlockInstance) -> AnyBlockType | None:
        """Return the class of a block instance (``None`` if the type is not installed)."""
        return self.registry.get(block.type_id) if block.type_id in self.registry else None

    def ports(self, block: BlockInstance) -> tuple[tuple[Port, ...], tuple[Port, ...]]:
        """Return the (input, output) ports of a block instance.

        Blocks whose ports depend on parameters (Python block) use the current parameters
        if they are valid.
        """
        cls = self.block_class(block)
        if cls is None:
            return (), ()
        try:
            params = cls.Params.model_validate(block.params)
        except ValidationError:
            params = None
        return cls.input_ports(params), cls.output_ports(params)

    def _new_block_id(self) -> str:
        used = {b.id for b in self.pipeline.blocks}
        n = len(used) + 1
        while f"b{n}" in used:
            n += 1
        return f"b{n}"

    def add_block(self, type_id: str, x: float, y: float) -> BlockInstance:
        """Add a block of the given type at a canvas position.

        Raises:
            EditError: If the type is unknown.
        """
        if type_id not in self.registry:
            raise EditError(f"Unknown block type {type_id!r}")
        cls = self.registry.get(type_id)
        block = BlockInstance(
            id=self._new_block_id(),
            type_id=type_id,
            type_version=cls.version,
            label=cls.title,
            position=(x, y),
        )
        self.pipeline.blocks.append(block)
        self._changed()
        return block

    def remove_block(self, block_id: str) -> None:
        """Remove a block and all its connections."""
        self.pipeline.blocks = [b for b in self.pipeline.blocks if b.id != block_id]
        self.pipeline.connections = [
            c for c in self.pipeline.connections if block_id not in (c.source.block, c.target.block)
        ]
        self._changed()

    def move_block(self, block_id: str, x: float, y: float) -> None:
        """Store a block's new canvas position."""
        block = self.pipeline.block(block_id)
        if block.position != (x, y):
            block.position = (round(x, 1), round(y, 1))
            self._changed()

    def set_param(self, block_id: str, name: str, value: Any) -> list[Connection]:
        """Set (or, with :data:`UNSET`, remove) one stored parameter of a block.

        If the change alters the block's ports (Python block), connections to ports that
        no longer exist or no longer have a compatible type are removed.

        Returns:
            The connections that were removed.
        """
        block = self.pipeline.block(block_id)
        before = self.ports(block)
        if value is UNSET:
            if name not in block.params:
                return []
            del block.params[name]
        else:
            if block.params.get(name, UNSET) == value:
                return []
            block.params[name] = value
        removed = self._prune_connections(block_id) if self.ports(block) != before else []
        self._changed()
        return removed

    def _prune_connections(self, block_id: str) -> list[Connection]:
        """Drop connections of a block whose ports vanished or became incompatible."""
        removed: list[Connection] = []
        kept: list[Connection] = []
        for conn in self.pipeline.connections:
            if block_id in (conn.source.block, conn.target.block) and not self._still_valid(conn):
                removed.append(conn)
            else:
                kept.append(conn)
        self.pipeline.connections = kept
        return removed

    def _still_valid(self, conn: Connection) -> bool:
        src = self.pipeline.block(conn.source.block)
        dst = self.pipeline.block(conn.target.block)
        out = next((p for p in self.ports(src)[1] if p.name == conn.source.port), None)
        inp = next((p for p in self.ports(dst)[0] if p.name == conn.target.port), None)
        return out is not None and inp is not None and types_compatible(out.type, inp.type)

    def connections_of(self, block_id: str) -> list[dict[str, str]]:
        """Return the connections of a block in canvas format."""
        return [
            self._conn_spec(c)
            for c in self.pipeline.connections
            if block_id in (c.source.block, c.target.block)
        ]

    @staticmethod
    def _conn_spec(c: Connection) -> dict[str, str]:
        return {
            "source_block": c.source.block,
            "source_port": c.source.port,
            "target_block": c.target.block,
            "target_port": c.target.port,
        }

    def set_variables(self, variables: dict[str, Any]) -> None:
        """Replace the pipeline variables."""
        if variables != self.pipeline.variables:
            self.pipeline.variables = dict(variables)
            self._changed()

    def set_settings(self, **changes: Any) -> None:
        """Change pipeline settings or metadata fields (``default_unit_system``, ``title`` ...)."""
        changed = False
        for key, value in changes.items():
            target: Any = (
                self.pipeline.settings
                if key in type(self.pipeline.settings).model_fields
                else self.pipeline.metadata
            )
            if getattr(target, key) != value:
                setattr(target, key, value)
                changed = True
        if changed:
            self._changed()

    @property
    def base_dir(self) -> Path:
        """Folder that relative paths refer to: the pipeline's folder, else the current one."""
        return self.path.parent if self.path else Path.cwd()

    def relative_path(self, path: Path) -> str:
        """Express a file path relative to the pipeline folder if possible (ENG-13)."""
        try:
            return path.resolve().relative_to(self.base_dir.resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def rename_block(self, block_id: str, label: str) -> None:
        """Change a block's label."""
        block = self.pipeline.block(block_id)
        if block.label != label:
            block.label = label
            self._changed()

    # ---- connections ------------------------------------------------------------------

    def connect(self, key: ConnectionKey) -> Connection:
        """Add a connection after checking it (GUI-04).

        Raises:
            EditError: With the reason, if the connection is not allowed.
        """
        src = self.pipeline.block(key.source_block)
        dst = self.pipeline.block(key.target_block)
        if src.id == dst.id:
            raise EditError("A block cannot be connected to itself")
        out_port = next((p for p in self.ports(src)[1] if p.name == key.source_port), None)
        in_port = next((p for p in self.ports(dst)[0] if p.name == key.target_port), None)
        if out_port is None or in_port is None:
            raise EditError("Unknown port")
        if not types_compatible(out_port.type, in_port.type):
            raise EditError(
                f"Cannot connect a {out_port.type} output to a {in_port.type} input "
                f"('{in_port.display_name}' of '{dst.label or dst.type_id}')"
            )
        existing = [
            c
            for c in self.pipeline.connections
            if c.target.block == dst.id and c.target.port == in_port.name
        ]
        if any(ConnectionKey.of(c) == key for c in existing):
            raise EditError("These ports are already connected")
        if existing and not in_port.multiple:
            raise EditError(
                f"Input '{in_port.display_name}' is already connected; "
                "remove the existing connector first"
            )
        if self._creates_cycle(src.id, dst.id):
            raise EditError("This connector would create a loop")
        conn = Connection(
            source=PortRef(block=src.id, port=out_port.name),
            target=PortRef(block=dst.id, port=in_port.name),
        )
        self.pipeline.connections.append(conn)
        self._changed()
        return conn

    def disconnect(self, key: ConnectionKey) -> None:
        """Remove a connection (no error if it does not exist)."""
        before = len(self.pipeline.connections)
        self.pipeline.connections = [
            c for c in self.pipeline.connections if ConnectionKey.of(c) != key
        ]
        if len(self.pipeline.connections) != before:
            self._changed()

    def _creates_cycle(self, source: str, target: str) -> bool:
        sorter: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter()
        for c in self.pipeline.connections:
            sorter.add(c.target.block, c.source.block)
        sorter.add(target, source)
        try:
            sorter.prepare()
        except graphlib.CycleError:
            return True
        return False

    # ---- data for the canvas ----------------------------------------------------------

    def node_spec(self, block: BlockInstance) -> dict[str, Any]:
        """Describe a block for the canvas (JSON-serialisable)."""
        cls = self.block_class(block)
        inputs, outputs = self.ports(block)

        def port(p: Port) -> dict[str, Any]:
            return {
                "name": p.name,
                "label": p.display_name,
                "type": str(p.type),
                "multiple": p.multiple,
                "optional": p.optional,
            }

        return {
            "id": block.id,
            "label": block.label or (cls.title if cls else block.type_id),
            "type_title": cls.title if cls else f"Unknown: {block.type_id}",
            "category": cls.category.split("/")[0].lower() if cls else "unknown",
            "inputs": [port(p) for p in inputs],
            "outputs": [port(p) for p in outputs],
            "x": block.position[0],
            "y": block.position[1],
        }

    def graph_spec(self) -> dict[str, Any]:
        """Describe the whole pipeline for the canvas."""
        return {
            "nodes": [self.node_spec(b) for b in self.pipeline.blocks],
            "connections": [self._conn_spec(c) for c in self.pipeline.connections],
        }
