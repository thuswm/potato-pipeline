# 06 — GUI Node Editor

## 1. Purpose

A browser-based, Simulink-like editor to build, configure, run and inspect pipelines.

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| GUI-01 | Started with `apb gui [file]`; serves `http://127.0.0.1:<port>` and opens it in an app window (§3.6, GUI-25), else the default browser; binds to localhost only | M |
| GUI-02 | Works in current Firefox, Edge and Chrome | M |
| GUI-03 | **Canvas:** blocks with labelled input/output ports; drag to move; connect ports by dragging; delete blocks/connectors; zoom and pan | M |
| GUI-04 | Connectors between incompatible port types are refused with a visible hint | M |
| GUI-05 | **Block library** panel: block types grouped by category, with search; drag or click to add | M |
| GUI-06 | **Property panel** for the selected block, auto-generated from its Pydantic `Params` model (text, number, bool, enum → drop-down, path → file picker, list, code → code editor) | M |
| GUI-07 | Blocks may provide a custom property panel when the auto-form is not enough: Nastran result browser (spec 02 §3.7), Python script view (spec 04 §3.5), report section editor (spec 05 §3.4) | M |
| GUI-08 | **Run** (whole pipeline) and **Run to here** (selected block); Stop | M |
| GUI-09 | Block state shown by colour/icon: idle, stale, running, done, error, skipped | M |
| GUI-10 | **Output preview** for the selected block: table preview (paged), scalar value, text, figure image | M |
| GUI-11 | **Log panel**: run events, print output, errors with tracebacks; clicking an error selects the block | M |
| GUI-12 | File: New, Open, Save, Save As (`*.apb.json`); unsaved-changes indicator and warning | M |
| GUI-13 | Undo / redo for graph edits | S |
| GUI-14 | Pipeline variables editor (ENG-14) | S |
| GUI-15 | Copy / paste blocks; multi-select | L |
| GUI-16 | Light/dark theme | L |
| GUI-21 | **Stored outputs** (spec 08 §3.9): states and previews of a reopened pipeline come from the stored outputs; the preview says where a result comes from; *Pipeline > Stored outputs…* and *Clear output* in the block panel | M |
| GUI-24 | Panel of the Constants block: constants with add/move/delete, inline errors, "used by" per constant, rename that follows connectors and `${name}` references (spec 03 §3.5.3) | M |
| GUI-23 | **HDF5 viewer** in its own browser tab (route `/h5`): *File > HDF5 viewer…*, **Open in viewer** in the HDF5 results panel, `apb h5view` (spec 10) | M |
| GUI-22 | Panel of the Nastran analysis block: status lines (program found, input, results current?), open the `.f06` and the folder, messages table, warning mark on the canvas (spec 09 §3.9) | M |
| GUI-17 | The file dialog can create a **new folder** in the folder it shows (§3.5) | M |
| GUI-18 | Every block on the canvas shows its **block type** under its label, also when the label equals the type name; the property panel states the block type and category explicitly (§3.5) | M |
| GUI-19 | **File > Quit** closes the application: asks about unsaved changes and a running pipeline, stops the server and closes the app window (§3.6, GUI-26); in a plain browser tab it tells the user the tab can be closed (§3.5) | M |
| GUI-20 | One vocabulary in all GUI texts, messages and documentation: *block*, *connector*, *port*, *pipeline* (spec 00 §1.1) | M |

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
  connection checks (types, one connector per single input, no self-connector, no loop)
  with a message shown to the user; the canvas undoes a refused connector.
- The initial pipeline is sent with the page (no flicker); connector paths are redrawn
  after layout; **View > Fit to window** (also automatic after opening), zoom in/out
  (also Ctrl + mouse wheel), reset view.
- Delete: select a block or connector and press Delete (only while the canvas has the
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
  connectors to ports that no longer exist (with a notice).
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

**File types and long paths (GUI-33, GUI-34) — round 6.** Found in the Windows test: the
Nastran input field listed only `.h5` files (the type was guessed from the word "nastran"
in the field label), and a long folder path hid its end.

| ID | Requirement | Prio |
|----|-------------|------|
| GUI-33 | A file parameter names its file types (`file_types(".bdf", ".dat", ".nas", ".blk")` in the block's parameter model, `apb_file_types`); the dialog lists folders and files of those types (case-insensitive) and has a switch *Show all files*. A parameter without types lists all files. In save mode the first type is appended to a name that has none of them. Save mode is used for files the block writes and for Python scripts (a new script gets a skeleton) | M |
| GUI-34 | The folder is shown as clickable parts (`C: / work / project / fem`). A path too long for the dialog is cut **at the start**, so the folder shown is always readable; the full path is the tooltip. Typing or pasting a folder path in *File name* and pressing Enter opens that folder | M |

Types per field: Nastran input deck `.bdf .dat .nas .blk`; Nastran results `.h5`; CSV
`.csv .txt`; Excel input `.xlsx .xlsm`; Excel output `.xlsx`; Python script `.py`; Word
base document `.docx .dotx`.

### 3.5 Review round 3: small fixes (GUI-17 … GUI-20)

**New folder (GUI-17).** The file dialog gets a "New folder" button next to "Parent folder"
and "Home". It asks for a name, creates the folder inside the folder shown and opens it, so
Save as can continue there. Names that are empty, contain a path separator or already exist
are refused with a message; a failure to create (e.g. no write permission) is shown, not
raised. The button is available in every mode of the dialog (open, save, choose file).

**Block type always visible (GUI-18).**
- Canvas: the small type line under the label is always drawn. (Until now it was left out
  when the label was identical to the type name, e.g. a block still labelled "Load case
  names".)
- Property panel: below the label field a line "Block type: *Load case names* · Table"
  (type name and category) replaces the technical line; the technical identifiers
  (`type_id`, block id) move into a tooltip on that line.

**Quit (GUI-19).** *File > Quit*:
1. If a pipeline is running: "A run is in progress. Stop it and quit?"
2. If this tab, or another open tab of the same server, has unsaved changes: "Unsaved
   changes in: *name*, … Quit without saving?" (Changes are only written by Save / Save as;
   nothing is saved automatically.)
3. All open tabs show a page "Potato Pipeline has been closed — you can close this
   tab", then the server stops and `apb gui` returns in the terminal.

Closing a browser tab does not stop the server (a reload or a second tab must keep working);
the existing warning when a tab with unsaved changes is closed stays. Starting and stopping
the tool as one application, without a visible terminal, is a later step (§4).

**Vocabulary (GUI-20).** GUI texts and messages are checked against spec 00 §1.1; today's
"connection" becomes "connector" in everything the user reads. Identifiers in the code and
the `connections` key of the pipeline file are not renamed, so saved pipelines stay valid.

Implementation notes (step 2): `make_folder` in `gui/filedialog.py`; open editors are kept in
`app._PAGES` so Quit can see unsaved changes and runs in all tabs; Quit shows the closing
message in every tab and then calls `nicegui.app.shutdown()`. The pipeline file schema is
unchanged.

### 3.6 Application window and clean exit (GUI-25 … GUI-29) — round 4 step 3

Status: decided 2026-11-21: **option A** (Chrome/Edge app window, no new dependency).

Today `apb gui` opens a tab in the default browser. *File > Quit* stops the server, but the
tab stays open showing "Closed", because a web page may only close windows it opened
itself. A real exit needs the program to own the window. MSC Nastran users are mostly on
Windows, where Edge is always installed.

| ID | Requirement | Prio |
|----|-------------|------|
| GUI-25 | `apb gui` opens the editor in an **app window** of Chrome or Edge (`--app=<url>`): its own window without tabs, address bar or bookmarks, with the tool's title and icon. It uses its own browser profile folder, so it is a separate browser process that never touches the user's normal browser windows | M |
| GUI-26 | **File > Quit** (after the existing checks, GUI-19) ends the server **and closes the app window** | M |
| GUI-27 | **Closing the app window** (window ×) ends the server. The existing unsaved-changes warning of the browser (GUI-12) appears first; nothing is saved automatically. A running pipeline is stopped. This must also work when the browser process outlives its window (Edge on Windows, round 6): the server then stops 5 s after the last page disconnected and ends the browser process | M |
| GUI-28 | **Fallback**: when neither Chrome nor Edge (nor Chromium) is found, or when chosen, the default browser opens a tab as today (Firefox has no app mode). Quit then shows "Closed" in the tab as today. In this mode the server also stops when no tab has been connected for 30 s (after at least one was), so a forgotten server does not keep running | M |
| GUI-29 | The window mode is chosen with `apb gui --window app\|browser\|none` (`none` = start only the server, the same as `--no-browser`) and as a computer default in the config file: `[gui] window = "browser"`, `[gui] browser = "<path to chrome.exe or msedge.exe>"`. Command line > config file > `app` | M |
| GUI-30 | The HDF5 viewer (`apb h5view`, spec 10) opens its file(s) in app windows the same way; extra windows of the same run join the same browser process | S |
| GUI-32 | On start the console says how the pages were shown: which browser (path), that it is a separate app window with its own profile, that closing it or File > Quit stops the tool, and how to get a normal tab instead (`--window browser`) — round 6 | M |
| GUI-35 | The property panel lists the warnings of the block's last run (the `WARNING:` lines of its log that give the warning mark on the canvas), up to 5 with "… and N more, see the log"; they update when a run finishes — round 6 | M |
| GUI-31 | Desktop shortcut / start menu entry, and hiding the console window on Windows (`pythonw`) | L |

#### 3.6.1 Design

- **Finding the browser** (`gui/launcher.py`, `find_browser()`): the configured path if
  set; else, in this order, Chrome, Edge, Chromium:
  - Windows: `chrome.exe` / `msedge.exe` under `%ProgramFiles%`, `%ProgramFiles(x86)%`
    and `%LOCALAPPDATA%` (standard install folders);
  - Linux: `google-chrome`, `google-chrome-stable`, `chromium`, `chromium-browser`,
    `microsoft-edge`, `microsoft-edge-stable` on `PATH`;
  - macOS: the `.app` bundles in `/Applications`.
- **Command:** `<browser> --app=<url> --user-data-dir=<profile> --no-first-run
  --no-default-browser-check --window-size=1500,950`. The profile folder is
  `<config folder>/apb/browser-profile` (next to `config.toml`). With its own profile the
  browser starts a new process that ends when its last window closes; that is what makes
  "window closed" observable.
- **Watching:** a background thread waits for the browser process. When it ends and Quit
  was not the reason, the server shuts down (stop running work, then `app.shutdown()`).
  When Quit ends the server, the browser process is terminated (its windows close). Windows
  opened later in the same profile (e.g. the HDF5 viewer from the File menu,
  `ui.navigate.to(..., new_tab=True)`) belong to the same process, so they close too.
- **Second start:** if `apb gui` is started while the port is in use by another APB server,
  the existing server is reused: only a new window is opened (as `apb h5view` does). A
  pipeline given on the command line is then not opened; a message says to use File > Open
  there or `--port` for a second server.
- **Windows finding (round 6, 2026-11-23):** on Windows 11 with Edge, closing the window
  did not stop the server: the watched process did not end (Edge can keep running in the
  background, or hand the window to another process after a few seconds). The end of the
  process is therefore only the fast path; the page timer below applies in app mode too.
- **Page timer (GUI-27, GUI-28):** connected pages are counted (NiceGUI connect/disconnect).
  When no page has been connected for the idle time, and only if one had connected, the
  server stops and, in app mode, ends the browser process. Idle time: 5 s in app mode,
  30 s in browser mode; `[gui] idle_shutdown_seconds` sets it for both. Reloading a page
  reconnects within a second and does not trigger it. Mode `none` never stops on its own.
- **No change to the pipeline file format**; tests keep using `--no-browser` (mode
  `none`), which never stops on its own.
- The config file reading moves from `blocks/nastran/command.py` to a small shared module
  `apb/config.py` (`config_path()`, `read_section(name)`); the Nastran block uses it too.

#### 3.6.2 Not in this step

A native window without any browser (option B, pywebview), packaging as an installer,
hiding the console window (GUI-31), single-instance locking across users.

## 4. Out of scope (v1)

Multi-user editing, remote server deployment with authentication, minimap, block grouping,
desktop packaging (a native window via NiceGUI's `native` mode / pywebview is possible later;
round 4 chose the Chrome/Edge app window instead, §3.6).

## 5. Open questions

1. ~~Native window?~~ A browser tab is fine. Round 4: an app window of Chrome/Edge (§3.6).
2. ~~GUI technology — browser or desktop Qt (PySide6)?~~ Decided: **browser** (NiceGUI +
   Drawflow), as described in this spec.
