"""Graph preparation and validation (ENG-03, ENG-04, ENG-08).

:func:`prepare` turns a :class:`PipelineModel` into :class:`PreparedBlock` objects: block
class looked up, stored parameters migrated, variables resolved and parameters validated.
Every problem found is returned as a :class:`ValidationIssue` instead of raising, so that
the GUI can show all problems at once.
"""

from __future__ import annotations

import graphlib
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from apb.engine.block import AnyBlockType
from apb.engine.model import BlockInstance, Connection, PipelineModel
from apb.engine.registry import BlockRegistry
from apb.engine.types import Port, types_compatible
from apb.engine.variables import VariableError, referenced_names, resolve


@dataclass(frozen=True)
class ValidationIssue:
    """A problem in a pipeline.

    Attributes:
        message: Human-readable description.
        block_id: Block the problem belongs to (``None`` for pipeline-level problems).
        connection_id: Connection involved, if any.
    """

    message: str
    block_id: str | None = None
    connection_id: str | None = None

    def __str__(self) -> str:
        where = f"[{self.block_id}] " if self.block_id else ""
        return f"{where}{self.message}"


@dataclass
class PreparedBlock:
    """A block instance ready to run.

    Attributes:
        instance: The block instance from the pipeline.
        cls: Block class (``None`` if the type is unknown).
        resolved_params: Parameters after migration and variable resolution.
        params: Validated parameter model (``None`` if invalid).
        inputs: Input ports.
        outputs: Output ports.
    """

    instance: BlockInstance
    cls: AnyBlockType | None
    resolved_params: dict[str, Any] = field(default_factory=dict)
    params: BaseModel | None = None
    inputs: tuple[Port, ...] = ()
    outputs: tuple[Port, ...] = ()

    @property
    def id(self) -> str:
        """Block instance ID."""
        return self.instance.id

    @property
    def name(self) -> str:
        """Label for messages: the block label, or its type title."""
        if self.instance.label:
            return self.instance.label
        return self.cls.title if self.cls else self.instance.type_id

    def input_port(self, name: str) -> Port | None:
        """Return the input port called ``name``, if any."""
        return next((p for p in self.inputs if p.name == name), None)

    def output_port(self, name: str) -> Port | None:
        """Return the output port called ``name``, if any."""
        return next((p for p in self.outputs if p.name == name), None)


@dataclass
class PreparedGraph:
    """Result of :func:`prepare`.

    Attributes:
        blocks: Block ID -> prepared block, in pipeline order.
        connections: Valid connections.
        issues: All problems found.
        order: Topological execution order (empty if the graph has a cycle).
    """

    blocks: dict[str, PreparedBlock]
    connections: list[Connection]
    issues: list[ValidationIssue]
    order: list[str]

    def incoming(self, block_id: str) -> list[Connection]:
        """Return the connections ending at a block, in pipeline order."""
        return [c for c in self.connections if c.target.block == block_id]

    def upstream(self, block_id: str) -> set[str]:
        """Return the block and all blocks it depends on (directly or indirectly)."""
        result: set[str] = set()
        stack = [block_id]
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            stack.extend(c.source.block for c in self.incoming(current))
        return result

    def downstream(self, block_id: str) -> set[str]:
        """Return all blocks that depend on a block (excluding the block itself)."""
        result: set[str] = set()
        stack = [block_id]
        while stack:
            current = stack.pop()
            for c in self.connections:
                if c.source.block == current and c.target.block not in result:
                    result.add(c.target.block)
                    stack.append(c.target.block)
        return result

    def input_names(self, block_id: str) -> dict[str, list[str]]:
        """Return the value names of each ``multiple`` input port of a block.

        Duplicate names get a ``_2``, ``_3`` ... suffix.
        """
        block = self.blocks[block_id]
        names: dict[str, list[str]] = {}
        for port in block.inputs:
            if not port.multiple:
                continue
            used: list[str] = []
            for conn in self.incoming(block_id):
                if conn.target.port != port.name:
                    continue
                base = connection_name(conn, self.blocks[conn.source.block])
                name, n = base, 1
                while name in used:
                    n += 1
                    name = f"{base}_{n}"
                used.append(name)
            names[port.name] = used
        return names

    def issues_for(self, block_ids: Iterable[str]) -> list[ValidationIssue]:
        """Return the issues relevant when running ``block_ids``.

        Pipeline-level issues (e.g. cycles) are always included.
        """
        ids = set(block_ids)
        return [i for i in self.issues if i.block_id is None or i.block_id in ids]


def python_name(text: str) -> str:
    """Turn a label into a valid lower-case Python name, e.g. ``"Max load"`` -> ``max_load``."""
    name = re.sub(r"\W+", "_", text.strip().lower()).strip("_")
    if not name:
        return "item"
    return f"_{name}" if name[0].isdigit() else name


def connection_name(conn: Connection, source: PreparedBlock) -> str:
    """Return the name of a connection's value at a ``multiple`` input.

    The explicit connection name if set; else the source port name if the source block has
    several outputs (ports are descriptive, e.g. ``min_ms``); else the source block label
    (or ID) as a Python name.
    """
    if conn.name:
        return conn.name
    if len(source.outputs) > 1:
        return conn.source.port
    return python_name(source.instance.label or source.instance.id)


def _prepare_block(
    inst: BlockInstance,
    registry: BlockRegistry,
    variables: Mapping[str, Any],
    issues: list[ValidationIssue],
    overrides: Mapping[str, Any] | None = None,
) -> PreparedBlock:
    """Look up, migrate, resolve and validate one block instance."""
    if inst.type_id not in registry:
        issues.append(ValidationIssue(f"Unknown block type {inst.type_id!r}", inst.id))
        return PreparedBlock(inst, None)
    cls = registry.get(inst.type_id)
    prepared = PreparedBlock(inst, cls)
    raw = dict(inst.params)
    if inst.type_version > cls.version:
        issues.append(
            ValidationIssue(
                f"Block was saved with a newer version ({inst.type_version}) of {inst.type_id!r} "
                f"than installed ({cls.version}); update the tool",
                inst.id,
            )
        )
        return prepared
    if inst.type_version < cls.version:
        try:
            raw = cls.migrate(inst.type_version, raw)
        except Exception as exc:
            issues.append(ValidationIssue(f"Could not upgrade parameters: {exc}", inst.id))
            return prepared
    if cls.provides_variables and overrides:
        raw = cls.apply_overrides(raw, overrides)
    try:
        prepared.resolved_params = resolve(raw, variables)
    except VariableError as exc:
        issues.append(ValidationIssue(str(exc), inst.id))
        return prepared
    try:
        prepared.params = cls.Params.model_validate(prepared.resolved_params)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "params"
            issues.append(ValidationIssue(f"Parameter '{loc}': {err['msg']}", inst.id))
    prepared.inputs = cls.input_ports(prepared.params)
    prepared.outputs = cls.output_ports(prepared.params)
    return prepared


def _check_connection(
    conn: Connection, blocks: Mapping[str, PreparedBlock], issues: list[ValidationIssue]
) -> bool:
    """Check that a connection joins existing, type-compatible ports."""
    src = blocks.get(conn.source.block)
    dst = blocks.get(conn.target.block)
    target_id = conn.target.block if dst else None
    if src is None or dst is None:
        missing = conn.source.block if src is None else conn.target.block
        issues.append(
            ValidationIssue(
                f"Connection {conn.source} -> {conn.target} refers to missing block {missing!r}",
                target_id,
                conn.id,
            )
        )
        return False
    if src.cls is None or dst.cls is None:
        return False  # already reported as unknown type
    out_port = src.output_port(conn.source.port)
    in_port = dst.input_port(conn.target.port)
    if out_port is None:
        issues.append(
            ValidationIssue(
                f"Block '{src.name}' has no output port {conn.source.port!r}", dst.id, conn.id
            )
        )
        return False
    if in_port is None:
        issues.append(
            ValidationIssue(
                f"Block '{dst.name}' has no input port {conn.target.port!r}", dst.id, conn.id
            )
        )
        return False
    if not types_compatible(out_port.type, in_port.type):
        issues.append(
            ValidationIssue(
                f"Cannot connect {out_port.type} output '{src.name}.{out_port.name}' to "
                f"{in_port.type} input '{dst.name}.{in_port.name}'",
                dst.id,
                conn.id,
            )
        )
        return False
    return True


def collect_variables(
    pipeline: PipelineModel,
    registry: BlockRegistry,
    overrides: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[ValidationIssue]]:
    """Collect the variables for ``${name}``: pipeline variables, blocks, overrides.

    Blocks that provide variables (the Constants block, spec 03 §3.5.2) are read from
    their stored parameters; nothing runs. Precedence: overrides (CLI ``--set``) > block
    variables > pipeline variables.

    Args:
        pipeline: The pipeline.
        registry: Available block types.
        overrides: Values that win over everything else.

    Returns:
        ``(variables, issues)``: issues for name clashes (CON-08) and for ``${...}`` inside
        a providing block's parameters. Invalid providing blocks contribute nothing; their
        parameter errors are reported by :func:`prepare`.
    """
    issues: list[ValidationIssue] = []
    provided: dict[str, Any] = {}
    owner: dict[str, str] = {}
    for inst in pipeline.blocks:
        if inst.type_id not in registry:
            continue
        cls = registry.get(inst.type_id)
        if not cls.provides_variables:
            continue
        name = inst.label or inst.id
        refs = sorted(referenced_names(inst.params))
        if refs:
            issues.append(
                ValidationIssue(
                    f"Block '{name}' defines values for ${{name}} and cannot use ${{...}} "
                    f"itself (found ${{{refs[0]}}})",
                    inst.id,
                )
            )
            continue
        try:
            params = cls.Params.model_validate(inst.params)
        except ValidationError:
            continue
        for key, value in cls.provided_variables(params).items():
            if key in owner:
                issues.append(
                    ValidationIssue(
                        f"Constant {key!r} is defined twice: in block '{owner[key]}' and in "
                        f"block '{name}'",
                        inst.id,
                    )
                )
                continue
            if key in pipeline.variables:
                issues.append(
                    ValidationIssue(
                        f"Constant {key!r} in block '{name}' has the same name as a pipeline "
                        "variable (Pipeline > Variables and settings); rename one of them",
                        inst.id,
                    )
                )
                continue
            owner[key] = name
            provided[key] = value
    return {**pipeline.variables, **provided, **(overrides or {})}, issues


def prepare(
    pipeline: PipelineModel, registry: BlockRegistry, overrides: Mapping[str, Any] | None = None
) -> PreparedGraph:
    """Prepare and validate a pipeline.

    Args:
        pipeline: The pipeline.
        registry: Available block types.
        overrides: Variable values overriding the pipeline's variables (e.g. from the CLI).

    Returns:
        The prepared graph with all issues found.
    """
    variables, issues = collect_variables(pipeline, registry, overrides)

    blocks: dict[str, PreparedBlock] = {}
    for inst in pipeline.blocks:
        if inst.id in blocks:
            issues.append(ValidationIssue(f"Duplicate block id {inst.id!r}", inst.id))
            continue
        blocks[inst.id] = _prepare_block(inst, registry, variables, issues, overrides)

    connections: list[Connection] = []
    sources_per_input: dict[tuple[str, str], int] = defaultdict(int)
    for conn in pipeline.connections:
        if not _check_connection(conn, blocks, issues):
            continue
        dst = blocks[conn.target.block]
        port = dst.input_port(conn.target.port)
        key = (dst.id, conn.target.port)
        sources_per_input[key] += 1
        if port is not None and not port.multiple and sources_per_input[key] > 1:
            issues.append(
                ValidationIssue(
                    f"Input '{dst.name}.{port.name}' has more than one connection", dst.id, conn.id
                )
            )
            continue
        connections.append(conn)

    for b in blocks.values():
        for port in b.inputs:
            if not port.optional and sources_per_input[(b.id, port.name)] == 0:
                issues.append(
                    ValidationIssue(
                        f"Input '{port.display_name}' of '{b.name}' is not connected", b.id
                    )
                )

    sorter: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter()
    for block_id in blocks:
        sorter.add(block_id)
    for conn in connections:
        sorter.add(conn.target.block, conn.source.block)
    try:
        order = list(sorter.static_order())
    except graphlib.CycleError as exc:
        cycle = " -> ".join(blocks[i].name for i in exc.args[1])
        issues.append(ValidationIssue(f"The pipeline contains a cycle: {cycle}"))
        order = []
    return PreparedGraph(blocks, connections, issues, order)
