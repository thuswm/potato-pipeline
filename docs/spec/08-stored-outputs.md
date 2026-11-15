# 08 — Stored Outputs

## 1. Purpose

Today a pipeline "forgets" everything when the tool is closed: reopening it shows every
block as never run, and the whole chain, including the slow blocks, has to be run again before a
table can be looked at. The analyst's work is interactive (run the analysis, look into the
results, pick one, build on it), so the results must survive closing and reopening.

This step stores the **outputs of every finished block on disk**, next to the pipeline.
When a pipeline is opened the tool compares the stored outputs with the current pipeline,
shows each block as *done*, *stale* or *idle*, lets the analyst look at the stored results
at once, and runs only what changed. A function clears the stored outputs.

It extends the in-memory cache of spec 01 §3.4, which stays as it is: same cache key,
same rules for when a block counts as up to date.

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| STO-01 | The outputs of every block that finished (`done`) are written to disk, next to the pipeline file, as soon as the block finishes | M |
| STO-02 | Tables are stored as **Parquet** (`pyarrow`), scalars and texts as JSON, figures as PNG. **No pickle**: stored outputs are data, never code | M |
| STO-03 | When a pipeline is opened, every block with stored outputs is shown as `done` (its key is unchanged) or `stale` (its key changed); blocks without are `idle`. Nothing is run and the input files are only fingerprinted | M |
| STO-04 | The preview of a `done` or `stale` block shows its stored outputs, loaded when first needed (opening a pipeline does not read the data files) | M |
| STO-05 | *Run* takes the outputs of up-to-date blocks from the store (`cached`) and runs the others; the result is the same as a run from scratch | M |
| STO-06 | A stored entry counts as up to date only if its cache key is equal, the tool version is equal and the store schema is known. For blocks that write files (`always_run`), the files they wrote must still exist | M |
| STO-07 | Stored tables are restored **exactly**: values, dtypes, column names (also two-level, duplicate or non-text names), index, and the metadata in `attrs` (units, title, source file, Nastran version …) | M |
| STO-08 | **Clear stored outputs:** everything, or one block together with all blocks downstream of it; in the GUI with a confirmation that names the folder and its size, on the command line as `apb clear-outputs` | M |
| STO-09 | A broken, partial or unknown entry is ignored (the block counts as `idle`) with one line in the log; it never stops the tool or a run. Entries are written atomically, so a crash cannot leave a half-written entry that looks valid | M |
| STO-10 | A value that cannot be stored (unknown type, too exotic for the formats) is **not** stored; the block still works from memory and the log says why. The run is not affected | M |
| STO-11 | The store lives in a hidden folder next to the pipeline and ignores itself in Git (it contains its own `.gitignore`); a folder that cannot be written (read-only) gives one warning, then the tool works in memory only | M |
| STO-12 | An unsaved pipeline has no folder: outputs stay in memory. At the first *Save* or *Save as* the outputs of the blocks that are `done` are written to the store | M |
| STO-13 | Entries of blocks that no longer exist in the pipeline (deleted, other ID) are removed when the pipeline is saved | S |
| STO-14 | `apb run` uses the store like the GUI (a second run of an unchanged pipeline does nothing except the file-writing blocks); `--no-cache` ignores stored outputs and recomputes everything | S |
| STO-15 | `apb outputs <pipeline>` lists the stored entries: block, state, time, size | S |
| STO-16 | A central or shared store for several users, a size limit with automatic clean-up, locking between several running tools, exporting results as a bundle | L |

## 3. Design

### 3.1 Where the store is

```
examples/
  test_case.apb.json
  .apb/                         <- hidden; created on the first write
    .gitignore                  <- contains "*": the store is never committed
    test_case/                  <- one store per pipeline file (name without .apb.json)
      global_margins/           <- one entry per block, named by block ID
        meta.json
        margins.parquet
      f1/
        meta.json
      ...
```

Next to the pipeline, because the stored results belong to that pipeline and travel with
its folder (copy, zip, move). Two pipelines in one folder get two stores; *Save as* starts a
new store for the new file name (the outputs of the open blocks are written to it, STO-12).
The file dialogs do not list hidden folders. The folder is created only when something is
written; a pipeline that was never run leaves no trace.

If the folder cannot be created or written, the tool shows one warning ("Results cannot be
stored next to the pipeline: *reason*. They are kept in memory only") and goes on without
a store, as for an unsaved pipeline.

### 3.2 One entry

`meta.json`, written last, says what the entry is; the data files sit next to it.

```json
{
  "store_schema": 1,
  "tool_version": "0.1.0.dev0",
  "block_id": "global_margins",
  "type_id": "python.script",
  "type_version": 1,
  "cache_key": "9f2c…",
  "finished": "2026-11-15T17:08:12+01:00",
  "duration": 0.19,
  "log": ["Minimum margin 0.467 at element 17, …"],
  "written_files": [{"path": "output/test_case_report.docx"}],
  "outputs": {
    "margins": {"kind": "table", "file": "margins.parquet", "rows": 6, "…": "…"},
    "min_ms":  {"kind": "scalar", "type": "float", "value": 0.4666667, "unit": ""},
    "plot":    {"kind": "figure", "file": "plot.png"}
  }
}
```

- `cache_key` is the key of spec 01 §3.4: block type and version, resolved parameters,
  keys of the upstream blocks, fingerprints of the input files (content hash for files up
  to 16 MB, size and modification time for larger ones).
- `log` and `duration` are restored too, so the block panel and the preview look as they
  did after the run.
- Entries are written into a temporary folder and moved into place when complete, the old
  entry being removed first, so a crash leaves either the old entry, the new one, or no
  entry — never a mixture. Leftover temporary folders are removed on the next write.

### 3.3 Formats (STO-02, STO-07, STO-10)

The kind of a stored value follows its **runtime type**, not the declared port type (a
*Convert units* block may return a table or a scalar on the same port).

| Value | Stored as | Restored as |
|-------|-----------|-------------|
| Table (`DataFrame`) | `<port>.parquet` + description in `meta.json` (below) | the same table |
| Number (`int`, `float`, also NaN and ±inf) | JSON: `type`, `value` (special values as the strings `"nan"`, `"inf"`, `"-inf"`) | `int` or `float` |
| Number with unit (`Quantity`) | the same plus `unit` | `Quantity` |
| Text | JSON string (any Unicode) | `str` |
| Figure (matplotlib) | `<port>.png`, rendered at 300 dpi | `StoredFigure` (see below) |
| Anything else (`Any` ports) | JSON if it consists of `None`, `bool`, numbers, text, lists and dicts of these; otherwise **not stored** | the same structure |

**Tables.** Column names and the index are kept out of Parquet, whose column names must be
unique text and which has no two-level headers. The writer stores the data under the names
`c0 … cN` and describes the rest in `meta.json`:

- `columns`: the original column labels in order — text, numbers, or lists for two-level
  headers — plus the level names; duplicates and odd characters survive;
- `index`: `"range"` for the default index; otherwise the index (also a multi-index) is
  stored as extra columns `i0 …` with its names;
- `dtypes`: the pandas dtype of every column, so that e.g. integers with missing values,
  categoricals, dates and text columns come back with the same dtype;
- `attrs`: the table's `attrs` as JSON (units, unit system, title, source file, Nastran
  version, result name …). If `attrs` holds something JSON cannot represent, the table is
  not stored (STO-10) rather than stored incompletely.

After writing, the tool does **not** read the table back to verify it (cost), but the test
suite proves the round trip for all supported shapes (§6).

**Figures** come back as a `StoredFigure`: an image with the `savefig(buffer, format="png",
dpi=…)` method that reports and the preview use. It is *not* a live matplotlib figure: a
Python block that receives it can write it again but not draw into it. Because a Python
block can also keep its figure only in memory, nothing breaks: a block that has a figure
output and runs in the current session gives the live figure to its successors; only
blocks restored from the store give the image.

**Not stored (STO-10):** values the formats above cannot hold; failed, skipped or stopped
blocks (there is nothing valid to store); blocks of an unknown type.

### 3.4 When an entry is valid (STO-03 … STO-06)

On opening a pipeline, and whenever the canvas states are refreshed, the executor computes
the current cache key of every block (this reads and fingerprints the input files, as it
does today) and compares it with the `cache_key` of the entry — reading only the small
`meta.json` files:

| Entry | Key equal | State |
|-------|-----------|-------|
| none | – | `idle` |
| readable, same `store_schema` and `tool_version` | yes | `done` |
| readable, same `store_schema` and `tool_version` | no | `stale` |
| readable but other `tool_version` or unknown `store_schema` | – | `stale` (shown, but never reused; the next run replaces it) |
| unreadable / partial / `meta.json` missing | – | `idle`, one log line |

The **tool version** is part of the validity so that a new release does not reuse outputs
of an old one. (The version number does not change during development: when a block's
result changes, bump the block's `version` as before; `apb clear-outputs` is for the
developer in between.)

**Blocks that write files** (`always_run`: Excel, Word report) run on every *Run* anyway
(ENG-17). Their entry records the files they wrote (`written_files`, via a new optional
hook `Block.output_files(params, outputs, ctx)`); the block shows `done` only while those
files exist. A deleted report is therefore `stale`, as the analyst expects.

The key contains the keys of the upstream blocks, so a change anywhere upstream (a
parameter, an input file, a variable) makes every block downstream of it `stale` at once; no
separate check of the upstream state is needed.

### 3.5 Loading (STO-04)

`done` and `stale` blocks get a `BlockResult` straight from the entry: state, log,
duration, key, the stored time. Its `outputs` is a **lazy mapping**: a port's value is
read from disk the first time it is used (preview, report editor, or a downstream block in
a run) and kept in memory afterwards. So opening a pipeline with a hundred blocks reads a
hundred small JSON files, not the data.

A stale entry's outputs can be looked at (marked *outdated* in the preview) but are never
used by a run.

### 3.6 Running (STO-05)

The executor asks, in this order: the in-memory cache (this session), then the store, then
runs the block. A block taken from the store emits the same *cached* event as today. After
a block finishes, its entry is written before the next block starts, so stopping the tool
in the middle of a run loses nothing that finished. Writing happens in the run's worker
thread; a failure to write (disk full, access denied) is logged as a note on that block
("Outputs not stored: *reason*") and never fails the block.

A block that **fails or is stopped** loses its stored entry (and its in-memory outputs), so
an old result is not shown as current next to the error.

### 3.7 Clearing (STO-08)

- **Clear all:** removes the whole store of the pipeline (all entries and the temporary
  folders); every block becomes `idle`; the in-memory cache is cleared too.
- **Clear a block:** removes the entry of the block **and of every block downstream of
  it** (their results were computed from it), then those blocks are `idle` and their
  previews empty. The point of clearing is to recompute; a block that is `done` while its
  upstream block is empty would not do that.
- The files written by blocks (Excel, Word) and the input files are never touched.
- Removing must also work on **Windows** while the tool is running: stored data files are
  read completely and closed (no memory mapping), so they can be deleted afterwards.

Orphans (STO-13): when the pipeline is saved, entries whose block ID is not in the pipeline
are deleted. They are also counted and offered in the dialog of §3.9.

### 3.8 Unsaved pipelines and *Save as* (STO-12)

A new pipeline has no file name, so no store. Results are kept in memory as today. At the
first *Save* or *Save as* the store of the new name is created and the outputs of all
`done` blocks are written to it. *Save as* of an existing pipeline to a new name does the
same (the old store stays with the old file).

### 3.9 GUI (spec 06)

| Where | What |
|-------|------|
| Opening a pipeline | the canvas shows the stored states at once; the log says e.g. "Stored outputs found for 9 of 12 blocks (2 outdated)"; clicking a block shows its preview as after a run |
| Preview | the header says where the result comes from: "Done in 0.19 s" (this session), "Done · stored 2026-11-15 17:08" (restored), "Outdated · stored 2026-11-15 17:08 — parameters or inputs changed since" (stale) |
| Block panel | a line "Stored output: 2026-11-15 17:08 · 12 kB" with **Clear output** (this block and everything downstream, with a confirmation that lists the blocks) |
| *Pipeline* menu | **Stored outputs…** dialog: folder (with *Open folder*), number of entries, size on disk, orphans; **Clear all** (confirmation); close |
| *Run* | unchanged; the summary counts blocks taken from the store ("Run finished: 12 blocks done, 9 up to date") |
| Warning | "Results cannot be stored next to the pipeline: *reason*" once per pipeline |

The unsaved-changes logic is unchanged: the store is not part of the pipeline file, so
running a pipeline does not mark it as modified.

### 3.10 Command line (STO-14, STO-15)

```
apb run pipeline.apb.json                 # uses the store; a second run only rewrites reports
apb run pipeline.apb.json --no-cache      # recompute everything (e.g. for the final issue)
apb outputs pipeline.apb.json             # block, state, time, size per entry
apb clear-outputs pipeline.apb.json       # clear all
apb clear-outputs pipeline.apb.json --block global_margins   # this block and downstream
```

`--no-cache` ignores stored outputs for the run but writes the new ones afterwards.

A run with `--set name=value` neither reads nor writes the store: its results belong to a
modified pipeline and must not replace the ones the GUI shows.

### 3.11 Implementation notes

- Object columns of a table are stored only if they hold text: Parquet would turn a column
  of dicts into a "struct" and return it changed, so such tables are refused (STO-10)
  rather than stored inexactly. `None` and NaN in a text column come back as they were.
- The entries of the store are read once per attached store and kept in memory, since the
  GUI refreshes the states twice a second; `Executor.attach_store` reads them again.
- `Executor.restore(pipeline)`, `persist(pipeline, dir)`, `clear_outputs(pipeline, ids)`,
  `stored_entry(id)` and `index_problems()` are the engine API used by the GUI and CLI.

### 3.12 Modules

`apb/engine/store.py` — `OutputStore` (folder, entry list, save, load, clear, size, orphans),
`StoredEntry`, `LazyOutputs`, `StoredFigure`, `StoreError`, and the encode/decode functions
(pure, tested without a pipeline). `Executor(registry, sink, store=None)` and
`Executor.attach_store(store)` (the GUI attaches the store of the current file when a
pipeline is opened or saved; `None` detaches); `Executor.restore(pipeline, dir)` returns
the `BlockResult` of every block with a readable entry. `BlockResult` gets `restored` and
`finished` (shown in the preview). `RunController` and `EditorState` use these; no block needs to change except the
two file writers, which implement `output_files`.

Dependency: `pyarrow` (industry standard, binary wheels for Windows and Linux; about 40 MB
installed). `pandas.read_parquet` / `to_parquet` do the table work.

## 4. Out of scope (v1)

A central or shared store, a size limit and automatic clean-up, file locking between two
tools working on one pipeline at once (the last writer wins; writes are atomic so nothing
is torn), compressing or deduplicating entries, storing the *inputs* of a run, versioned
history of results, exporting a results bundle for colleagues (collaboration is a later
step, spec README).

## 5. Open questions

1. ~~Location~~ Decided: next to the pipeline in the hidden `.apb` folder (§3.1).
2. ~~`apb run` and the store~~ Decided: `apb run` reuses stored results by default, with
   `--no-cache` for a full recomputation (§3.10).
3. ~~Clear a block~~ Decided: it clears the block and everything downstream (§3.7); no
   separate "only this block" action.
4. ~~Figures~~ Decided: acceptable that a restored figure is an image (§3.3).
5. ~~Traceability~~ Decided: not wanted; the report's traceability section stays as it is
   (the other traceability points come later).

## 6. Test plan

- **Round trip (unit):** tables of every shape — plain, non-range and multi-index, two-level
  headers (the load case table), duplicate and numeric column names, NaN, integers with
  missing values, categoricals, dates, text, empty tables, nested `attrs` — compared with
  `assert_frame_equal(check_exact=True)`, the dtypes and `attrs`; scalars (int, float,
  NaN, ±inf, `Quantity`), Unicode text, figures (image equal to a re-render), JSON values;
  values that cannot be stored are refused with a message and leave nothing behind.
- **Store (unit):** atomic write (an interrupted write leaves the old entry), corrupt and
  partial entries, unknown schema, other tool version, orphans, clearing (also downstream),
  read-only folder, `.gitignore` in the folder, entries read and deleted on Windows
  semantics (no open handles).
- **Executor:** a second `Executor` on the same store: all blocks `done` without running,
  all outputs equal; a changed parameter, a changed input file, a changed variable make the
  right blocks `stale`; a deleted report file makes the report block `stale`; `--no-cache`.
- **Test case (spec 07):** run, "restart" (new executor), all 12 blocks `done`, numbers of
  spec 07 §5 read from the store.
- **Browser:** run the test case, stop the server, start it again on the same folder: the
  states are `done` at once, the preview shows the requirement table, *Clear output* of a
  block empties it and its downstream blocks, *Clear all* empties everything, and a read-only
  folder shows the warning.
