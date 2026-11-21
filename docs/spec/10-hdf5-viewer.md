# 10 — HDF5 Viewer

Status: **approved** (2026-11-21, round 4 step 1)

## 1. Purpose

The result browser in the *Nastran HDF5 results* panel (spec 02 §3.7) lists only the
**results** the reader knows how to turn into tables. When building a pipeline the analyst
often needs more: look at the whole file (model data under `/NASTRAN/INPUT`, the `DOMAINS`
table, the `/INDEX` group, attributes), see what a dataset really contains before choosing
a block, and check which subcases and modes are in it.

The HDF5 viewer is a separate, read-only view of one `.h5` file in its own browser tab. It
does not change the pipeline; it helps the analyst find the dataset path and filters to
type into a block, and it is useful on its own (e.g. `apb h5view job.h5` to look into a
result file without opening a pipeline).

Related: spec 02 (reader, mapping, result browser), spec 06 (GUI), spec 09 (the analysis
block that produces the `.h5`).

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| H5V-01 | The viewer opens in a **new browser tab** at route `/h5?file=<path>`; several viewers can be open at the same time, next to the editor | M |
| H5V-02 | Entry points: *File > HDF5 viewer…* (file dialog), **Open in viewer** button in the panels of the HDF5 result blocks (file of the block, selected result pre-selected), and `apb h5view <file>` on the command line (starts the server if needed, opens only the viewer) | M |
| H5V-03 | **Tree** of the whole file: groups and datasets (including `INPUT`, `INDEX`, `RESULT`); each dataset shows shape, number of rows and number of columns; a search box filters the tree by name | M |
| H5V-04 | **Details** of the selected item: full path, attributes (name → value), for datasets the columns of the compound type with their HDF5 type and, where the mapping knows it, quantity kind and unit (spec 02 §3.2) | M |
| H5V-05 | **Raw view** of a dataset: its rows in a table with paging (default 100 rows per page); only the rows of the shown page are read from the file (NAS-09), so large datasets work | M |
| H5V-06 | Multi-valued fields (arrays inside a row, e.g. stress at several points) are shown as separate columns `NAME[0]`, `NAME[1]`, …; nested compound fields as `PARENT.CHILD` | M |
| H5V-07 | **Result view** for datasets below `/NASTRAN/RESULT`: the rows as the *Nastran HDF5 results* block would deliver them (domain joined in: subcase, mode, eigenvalue/frequency; mapped column names and units), with filters for subcase, mode and IDs (same ID syntax as the block, e.g. `1001-1200, 3005`) | M |
| H5V-08 | A switch between raw view and result view for result datasets; other datasets have the raw view only | M |
| H5V-09 | **File summary** at the top: file name, size, Nastran version, solution, input deck, run time (as in spec 02 NAS-14), subcases present with their number of domains/modes | M |
| H5V-10 | **Copy path** of the selected dataset (for the `result` parameter of an HDF5 block; results are copied in the short form the block uses, e.g. `NODAL/DISPLACEMENT`) | M |
| H5V-11 | **Export CSV** of the current view (with filters) to a file chosen in the file dialog; a warning above 1 000 000 rows | S |
| H5V-12 | Read-only: the file is opened read-only for each read and closed right after, so it is never locked or changed and a running Nastran job can rewrite it; **Reload** button re-reads the file (tree and view) | M |
| H5V-13 | A missing, unreadable or non-HDF5 file gives a clear message on the page, not a traceback | M |
| H5V-14 | **Use in pipeline**: from the viewer, set the selected result (and filters) on an HDF5 block of the open pipeline, or add a new block | L |
| H5V-15 | Plots of a column (e.g. displacement along a list of grids) | L |

## 3. Design

### 3.1 Layout

```
+--------------------------------------------------------------------------------+
| HDF5 viewer — modal.h5   [Reload] [Copy path] [Export CSV]                     |
| examples/fem/modal.h5 · 2.4 MB · MSC Nastran 2023.3 · SOL 103 · modal.bdf ·     |
| 2026-11-15 14:25 · Subcases: 1 (10 modes)                                       |
+----------------------------+---------------------------------------------------+
| [ search…               ]  | /NASTRAN/RESULT/NODAL/EIGENVECTOR                  |
| ▾ NASTRAN                  | 30 080 rows · 8 columns    View: (•) Result ( ) Raw |
|   ▾ INPUT                  | Subcase [1 ▾]  Mode [all ▾]  IDs [          ]      |
|     ▸ ELEMENT              |                                                    |
|     ▸ NODE                 | SUBCASE MODE FREQ   ID   X     Y     Z  …          |
|       DOMAINS  1 × 5       |   1      1   25.71  1   0.0   0.0  1.2e-3          |
|   ▾ RESULT                 |   …                                                |
|       DOMAINS  22 × 16     |                                                    |
|     ▸ ELEMENTAL            |                    ◀ 1 2 3 … 301 ▶  100 rows/page   |
|     ▾ NODAL                +---------------------------------------------------+
|       ● EIGENVECTOR 30080  | Details                                            |
|         SPC_FORCE   30080  |  Attributes: version = 1                           |
| ▸ INDEX                    |  Columns: ID int64 · X float64 length [m] · …      |
+----------------------------+---------------------------------------------------+
```

- Left: the tree, with groups folded except the path to the selected dataset; while a
  search text is entered every group is unfolded so the matches are visible. The border
  between tree and table can be dragged (splitter). Row counts
  next to datasets; groups show the number of children.
- Right top: the table of the selected dataset (raw or result view).
- Right bottom: details (folding section, open by default for groups, folded for datasets).
- Selecting a group shows its attributes and children only.
- The page has its own title (`<file name> — HDF5 viewer`), so viewer tabs are easy to tell
  apart from the editor.

### 3.2 Opening

| From | Behaviour |
|------|-----------|
| *File > HDF5 viewer…* | File dialog (spec 06 §3.4) filtered on `*.h5`, starting in the folder of the pipeline; opens a new tab |
| *Nastran HDF5 results* panel | **Open in viewer** below the file information; opens the file of the block (typed or delivered by a connected analysis block) with the block's result selected and its subcase/ID filters and unit system filled in. Shown only while the file can be read. The other HDF5 blocks (eigenvalues, RBE2 loads, point masses) use the generated form and have no button; File > HDF5 viewer covers them |
| `apb h5view <file>… [--units SI\|SI_mm] [--port N] [--no-browser]` | One tab per file. Starts the GUI server like `apb gui`, opens `/h5?file=…` only (the editor stays reachable at `/`). If an APB server already answers on the port, only the browser tabs are opened |

URL parameters: `file` (absolute path, required), `dataset` (path to select), `subcases`,
`ids` (filters of the result view), `units` (`SI`, `SI_mm`; empty = the unit system stored
in the file — synthetic files have one, real Nastran files do not — else no unit labels).
The unit system can be changed on the page. The server only opens files the user asked for: the
route accepts any path on the local computer, as the GUI already does (localhost only,
GUI-01); no file is ever written except the CSV export.

### 3.3 Reading (library, no GUI code)

New module `apb.blocks.nastran.h5browse` (pure library next to `h5reader`, usable from
scripts and tests):

- `list_tree(path) -> list[H5Node]` — one walk of the file; `H5Node` = path, kind
  (group/dataset), shape, row count, column count, attribute count. Cheap: no data read.
- `node_details(path, node) -> H5Details` — attributes (values converted to text, byte
  strings decoded), columns of a compound type (name, HDF5 type, shape of array fields),
  quantity kind and unit from the mapping when the dataset is a known result.
- `read_rows(path, dataset, start, count) -> pd.DataFrame` — raw rows `start … start+count`
  via h5py slicing (only those rows are read), flattened per H5V-06. Scalar and 0-d
  datasets give one row; non-compound arrays give columns `[0]`, `[1]`, ….
- `select_rows(path, dataset, subcases=None, modes=None, ids="") -> RowSelection` — the
  rows of a dataset that match the filters, computed **once per filter change** and kept by
  the page. Subcase/mode filters go through the domains and use the `/INDEX` group
  (position and length per domain) when present, so no data is read; without `/INDEX` the
  `DOMAIN_ID` field is read in chunks. Tables with their own `MODE` field (modal
  summaries) are filtered on that field. The ID filter reads only the ID field, chunk by
  chunk. Non-result datasets are not filtered.
- `read_page(selection, start, count, result_view=True, unit_system=None)` — one page:
  in the result view as `h5reader.read_result` delivers it (the table step is shared:
  `h5reader.result_table`, no second implementation), else the raw rows with their row
  numbers. The domain columns are decided from the whole selection, so every page has the
  same columns. Paging counts dataset rows; a beam row (stations) or a 6 × 6 matrix row
  shows as several table rows.
- `export_csv(selection, target, result_view, unit_system) -> int` — all pages to CSV.
- `subcase_summary(path) -> list[SubcaseInfo]` — subcases with number of domains and modes
  (from `RESULT/DOMAINS`).

Every function opens the file read-only, reads, and closes it (H5V-12). Errors become
`NastranH5Error` with a message for the user.

### 3.4 Paging and size limits

- Page size 50 / 100 (default) / 500 rows; page buttons with first/last.
- Filters are applied in chunks of 100 000 rows, so memory stays bounded; the matching
  rows are found once per filter change, then paging only reads the rows of the page.
- The table is a NiceGUI `ui.table` (as in the preview) holding only the shown page, with
  a `ui.pagination` below it: the browser never holds more than one page.
- Floating point values are shown with 6 significant digits; the CSV export writes full
  precision.

### 3.5 Copy path and export

- **Copy path** copies the dataset path; for datasets below `/NASTRAN/RESULT` the short form
  the HDF5 block accepts (`NODAL/EIGENVECTOR`, spec 02 `normalise_result_path`), else the
  full path. A notification confirms what was copied.
- **Export CSV** writes the current view (raw or result, with the filters, all pages) with
  a header row; in the result view the units are written into the header as
  `T1 [m]`. File name proposal: `<file stem>_<dataset name>.csv` next to the `.h5`.

### 3.6 Code

| Part | File |
|------|------|
| Reading helpers | `src/apb/blocks/nastran/h5browse.py` (new) |
| Viewer page | `src/apb/gui/h5viewer.py` (new), route `/h5` registered in `app.py` |
| Entry points | `app.py` (File menu), `nastran_panel.py` (button), `cli.py` (`h5view` command) |
| Test files with several subcases | the existing synthetic files `examples/data/synthetic_plate.h5` (subcases 1–3 + modal subcase 10) and `synthetic_fastener_joint.h5` (4 subcases); `tools/make_synthetic_h5.py` now also writes the `/INDEX` group like the real files |
| Tests | `tests/nastran/test_h5browse.py` (real files in `examples/fem` + the synthetic files, with and without `/INDEX`), browser tests in `tests/gui/` |

## 4. Out of scope (this step)

Editing or writing HDF5 files; opening files other than local paths; plots (H5V-15);
writing back to the pipeline (H5V-14); comparing two files; OP2/F06 viewing.

## 5. Decisions (review 2026-11-21)

1. Result datasets open in the **result view**; the raw view is one click away.
2. `apb h5view` accepts **several files**, one tab each.
3. CSV export (H5V-11) is part of this step.
