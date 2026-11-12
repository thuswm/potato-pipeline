# 01 — Pipeline Engine & File Format

## 1. Purpose

The engine is the GUI-independent core: it defines what a block is, how blocks are connected,
how a pipeline is executed, cached and saved.

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| ENG-01 | A pipeline is a directed acyclic graph (DAG) of block instances and connections | M |
| ENG-02 | Each block type declares typed input ports, output ports and a parameter model | M |
| ENG-03 | Connections are validated: existing ports, compatible types, one source per input, no cycles | M |
| ENG-04 | Execution order is a topological sort; a block runs only after all its inputs are available | M |
| ENG-05 | Errors in a block are captured (message + traceback) and attached to that block; downstream blocks are marked *skipped*; independent branches still run | M |
| ENG-06 | Each block has a state: `idle`, `stale`, `running`, `done`, `error`, `skipped` | M |
| ENG-07 | Pipelines are saved/loaded as JSON (`*.apb.json`) with a schema version | M |
| ENG-08 | Block types are versioned; loading a pipeline with an older block version calls an optional `migrate()` hook | S |
| ENG-09 | Caching: a block is re-run only if its parameters, its block version, its upstream outputs, or its referenced input files changed | S |
| ENG-10 | Run the whole pipeline, or "run up to block X" | M |
| ENG-11 | Progress/log events are emitted (used by GUI and CLI) | M |
| ENG-12 | Block registry discovers built-in blocks and blocks from installed packages via Python entry points (`apb.blocks`) | S |
| ENG-13 | Relative file paths in a pipeline are resolved relative to the pipeline file | M |
| ENG-14 | Pipeline-level **variables** (e.g. `h5_file`, `allowable`) that block parameters can reference as `${name}`; overridable from the CLI | S |
| ENG-15 | Pipeline setting `default_unit_system` (`SI` / `SI_mm`), used as default by input blocks (spec 00 §5.1) | M |
| ENG-16 | Macro blocks (sub-pipeline as reusable block) | L |
| ENG-17 | Blocks that write files (Excel, report) are marked `always_run` and bypass the cache, so a deleted or edited output file is always rewritten | M |

## 3. Design

### 3.1 Block definition

```python
class Block(ABC):
    """Base class of all block types."""

    type_id: ClassVar[str]  # globally unique, e.g. "nastran.hdf5_results"
    version: ClassVar[int] = 1
    category: ClassVar[str]  # library grouping, e.g. "Input/Nastran"
    title: ClassVar[str]  # shown in GUI
    inputs: ClassVar[list[Port]]
    outputs: ClassVar[list[Port]]
    Params: ClassVar[type[BaseModel]]  # Pydantic model of the block's settings

    def run(self, params: BaseModel, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """Compute outputs from inputs. Must be deterministic and side-effect free,
        except for writing files declared via ctx."""

    def input_files(self, params: BaseModel) -> list[Path]:
        """Files read by the block (used for cache invalidation). Default: none."""

    @classmethod
    def migrate(cls, old_version: int, params: dict[str, Any]) -> dict[str, Any]:
        """Upgrade stored params from an older block version."""
```

```python
@dataclass(frozen=True)
class Port:
    name: str  # identifier, unique within block side
    type: PortType  # Table, Scalar, Text, Figure, Any
    label: str = ""
    optional: bool = False  # optional inputs may be unconnected
    multiple: bool = False  # input accepts several connections (gets a list), e.g. report items
```

`RunContext` gives the block: pipeline directory, an output directory, a logger, and a
progress callback.

### 3.2 Graph model

- `PipelineModel` (Pydantic): `schema_version`, `settings`, `variables`, `blocks`, `connections`, `metadata`.
- `BlockInstance`: `id` (short uuid), `type_id`, `type_version`, `label`, `params` (dict),
  `position` (x, y for the GUI), `notes`.
- `Connection`: `id`, `source: {block, port}`, `target: {block, port}`.

### 3.3 Execution

1. Validate the graph (ENG-03); fail fast with a list of problems.
2. Topologically sort (`graphlib.TopologicalSorter` from the standard library).
3. For each block: resolve `${variables}`, validate params through the block's `Params`
   model, gather inputs, compute cache key, run or load from cache, store outputs.
4. Emit events: `block_started`, `block_finished`, `block_failed`, `block_skipped`, `log`.

v1 runs blocks sequentially in a worker thread (GUI stays responsive). Parallel execution is
*later*.

### 3.4 Caching (ENG-09)

Cache key = SHA-256 of: `type_id`, `version`, canonical JSON of params, cache keys of upstream
outputs, and for each `input_files()` entry its size + modification time (+ content hash if
small). v1 cache is **in-memory** per session; the GUI marks blocks `stale` when their key
changes. A persistent on-disk cache is *later*.

### 3.5 File format (`*.apb.json`)

```json
{
  "schema_version": 1,
  "metadata": {"title": "Wing rib stress check", "author": "", "created": "2026-11-11"},
  "settings": {"default_unit_system": "SI_mm"},
  "variables": {"h5_file": "results/rib_v3.h5", "allowable_MPa": 410.0},
  "blocks": [
    {"id": "b1", "type_id": "nastran.hdf5_results", "type_version": 1,
     "label": "QUAD4 stresses", "position": [100, 120],
     "params": {"file": "${h5_file}", "result": "STRESS/QUAD4", "subcases": []}},
    {"id": "b2", "type_id": "table.envelope", "type_version": 1, "label": "Max vM",
     "position": [380, 120], "params": {"group_by": ["EID"], "column": "VON_MISES", "mode": "max"}}
  ],
  "connections": [
    {"id": "c1", "source": {"block": "b1", "port": "table"},
     "target": {"block": "b2", "port": "table"}}
  ]
}
```

JSON is chosen over YAML: native in browser and Python stdlib, unambiguous, diff-friendly when
pretty-printed with a fixed key order.

### 3.6 Command line (Typer)

```
apb gui [pipeline.apb.json]            # start the GUI and open the browser
apb run pipeline.apb.json [--set h5_file=new.h5] [--until b3] [--verbose]
apb validate pipeline.apb.json
apb blocks                              # list available block types
```

Exit code ≠ 0 if any block fails.

## 4. Out of scope (v1)

Parallel execution, persistent disk cache, loops/conditionals in the graph, macro blocks
(see below).

**Macro blocks (later, foreseen):** a sub-pipeline saved as a reusable block, so the standard
library can grow without writing Python. Not in v1, but v1 must not block it: block instances
are addressed only through `type_id` + ports, and the file format reserves a top-level
`"macros": []` key (ignored in v1).

## 5. Open questions

None.
