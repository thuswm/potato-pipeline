# PLAN — review round 4 (2026-11-21)

Status: **approved 2026-11-21, in progress**. Completed plans are archived in `docs/plans/`.

Source: the user's notes after testing round 3. Each step: spec → user review → code →
tests (`ruff format`, `ruff check`, `mypy src`, `pytest`) → user asks for the commit.
Tick the boxes as work is done.

## Check of the round 3 notes

| Note | Status |
|------|--------|
| Save as has no "Create new folder" | done (step 2, `filedialog.py` New folder button) |
| HDF5 reader should preview the results in the file | done (step 1, result browser + peek). The full file browser is round 4 step 1 |
| No clean exit; warn about unsaved changes | partly: File > Quit asks about running work and unsaved changes, and the browser warns before a tab with unsaved changes closes. Nothing is saved automatically. **Gap:** the browser tab stays open and only shows "Closed" (round 4 step 3) |
| Hide the server setup | later (round 4 step 3 makes a start) |
| What does the fastener loads block do / too specialised? | decided: it stays as it is until it moves into a fastener toolbox |
| Block type under the label, also for Load case names, and in the property panel | done (step 2; every block shows `type_title`) |
| A reopened pipeline is seen as already run; clear outputs | done (step 5, stored outputs + Clear outputs) |
| Python block: view the code, templates, never overwrite | done (step 3) |
| Data organisation for collaboration | later |
| Nastran and Abaqus analysis blocks, f06 checks | Nastran done (step 6); Abaqus later |
| One term: block / node / box | done ("block", "connector") |
| Blocks in an extendable toolbox folder | later |
| Word block: template file input | later |

**Finding about load cases:** the HDF5 reader and the result browser handle any number of
subcases (the Subcases filter lists them all). The example files have only one load case
each: `global_loads.bdf`, `local_loads.bdf` and `modal.bdf` have no `SUBCASE` cards, and
their `DOMAINS` tables have subcase 1 only. The modal file has 22 domains = 1 subcase with
the modes. A deck with several subcases from the user would show the rest; until then a
small synthetic multi-subcase test file is used (step 1).

## Step 1 — HDF5 viewer (new spec `10-hdf5-viewer.md`)

A separate view in its own browser tab, to look through an `.h5` file and find the
data blocks need.

- [x] 1.1 Spec 10: purpose, requirements, layout, links to spec 02 and 06
  - Opened from File > "HDF5 viewer…", from the HDF5 block panel ("Open in viewer" for the
    connected/selected file) and from the CLI (`apb h5view <file>`); route `/h5?file=…`, new tab
  - Left: tree of the whole file (groups, datasets incl. `INPUT`, `INDEX`, `RESULT`) with
    shape, dtype, row count; attributes of the selected item
  - Right, raw view: dataset rows with paging (large datasets never loaded at once),
    nested fields flattened to columns
  - Right, result view (datasets below `RESULT`): domains joined in (subcase, mode,
    frequency/eigenvalue, analysis code), filter by subcase/mode/ID, units shown
  - "Copy path" for the dataset path used in an HDF5 block; optional CSV export of the view
  - Read-only; the file is never locked or changed (opened `r`, closed after each read)
- [x] 1.2 User review of spec 10 (result view default, several files per `h5view`, CSV export in this step)
- [x] 1.3 Reader helpers in `src/apb/blocks/nastran/h5reader.py` (or a new `h5browse.py`):
      tree listing, paged raw read, domain join — with tests on the real files
- [x] 1.4 Synthetic multi-subcase test file (tools script + fixture) and tests — the existing
      synthetic files already have 3–4 subcases; the generator now adds the `/INDEX` group
- [x] 1.5 Viewer page `src/apb/gui/h5viewer.py` (NiceGUI `ui.tree` + paged `ui.aggrid`/`ui.table`)
- [x] 1.6 Entry points: File menu, button in `nastran_panel.py`, `apb h5view` in `cli.py`
- [x] 1.7 Browser tests (Playwright) and screenshots; update spec 02/06 cross-references
- [x] 1.8 Commit

## Step 2 — Constants block (`table.constants`, spec 03 new section)

A block holding the project constants (e.g. safety factors). Output: one table. Columns:
Name, Value, Description. Proposal and points for the user to decide in the review:

- [x] 2.1 Spec: block, parameters, validation, how receiving blocks use a constant
      (draft: spec 03 §3.5)
  - Name rule: letter or `_` first, then letters, digits, `_` (no spaces), unique.
    Proposal: this Python identifier rule, so names also work in Python scripts
  - Value: number. *Decide:* allow text values too?
  - *Proposal:* a 4th column **Unit** (empty = dimensionless), since units must always be
    visible in reports
  - Use in receiving blocks, *proposal:* connect the Constants output to the block (new
    optional `constants` input on the requirement check, Python and report blocks); any
    numeric parameter can then be written `${SF_yield}` (the existing variable syntax);
    names from connected Constants blocks come before pipeline variables. A connector
    shows the dependency on the canvas and makes the cache key right when a constant
    changes. The Python block gets the constants as a table or dict argument
  - *Decide:* keep the pipeline variables dialog, or replace it with the Constants block
  - Report: the table goes into the Word report like any table (e.g. "Safety factors used")
- [x] 2.2 User review (all proposals of spec 03 §3.5.1 accepted)
- [x] 2.3 Block class + params model in `src/apb/blocks/table/` with validation; register it
- [x] 2.4 Table editor in the property panel (add/delete/move rows, inline validation)
- [x] 2.5 Engine: collect the constants of all Constants blocks while preparing the pipeline,
      resolve `${name}` like variables, clash check, `--set` overrides (`engine/graph.py`)
- [x] 2.6 One output port per constant (dynamic ports); rename keeps connectors and updates
      `${name}` references; "used by" list. *(Replaces the earlier idea of a `constants`
      input on the requirement check / Python / report blocks — see spec 03 §3.5.1)*
- [x] 2.7 Test case: Constants block with SF 1.5 and the 20 Hz requirement feeding the check,
      margin scripts and report (`tools/make_test_case.py`, regenerate, spec 07)
- [x] 2.8 Tests (unit + browser) and commit

## Step 3 — Clean exit (spec 06 new section; first part of "hide the server")

Today File > Quit stops the server but the browser tab stays. A web page can only close
windows it opened itself, so a real exit needs the program to own the window.

- [ ] 3.1 Spec with a choice for the user:
  - *Proposal A (no new dependency):* `apb gui` starts Chrome or Edge in app mode
    (`--app=<url>` with its own profile folder): a window without tabs or address bar.
    Quit ends that browser process, so the window closes; closing the window ends the
    server. Firefox has no app mode: there the tab shows "Closed" as today
  - *Proposal B:* NiceGUI native mode (pywebview): a real desktop window, but an extra
    dependency that needs WebView2 (Windows) or GTK/Qt WebKit (Linux)
  - Both: the server also stops when the last window has been closed for a few seconds;
    unsaved-change warning kept
- [ ] 3.2 User review / choice
- [ ] 3.3 Launcher (`src/apb/gui/launcher.py`): find browser, start, watch, shut down
- [ ] 3.4 Quit and window close wired to it; tests (launcher logic unit-tested, browser tests)
- [ ] 3.5 Commit

## Step 4 — Property panel review (one panel at a time)

The right-hand panel is hard to read and cluttered.

- [ ] 4.1 Inventory: screenshot of every block type's panel, listed with what is hard to
      read; general layout rules (fixed header with label + block type, sections that fold,
      help text in tooltips, wider/resizable panel, "Open editor" dialog for big content
      like tables, code and report sections)
- [ ] 4.2 User review: agree the rules and the order of the panels
- [ ] 4.3+ One sub-step per panel (spec note → code → screenshots → user check), e.g.
      HDF5 results, Nastran analysis, Python, Word report, requirement check, table blocks,
      fastener loads, Excel, Constants
- [ ] 4.x Commit after each panel or group of panels, as the user asks

Later (unchanged): load table → model update block; mode shape plots; Abaqus blocks; Word
template as block input; data organisation for collaboration; toolbox restructure; undo/redo
(GUI-13); push to a git remote so CI runs.

Open points carried over (from the old HANDOFF notes):
- Real MSC Nastran never run here: the default keywords `batch=no old=no scr=yes` in
  `src/apb/blocks/nastran/command.py` (spec 09 §3.3) need a test on a real model.
- Test case Excel workbook: decide whether the margin tables go in as extra sheets
  (`tools/make_test_case.py`, spec 07).
- CQUAD4, CBEAM, MPC force and grid point force readers are only tested on synthetic files
  (spec 02 §5); needs a real file with those requests.
- Practical: the full test suite takes ~6 min (browser tests need
  `uv run playwright install chromium`); run it in the background.
