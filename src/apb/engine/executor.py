"""Pipeline execution with caching (ENG-04, ENG-05, ENG-09, ENG-10, ENG-11).

The executor keeps an in-memory cache per session: a block is re-run only if its cache key
changed. The key covers the block type and version, its resolved parameters, the keys of
the blocks feeding it and the size/modification time (and, for small files, the content
hash) of the files it reads. With an :class:`~apb.engine.store.OutputStore` attached the
outputs are also written to disk and found again when the pipeline is reopened (spec 08).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import traceback
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apb.engine.block import BlockCancelledError, RunContext
from apb.engine.events import BlockState, EventKind, EventSink, RunEvent
from apb.engine.graph import PreparedBlock, PreparedGraph, ValidationIssue, prepare
from apb.engine.model import Connection, PipelineModel
from apb.engine.registry import BlockRegistry
from apb.engine.store import (
    OutputStore,
    StoredEntry,
    StoredOutputs,
    StoreError,
    StoreUnavailableError,
    now_text,
)
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
        restored: Outputs come from the stored outputs of an earlier session.
        finished: When the block finished (ISO 8601), if known.
    """

    state: BlockState
    outputs: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""
    traceback: str = ""
    log: list[str] = field(default_factory=list)
    cache_key: str = ""
    cached: bool = False
    duration: float = 0.0
    restored: bool = False
    finished: str = ""


@dataclass
class _Cached:
    """One block's outputs in the in-memory cache."""

    key: str
    outputs: Mapping[str, Any]
    log: list[str]
    duration: float
    finished: str
    written: list[str] = field(default_factory=list)
    restored: bool = False


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


#: Files changed less than this long ago (ns) are always re-hashed: a second change could keep
#: the same size and the same, coarse, modification time ("racy" timestamps).
_RACY_NS = 2_000_000_000

_stamp_cache: dict[str, tuple[int, int, str]] = {}
"""path -> (size, mtime_ns, digest): the content hash of files that did not change."""


def _file_stamp(path: Path) -> str:
    """Return a fingerprint of a file for the cache key.

    Small files are hashed (the hash is reused while size and modification time are the
    same, so refreshing the states twice a second does not read every file twice a second);
    large files use size and modification time.
    """
    try:
        stat = path.stat()
    except OSError:
        return f"{path}:missing"
    if stat.st_size > HASH_LIMIT_BYTES:
        return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
    key = str(path)
    known = _stamp_cache.get(key)
    if known is not None and known[:2] == (stat.st_size, stat.st_mtime_ns):
        return f"{path}:{known[2]}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if time.time_ns() - stat.st_mtime_ns > _RACY_NS:
        if len(_stamp_cache) > 2000:
            _stamp_cache.clear()
        _stamp_cache[key] = (stat.st_size, stat.st_mtime_ns, digest)
    return f"{path}:{digest}"


def _recorded(base: Path, path: Path) -> str:
    """Return a written file as stored: relative to the pipeline folder if inside it."""
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _files_exist(base: Path, recorded: Collection[str]) -> bool:
    """Return whether all recorded written files still exist."""
    return all((base / name).exists() for name in recorded)


class Executor:
    """Runs pipelines and caches block outputs between runs."""

    def __init__(
        self,
        registry: BlockRegistry,
        sink: EventSink | None = None,
        store: OutputStore | None = None,
        *,
        use_stored: bool = True,
    ) -> None:
        """Create an executor.

        Args:
            registry: Available block types.
            sink: Receives run events (GUI, CLI output). Exceptions in the sink are ignored.
            store: Where outputs are kept on disk (``None`` = memory only).
            use_stored: Take up-to-date outputs from the store; with ``False`` everything is
                recomputed, and the new outputs are still written (``--no-cache``).
        """
        self.registry = registry
        self._sink = sink
        self._cache: dict[str, _Cached] = {}
        self._lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.store: OutputStore | None = None
        self.use_stored = use_stored
        self.store_problems: dict[str, str] = {}
        """Entries of the store that could not be read: block id -> reason."""
        self.store_warning = ""
        """Set once if the store folder cannot be written (outputs stay in memory)."""
        self._entries: dict[str, StoredEntry] | None = None
        self._store_off = False
        self.attach_store(store)

    # ---- public API ---------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Forget all outputs kept in memory (the stored outputs are not touched)."""
        self._cache.clear()

    def attach_store(self, store: OutputStore | None) -> None:
        """Use another output store (``None`` = memory only); entries are read again."""
        self.store = store
        self._entries = None
        self._store_off = False
        self.store_problems = {}
        self.store_warning = ""

    def index_problems(self) -> dict[str, str]:
        """Return the stored entries that could not be read (block ID -> reason)."""
        self._index()
        return dict(self.store_problems)

    def stored_entry(self, block_id: str) -> StoredEntry | None:
        """Return the stored entry of a block, if there is a readable one."""
        return self._index().get(block_id)

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

        A block is ``DONE`` if its outputs (in memory or stored) are up to date, ``STALE`` if
        it has outputs whose key changed, and ``IDLE`` if it has none. A block that writes
        files is only ``DONE`` while those files exist.

        Args:
            pipeline: The pipeline.
            pipeline_dir: Directory of the pipeline file.
            overrides: Variable overrides.

        Returns:
            Block ID -> state.
        """
        graph = prepare(pipeline, self.registry, overrides)
        keys = self._cache_keys(graph, graph.order, self._context_factory(pipeline, pipeline_dir))
        index = self._index()
        base = pipeline_dir.resolve()
        states: dict[str, BlockState] = {}
        for block_id in graph.blocks:
            key = keys.get(block_id)
            cached = self._cache.get(block_id)
            entry = index.get(block_id)
            if cached is not None:
                fresh = key == cached.key and _files_exist(base, cached.written)
            elif entry is not None:
                fresh = (
                    entry.usable
                    and key == entry.cache_key
                    and _files_exist(base, entry.written_files)
                )
            else:
                states[block_id] = BlockState.IDLE
                continue
            states[block_id] = BlockState.DONE if fresh else BlockState.STALE
        return states

    def restore(self, pipeline: PipelineModel) -> dict[str, BlockResult]:
        """Return the stored results of the pipeline's blocks, to show them without running.

        The outputs are read from disk when first used. Entries made by another tool
        version are left out. See also :attr:`store_problems`.

        Args:
            pipeline: The pipeline.

        Returns:
            Block ID -> result (``restored``), for every block with a usable stored entry.
        """
        index = self._index()
        results: dict[str, BlockResult] = {}
        for block in pipeline.blocks:
            entry = index.get(block.id)
            if entry is not None and entry.usable:
                results[block.id] = BlockResult(
                    BlockState.DONE,
                    outputs=StoredOutputs(entry),
                    log=list(entry.log),
                    cache_key=entry.cache_key,
                    cached=True,
                    duration=entry.duration,
                    restored=True,
                    finished=entry.finished,
                )
        return results

    def persist(self, pipeline: PipelineModel, pipeline_dir: Path) -> int:
        """Write the up-to-date in-memory outputs to the store (first save of a pipeline).

        Also removes the entries of blocks that are no longer in the pipeline (STO-13).

        Args:
            pipeline: The pipeline.
            pipeline_dir: Its directory.

        Returns:
            The number of entries written.
        """
        if self.store is None or self._store_off:
            return 0
        graph = prepare(pipeline, self.registry)
        keys = self._cache_keys(graph, graph.order, self._context_factory(pipeline, pipeline_dir))
        written = 0
        for block_id, cached in list(self._cache.items()):
            block = graph.blocks.get(block_id)
            entry = self._index().get(block_id)
            if (
                block is None
                or block.cls is None
                or keys.get(block_id) != cached.key
                or (entry is not None and entry.usable and entry.cache_key == cached.key)
            ):
                continue
            if self._write_entry(block_id, block.cls.type_id, block.cls.version, cached, None):
                written += 1
        try:
            for block_id in self.store.orphans(set(graph.blocks)):
                self.store.clear([block_id])
                self._index().pop(block_id, None)
        except StoreError as exc:
            _log.warning("Orphaned entries not removed: %s", exc)
        return written

    def clear_outputs(
        self, pipeline: PipelineModel, block_ids: Collection[str] | None = None
    ) -> list[str]:
        """Forget stored and in-memory outputs: everything, or blocks and what is downstream.

        Args:
            pipeline: The pipeline.
            block_ids: Blocks to clear together with all blocks downstream of them;
                ``None`` clears everything.

        Returns:
            The IDs of the blocks that had outputs.

        Raises:
            StoreError: If stored data cannot be removed.
        """
        with self._lock:
            if block_ids is None:
                cleared = set(self._cache) | set(self._index())
                self._cache.clear()
                if self.store is not None:
                    cleared |= set(self.store.clear())
            else:
                graph = prepare(pipeline, self.registry)
                targets = set(block_ids)
                for block_id in block_ids:
                    if block_id in graph.blocks:
                        targets |= graph.downstream(block_id)
                cleared = {b for b in targets if b in self._cache or b in self._index()}
                for block_id in targets:
                    self._cache.pop(block_id, None)
                if self.store is not None:
                    cleared |= set(self.store.clear(targets))
            self._entries = None
            return sorted(cleared)

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
            keys: dict[str, str] = {}

            self._emit(RunEvent(EventKind.RUN_STARTED, message=f"{len(order)} block(s)"))
            results: dict[str, BlockResult] = {}
            for block_id in order:
                if self.cancel_event.is_set():
                    results[block_id] = self._skip(block_id, "Run stopped by user")
                    continue
                # Computed now, not at the start of the run: an upstream block that ran
                # in this run may have delivered new files (ENG-19).
                key = self._key_for(graph, block_id, keys, make_ctx)
                assert key is not None  # the blocks to run were validated
                keys[block_id] = key
                results[block_id] = self._run_block(
                    graph, graph.blocks[block_id], key, results, make_ctx
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
            key = self._key_for(graph, block_id, keys, make_ctx)
            if key is not None:
                keys[block_id] = key
        return keys

    def _delivered(self, block_id: str) -> list[str]:
        """Return the files a block delivered last time (written or handed on), as recorded."""
        cached = self._cache.get(block_id)
        if cached is not None:
            return cached.written
        entry = self._index().get(block_id)
        return list(entry.written_files) if entry is not None else []

    def _key_for(
        self,
        graph: PreparedGraph,
        block_id: str,
        keys: Mapping[str, str],
        make_ctx: _ContextFactory,
    ) -> str | None:
        """Compute the cache key of one block, given the keys of the blocks upstream of it.

        The key covers the block type and version, its parameters, the keys of its upstream
        blocks, the files it reads and the files its upstream blocks *deliver* (ENG-19), so
        that e.g. a rewritten result file makes the blocks that read it stale.

        Returns:
            The key, or ``None`` for a block whose type or parameters are not valid.
        """
        block = graph.blocks[block_id]
        if block.cls is None or block.params is None:
            return None
        incoming = graph.incoming(block_id)
        upstream = [
            [c.target.port, c.source.port, c.name, self._source_key(graph, c, keys)]
            for c in incoming
        ]
        base = make_ctx.pipeline_dir
        delivered = sorted(
            {_file_stamp(base / name) for c in incoming for name in self._delivered(c.source.block)}
        )
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
                delivered,
            ],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _source_key(graph: PreparedGraph, conn: Connection, keys: Mapping[str, str]) -> str:
        """Key of the value on a connector: the port's own fingerprint, else the block's key."""
        source = graph.blocks[conn.source.block]
        if source.cls is not None and source.params is not None:
            own = source.cls.port_fingerprint(source.params, conn.source.port)
            if own is not None and conn.source.block in keys:
                return f"port:{own}"
        return keys.get(conn.source.block, "")

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

        base = make_ctx.pipeline_dir
        cached = self._cache.get(block.id)
        always = block.cls.always_runs(block.params)
        if cached is not None and cached.key == key and not always:
            self._emit(RunEvent(EventKind.BLOCK_CACHED, block.id, "Up to date (cached)"))
            return self._from_cache(cached, key)
        entry = self._index().get(block.id) if self.use_stored else None
        if (
            entry is not None
            and entry.usable
            and entry.cache_key == key
            and not always
            and _files_exist(base, entry.written_files)
        ):
            cached = _Cached(
                key,
                StoredOutputs(entry),
                list(entry.log),
                entry.duration,
                entry.finished,
                list(entry.written_files),
                restored=True,
            )
            self._cache[block.id] = cached
            self._emit(
                RunEvent(EventKind.BLOCK_CACHED, block.id, f"Up to date (stored {entry.finished})")
            )
            return self._from_cache(cached, key)

        log: list[str] = []
        ctx = make_ctx(block.id, log)
        ctx.input_names = graph.input_names(block.id)
        upstream = graph.upstream(block.id)
        ctx.upstream_files = sorted(
            {f for b in upstream for f in make_ctx.block_files.get(b, [])}
            | {base / name for b in upstream for name in self._delivered(b)}
        )  # files read upstream and files delivered upstream (e.g. Nastran results)
        self._emit(RunEvent(EventKind.BLOCK_STARTED, block.id, block.name))
        start = time.perf_counter()
        try:
            outputs = block.cls().run(block.params, self._gather_inputs(graph, block, results), ctx)
            self._check_outputs(block, outputs)
        except BlockCancelledError as exc:
            self._forget(block.id)
            return BlockResult(BlockState.SKIPPED, error=str(exc), log=log)
        except Exception as exc:
            self._forget(block.id)
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
        finished = now_text()
        try:
            files = block.cls().output_files(block.params, outputs, ctx)
        except Exception:  # a broken hook must not fail a block that did its work
            files = []
        cached = _Cached(key, outputs, log, duration, finished, [_recorded(base, f) for f in files])
        self._cache[block.id] = cached
        self._store_entry(block, cached, log)
        self._emit(RunEvent(EventKind.BLOCK_FINISHED, block.id, f"Done in {duration:.2f} s"))
        return BlockResult(
            BlockState.DONE,
            outputs=outputs,
            log=log,
            cache_key=key,
            duration=duration,
            finished=finished,
        )

    @staticmethod
    def _from_cache(cached: _Cached, key: str) -> BlockResult:
        """Return the result of a block taken from the cache."""
        return BlockResult(
            BlockState.DONE,
            outputs=cached.outputs,
            log=list(cached.log),
            cache_key=key,
            cached=True,
            duration=cached.duration,
            restored=cached.restored,
            finished=cached.finished,
        )

    # ---- stored outputs -----------------------------------------------------------------

    def _index(self) -> dict[str, StoredEntry]:
        """Return the stored entries by block ID (read once per attached store)."""
        if self.store is None:
            return {}
        if self._entries is None:
            self._entries, self.store_problems = self.store.entries()
        return self._entries

    def _forget(self, block_id: str) -> None:
        """Drop the outputs of a block that failed or was stopped, in memory and on disk."""
        self._cache.pop(block_id, None)
        entries = self._index()
        if self.store is not None and block_id in entries:
            del entries[block_id]
            try:
                self.store.clear([block_id])
            except StoreError as exc:
                _log.warning("Stored output of %s not removed: %s", block_id, exc)

    def _store_entry(self, block: PreparedBlock, cached: _Cached, log: list[str]) -> None:
        """Write the outputs of a finished block; problems are noted, never raised."""
        assert block.cls is not None
        if self.store is None or self._store_off:
            return
        self._write_entry(block.id, block.cls.type_id, block.cls.version, cached, log)

    def _write_entry(
        self,
        block_id: str,
        type_id: str,
        type_version: int,
        cached: _Cached,
        log: list[str] | None,
    ) -> bool:
        """Write one entry; returns whether it was written."""
        assert self.store is not None
        try:
            entry = self.store.save(
                block_id,
                type_id=type_id,
                type_version=type_version,
                cache_key=cached.key,
                outputs=cached.outputs,
                log=cached.log,
                duration=cached.duration,
                written_files=cached.written,
                finished=cached.finished,
            )
        except StoreUnavailableError as exc:
            self._store_off = True
            self.store_warning = (
                f"Results cannot be stored next to the pipeline: {exc}. "
                "They are kept in memory only"
            )
            self._emit(RunEvent(EventKind.LOG, None, self.store_warning))
            return False
        except StoreError as exc:
            note = f"Outputs not stored: {exc}"
            if log is not None:
                log.append(note)
            self._emit(RunEvent(EventKind.LOG, block_id, note))
            self._index().pop(block_id, None)  # an older entry would no longer be current
            return False
        self._index()[block_id] = entry
        return True

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

    @property
    def pipeline_dir(self) -> Path:
        """Directory of the pipeline file."""
        return self._pipeline_dir

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
