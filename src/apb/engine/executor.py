"""Pipeline execution with caching (ENG-04, ENG-05, ENG-09, ENG-10, ENG-11).

The executor keeps an in-memory cache per session: a block is re-run only if its cache key
changed. The key covers the block type and version, its resolved parameters, the keys of
the blocks feeding it and the size/modification time (and, for small files, the content
hash) of the files it reads.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apb.engine.block import BlockCancelledError, RunContext
from apb.engine.events import BlockState, EventKind, EventSink, RunEvent
from apb.engine.graph import PreparedBlock, PreparedGraph, ValidationIssue, prepare
from apb.engine.model import PipelineModel
from apb.engine.registry import BlockRegistry
from apb.engine.types import copy_value, value_matches

_log = logging.getLogger(__name__)

#: Files up to this size are content-hashed for the cache key; larger ones use size + mtime.
HASH_LIMIT_BYTES = 16 * 1024 * 1024


class PipelineValidationError(Exception):
    """Raised when a pipeline cannot run because of validation issues."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        """Create the error from the list of issues."""
        self.issues = issues
        super().__init__("Pipeline is not valid:\n" + "\n".join(f"  - {i}" for i in issues))


@dataclass
class BlockResult:
    """Outcome of one block in a run.

    Attributes:
        state: Final state.
        outputs: Output port -> value (empty unless ``DONE``).
        error: Error message (``ERROR`` state) or reason (``SKIPPED`` state).
        traceback: Python traceback for errors.
        log: Log lines written by the block.
        cache_key: Key the outputs belong to.
        cached: Outputs were taken from the cache.
        duration: Run time in seconds.
    """

    state: BlockState
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    traceback: str = ""
    log: list[str] = field(default_factory=list)
    cache_key: str = ""
    cached: bool = False
    duration: float = 0.0


@dataclass
class RunResult:
    """Outcome of a run.

    Attributes:
        results: Block ID -> result, for the blocks that were part of the run.
        cancelled: The run was stopped by the user.
    """

    results: dict[str, BlockResult]
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        """``True`` if every block finished without error."""
        return not self.cancelled and all(r.state is BlockState.DONE for r in self.results.values())

    def output(self, block_id: str, port: str) -> Any:
        """Return an output value of a block.

        Raises:
            KeyError: If the block did not produce that output.
        """
        return self.results[block_id].outputs[port]


def _file_stamp(path: Path) -> str:
    """Return a fingerprint of a file for the cache key."""
    try:
        stat = path.stat()
    except OSError:
        return f"{path}:missing"
    if stat.st_size <= HASH_LIMIT_BYTES:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return f"{path}:{digest}"
    return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"


class Executor:
    """Runs pipelines and caches block outputs between runs."""

    def __init__(self, registry: BlockRegistry, sink: EventSink | None = None) -> None:
        """Create an executor.

        Args:
            registry: Available block types.
            sink: Receives run events (GUI, CLI output). Exceptions in the sink are ignored.
        """
        self.registry = registry
        self._sink = sink
        self._cache: dict[str, tuple[str, dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self.cancel_event = threading.Event()

    # ---- public API ---------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Forget all cached outputs."""
        self._cache.clear()

    def stop(self) -> None:
        """Ask a running run to stop after the current block."""
        self.cancel_event.set()

    def states(
        self,
        pipeline: PipelineModel,
        pipeline_dir: Path,
        overrides: Mapping[str, Any] | None = None,
    ) -> dict[str, BlockState]:
        """Return the state of every block without running anything.

        A block is ``DONE`` if its cached outputs are up to date, ``STALE`` if it has cached
        outputs whose key changed, and ``IDLE`` if it has never run.

        Args:
            pipeline: The pipeline.
            pipeline_dir: Directory of the pipeline file.
            overrides: Variable overrides.

        Returns:
            Block ID -> state.
        """
        graph = prepare(pipeline, self.registry, overrides)
        keys = self._cache_keys(graph, graph.order, self._context_factory(pipeline, pipeline_dir))
        states: dict[str, BlockState] = {}
        for block_id in graph.blocks:
            cached = self._cache.get(block_id)
            if cached is None:
                states[block_id] = BlockState.IDLE
            elif keys.get(block_id) == cached[0]:
                states[block_id] = BlockState.DONE
            else:
                states[block_id] = BlockState.STALE
        return states

    def run(
        self,
        pipeline: PipelineModel,
        pipeline_dir: Path,
        *,
        until: str | None = None,
        overrides: Mapping[str, Any] | None = None,
        output_dir: Path | None = None,
        pipeline_file: Path | None = None,
    ) -> RunResult:
        """Run a pipeline, or only the blocks needed for block ``until``.

        Args:
            pipeline: The pipeline.
            pipeline_dir: Directory of the pipeline file (relative paths resolve here).
            until: Run only this block and its upstream blocks.
            overrides: Variable values overriding the pipeline's variables.
            output_dir: Directory for written files (default: ``pipeline_dir``).
            pipeline_file: The pipeline file, if any (reported by blocks, e.g. traceability).

        Returns:
            Results per block.

        Raises:
            PipelineValidationError: If the blocks to run have validation issues.
            KeyError: If ``until`` is not a block of the pipeline.
        """
        with self._lock:
            self.cancel_event.clear()
            graph = prepare(pipeline, self.registry, overrides)
            if until is not None and until not in graph.blocks:
                raise KeyError(f"No block with id {until!r}")
            run_set = graph.upstream(until) if until else set(graph.blocks)
            issues = graph.issues_for(run_set)
            if issues:
                raise PipelineValidationError(issues)
            order = [b for b in graph.order if b in run_set]
            make_ctx = self._context_factory(pipeline, pipeline_dir, output_dir)
            make_ctx.pipeline_file = pipeline_file
            keys = self._cache_keys(graph, order, make_ctx)

            self._emit(RunEvent(EventKind.RUN_STARTED, message=f"{len(order)} block(s)"))
            results: dict[str, BlockResult] = {}
            for block_id in order:
                if self.cancel_event.is_set():
                    results[block_id] = self._skip(block_id, "Run stopped by user")
                    continue
                results[block_id] = self._run_block(
                    graph, graph.blocks[block_id], keys[block_id], results, make_ctx
                )
            run = RunResult(results, cancelled=self.cancel_event.is_set())
            failed = sum(r.state is BlockState.ERROR for r in results.values())
            summary = "stopped" if run.cancelled else (f"{failed} failed" if failed else "ok")
            self._emit(RunEvent(EventKind.RUN_FINISHED, message=summary))
            return run

    # ---- internals ----------------------------------------------------------------------

    def _emit(self, event: RunEvent) -> None:
        """Send an event to the sink, never letting the sink break the run."""
        if self._sink is None:
            return
        try:
            self._sink(event)
        except Exception:  # a broken GUI callback must not stop the run
            _log.exception("Event sink failed for %s event", event.kind)

    def _context_factory(
        self, pipeline: PipelineModel, pipeline_dir: Path, output_dir: Path | None = None
    ) -> _ContextFactory:
        """Return a function creating the run context for a block."""
        return _ContextFactory(
            self, pipeline, pipeline_dir.resolve(), (output_dir or pipeline_dir).resolve()
        )

    def _cache_keys(
        self, graph: PreparedGraph, order: list[str], make_ctx: _ContextFactory
    ) -> dict[str, str]:
        """Compute the cache key of each block in ``order`` (upstream blocks first)."""
        keys: dict[str, str] = {}
        for block_id in order:
            block = graph.blocks[block_id]
            if block.cls is None or block.params is None:
                continue
            upstream = [
                [c.target.port, c.source.port, c.name, keys.get(c.source.block, "")]
                for c in graph.incoming(block_id)
            ]
            try:
                files = block.cls().input_files(block.params, make_ctx(block_id))
                make_ctx.block_files[block_id] = list(files)
                stamps = sorted(_file_stamp(f) for f in files)
            except Exception as exc:  # error surfaces again when the block runs
                stamps = [f"error:{exc}"]
            payload = json.dumps(
                [
                    block.cls.type_id,
                    block.cls.version,
                    block.params.model_dump(mode="json"),
                    upstream,
                    stamps,
                ],
                sort_keys=True,
                default=str,
            )
            keys[block_id] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return keys

    def _skip(self, block_id: str, reason: str) -> BlockResult:
        """Mark a block as skipped."""
        self._emit(RunEvent(EventKind.BLOCK_SKIPPED, block_id, reason))
        return BlockResult(BlockState.SKIPPED, error=reason)

    def _gather_inputs(
        self, graph: PreparedGraph, block: PreparedBlock, results: Mapping[str, BlockResult]
    ) -> dict[str, Any]:
        """Collect (copies of) the input values of a block from upstream results."""
        values: dict[str, Any] = {p.name: ([] if p.multiple else None) for p in block.inputs}
        for conn in graph.incoming(block.id):
            value = copy_value(results[conn.source.block].outputs[conn.source.port])
            port = block.input_port(conn.target.port)
            if port is not None and port.multiple:
                values[port.name].append(value)
            else:
                values[conn.target.port] = value
        return values

    def _run_block(
        self,
        graph: PreparedGraph,
        block: PreparedBlock,
        key: str,
        results: Mapping[str, BlockResult],
        make_ctx: _ContextFactory,
    ) -> BlockResult:
        """Run one block (or take it from the cache) and return its result."""
        assert block.cls is not None and block.params is not None  # ensured by validation
        failed_upstream = [
            graph.blocks[c.source.block].name
            for c in graph.incoming(block.id)
            if results[c.source.block].state is not BlockState.DONE
        ]
        if failed_upstream:
            return self._skip(block.id, f"Upstream block(s) not done: {', '.join(failed_upstream)}")

        cached = self._cache.get(block.id)
        if cached is not None and cached[0] == key and not block.cls.always_run:
            self._emit(RunEvent(EventKind.BLOCK_CACHED, block.id, "Up to date (cached)"))
            return BlockResult(BlockState.DONE, outputs=cached[1], cache_key=key, cached=True)

        log: list[str] = []
        ctx = make_ctx(block.id, log)
        ctx.input_names = graph.input_names(block.id)
        ctx.upstream_files = sorted(
            {f for b in graph.upstream(block.id) for f in make_ctx.block_files.get(b, [])}
        )
        self._emit(RunEvent(EventKind.BLOCK_STARTED, block.id, block.name))
        start = time.perf_counter()
        try:
            outputs = block.cls().run(block.params, self._gather_inputs(graph, block, results), ctx)
            self._check_outputs(block, outputs)
        except BlockCancelledError as exc:
            self._cache.pop(block.id, None)
            return BlockResult(BlockState.SKIPPED, error=str(exc), log=log)
        except Exception as exc:
            self._cache.pop(block.id, None)
            tb = traceback.format_exc()
            message = f"{type(exc).__name__}: {exc}"
            self._emit(RunEvent(EventKind.BLOCK_FAILED, block.id, message, detail=tb))
            return BlockResult(
                BlockState.ERROR,
                error=message,
                traceback=tb,
                log=log,
                duration=time.perf_counter() - start,
            )
        duration = time.perf_counter() - start
        self._cache[block.id] = (key, outputs)
        self._emit(RunEvent(EventKind.BLOCK_FINISHED, block.id, f"Done in {duration:.2f} s"))
        return BlockResult(
            BlockState.DONE, outputs=outputs, log=log, cache_key=key, duration=duration
        )

    @staticmethod
    def _check_outputs(block: PreparedBlock, outputs: Any) -> None:
        """Check that a block returned exactly its declared outputs with matching types.

        Raises:
            TypeError: On missing, unexpected or wrongly typed outputs.
        """
        if not isinstance(outputs, dict):
            raise TypeError(f"Block must return a dict of outputs, got {type(outputs).__name__}")
        declared = {p.name: p for p in block.outputs}
        missing = declared.keys() - outputs.keys()
        extra = outputs.keys() - declared.keys()
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"missing outputs {sorted(missing)}")
            if extra:
                parts.append(f"unexpected outputs {sorted(extra)}")
            raise TypeError("Block returned " + " and ".join(parts))
        for name, port in declared.items():
            if not value_matches(outputs[name], port.type):
                raise TypeError(
                    f"Output '{name}' must be {port.type}, got {type(outputs[name]).__name__}"
                )


class _ContextFactory:
    """Creates :class:`RunContext` objects wired to an executor's event sink."""

    def __init__(
        self, executor: Executor, pipeline: PipelineModel, pipeline_dir: Path, output_dir: Path
    ) -> None:
        self._executor = executor
        self._pipeline_dir = pipeline_dir
        self._output_dir = output_dir
        self._unit_system = pipeline.settings.default_unit_system
        self.pipeline_file: Path | None = None
        self.block_files: dict[str, list[Path]] = {}

    def __call__(self, block_id: str, log: list[str] | None = None) -> RunContext:
        """Return a context for ``block_id``; log lines are also appended to ``log``."""
        executor = self._executor

        def on_log(message: str) -> None:
            if log is not None:
                log.append(message)
            executor._emit(RunEvent(EventKind.LOG, block_id, message))

        def on_progress(fraction: float) -> None:
            executor._emit(RunEvent(EventKind.PROGRESS, block_id, progress=fraction))

        return RunContext(
            block_id=block_id,
            pipeline_dir=self._pipeline_dir,
            output_dir=self._output_dir,
            default_unit_system=self._unit_system,
            log_callback=on_log,
            progress_callback=on_progress,
            cancel_event=executor.cancel_event,
            pipeline_file=self.pipeline_file,
        )
