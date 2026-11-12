# 06 — GUI Node Editor

## 1. Purpose

A browser-based, Simulink-like editor to build, configure, run and inspect pipelines.

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| GUI-01 | Started with `apb gui [file]`; opens the default browser at `http://127.0.0.1:<port>`; binds to localhost only | M |
| GUI-02 | Works in current Firefox, Edge and Chrome | M |
| GUI-03 | **Canvas:** blocks with labelled input/output ports; drag to move; connect ports by dragging; delete blocks/connections; zoom and pan | M |
| GUI-04 | Connections between incompatible port types are refused with a visible hint | M |
| GUI-05 | **Block library** panel: block types grouped by category, with search; drag or click to add | M |
| GUI-06 | **Property panel** for the selected block, auto-generated from its Pydantic `Params` model (text, number, bool, enum → drop-down, path → file picker, list, code → code editor) | M |
| GUI-07 | Blocks may provide a custom property panel when the auto-form is not enough (e.g. report sections editor, Python code editor) | M |
| GUI-08 | **Run** (whole pipeline) and **Run to here** (selected block); Stop | M |
| GUI-09 | Block state shown by colour/icon: idle, stale, running, done, error, skipped | M |
| GUI-10 | **Output preview** for the selected block: table preview (paged), scalar value, text, figure image | M |
| GUI-11 | **Log panel**: run events, print output, errors with tracebacks; clicking an error selects the block | M |
| GUI-12 | File: New, Open, Save, Save As (`*.apb.json`); unsaved-changes indicator and warning | M |
| GUI-13 | Undo / redo for graph edits | S |
| GUI-14 | Pipeline variables editor (ENG-14) | S |
| GUI-15 | Copy / paste blocks; multi-select | L |
| GUI-16 | Light/dark theme | L |

## 3. Design

### 3.1 Layout

```
+--------------------------------------------------------------------------+
| File  Edit  Run          [▶ Run] [■ Stop]          pipeline.apb.json *    |
+-----------+----------------------------------------------+---------------+
| Library   |                                              | Properties    |
|  Input    |   [Nastran H5]──▶[Envelope]──▶[Python]──┐    |  (form for    |
|  Table    |                       │                 ▼    |   selected    |
|  Python   |                       └──────────▶[Word report]  block)      |
|  Report   |                                              |               |
+-----------+----------------------------------------------+---------------+
| Preview (table / value / figure)        | Log                            |
+-----------------------------------------+--------------------------------+
```

### 3.2 Technology

- **NiceGUI** for the page, panels, forms, tables (`ui.aggrid` / `ui.table`), file dialogs,
  code editor (`ui.codemirror`) and log.
- **Drawflow** (vendored JS + CSS, no npm/build step) for the canvas, wrapped in one NiceGUI
  custom element `NodeCanvas` (`gui/canvas.py` + `gui/static/canvas.js`).
- The canvas is a *view*: the Python `PipelineModel` is the single source of truth.
  - JS → Python events: `block_moved`, `connection_created`, `connection_removed`,
    `block_removed`, `block_selected`.
  - Python → JS commands: `add_block`, `remove_block`, `connect`, `set_state`, `load_graph`.
  - Python validates each change (e.g. port compatibility) and rejects it by sending a revert.
- If Drawflow turns out to be limiting, only `NodeCanvas` is replaced (e.g. by Vue Flow, which
  fits NiceGUI's Vue base); the rest of the GUI and the engine are unaffected.

### 3.2.1 Implementation notes (step 1)

- `gui/editor.py` (`EditorState`) holds all edit rules and is tested without a browser:
  readable block IDs (`b1`, `b2` …, never reused), label defaults to the block title,
  connection checks (types, one connection per single input, no self-connection, no loop)
  with a message shown to the user; the canvas undoes a refused connection.
- The initial pipeline is sent with the page (no flicker); connection paths are redrawn
  after layout; **View > Fit to window** (also automatic after opening), zoom in/out
  (also Ctrl + mouse wheel), reset view.
- Delete: select a block or connection and press Delete (only while the canvas has the
  keyboard focus, so typing in the side panels is safe), or use "Delete block" in the panel.
- The tab title and header show the file name with `*` for unsaved changes; the browser
  asks before closing a tab with unsaved changes. New/Open ask before discarding changes.
- Opening a pipeline with Python blocks shows a trust notice (spec 04 §3.3); blocks of
  types that are not installed are shown in red and reported.
- Browser tests: `tests/gui/test_browser.py` drives the real GUI in headless Chromium with
  **Playwright** (dev dependency; `uv run playwright install chromium` once). They run in CI.

### 3.2.2 Implementation notes (step 2)

- **Parameter panel** (`gui/forms.py`): generated from the block's Pydantic `Params`;
  edits the stored parameters, so `${variables}` are kept (number fields accept a number or
  `${name}`). Widgets: text, file (with server-side file picker, path stored relative to
  the pipeline), code (folded when empty), number, switch, drop-down, multi-select,
  comma-separated list, key/value rows, port rows (Python block), nested group, JSON
  editor fallback (report sections until their own editor, step 3). Validation messages of
  the selected block are shown above its parameters. A newly added block is selected.
- **Python block:** "Create script" (skeleton matching the ports) and "Edit script"
  (full-screen code editor). Changing ports rebuilds the block on the canvas and removes
  connections to ports that no longer exist (with a notice).
- **Run** (header) and **Run to here** (block panel) run in a worker thread; events reach
  the page through a queue. **Stop** stops after the current block. Block border colours:
  green done, amber stale (settings, inputs or files changed since the run), blue dashed
  running, red error, faded skipped. Validation problems block the run and are listed in
  the log.
- **Preview** (below the canvas): the selected block's result — error with traceback,
  one tab per output (table: first 500 rows, units in headers, two-level headers as
  "TENSION [N] / LC1"; values with unit; figures; written files with an "Open" button),
  and the block's own log.
- **Log** (below the canvas): run events; lines of a block are clickable and select it.
- **Pipeline > Variables and settings:** variables (values as JSON, else text), default
  unit system, title, author, description.

### 3.3 Running

Runs execute in a background thread via the engine; engine events are pushed to the browser
through NiceGUI's websocket to update block states, previews and the log live.

### 3.4 Native file dialogs

A browser cannot give a server full local paths. Since the server runs locally, the file
picker is a NiceGUI dialog browsing the local filesystem (server side). Paths are stored
relative to the pipeline file when possible (ENG-13).

## 4. Out of scope (v1)

Multi-user editing, remote server deployment with authentication, minimap, block grouping,
desktop packaging (a native window via NiceGUI's `native` mode / pywebview is possible later).

## 5. Open questions

1. ~~Native window?~~ A browser tab is fine.
2. ~~GUI technology — browser or desktop Qt (PySide6)?~~ Decided: **browser** (NiceGUI +
   Drawflow), as described in this spec.
