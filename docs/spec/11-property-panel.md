# 11 — Property Panel Layout

Status: **approved** (2026-11-21, round 4 step 4)

## 1. Purpose

The property panel on the right (spec 06 GUI-06/07) has grown block by block and is now
hard to read: the same frame takes a third of every panel, help texts double the height of
every form, and the big panels (Word report, Python, Nastran analysis) need a lot of
scrolling in a 340 px wide column. This spec records what every panel looks like today,
proposes common layout rules, and an order in which the panels are reworked one at a
time (round 4 step 4).

Screenshots of every panel before the rework, taken from the test case with all blocks
run: [`panels/before/`](panels/before/) (one PNG per block type).

## 2. Inventory (before)

Height = height of the panel content at 340 px width, without scrolling. *Frame* is the
part that is the same for every block: header, label, block type line, description,
stored output line, and the Inputs/Outputs list at the bottom.

| Block type | Height | Screenshot | What is hard to read |
|------------|-------:|------------|----------------------|
| Word report | 2270 | [report.word](panels/before/report.word.png) | Longest panel by far: metadata, 11 item names (2 lines each), 20 sections in one column; sections are truncated to ~30 characters; everything scrolls together |
| Python | 1310 | [python.script](panels/before/python.script.png) | Absolute script path wraps over 3 lines; code view cut at the right; the port editor *and* the Inputs/Outputs list show the ports twice |
| MSC Nastran analysis | 1275 | [nastran.run](panels/before/nastran.run.png) | Status lines are good, but the run-mode explanation appears twice; 6 rarely changed settings (command, path, options, timeout, ignore messages, information) are always open, each with a 2-line help text |
| Nastran HDF5 results | 1090 | [nastran.hdf5_results](panels/before/nastran.hdf5_results.png) | Absolute file path of the connected analysis wraps over 3 lines; the result list is good but only ~8 rows high; filters below it scroll away from the preview |
| Load case table | 850 | [table.load_case_table](panels/before/table.load_case_table.png) | Help texts; labels like "Id columns" |
| RBE2 loads | 790 | [nastran.hdf5_rbe2_loads](panels/before/nastran.hdf5_rbe2_loads.png) | Label "Eids"; subcases as free text (the HDF5 results panel has a drop-down); file field shows `${h5_file}` without its value |
| Requirement check | 770 | [check.requirement](panels/before/check.requirement.png) | *Limit* shows an empty field although the limit comes from a connector (Project constants · f_min); *Unit* is free text while other blocks use a unit drop-down |
| Envelope | 690 | [table.envelope](panels/before/table.envelope.png) | Column lists are comma-separated text although the upstream columns are known after a run |
| Constants | 650 | [table.constants](panels/before/table.constants.png) | Descriptions are cut at the right; acceptable otherwise (new in round 4) |
| Read CSV | 645 | [table.read_csv](panels/before/table.read_csv.png) | Four format settings with help texts always open; usually only *File* matters |
| Read Excel, Compute, Pick, Fastener loads, Case labels, Set units, Write Excel, Join, Select, Eigenvalues, Masses, Constant, Convert, Sort, Filter, Stack | 390–590 | e.g. [table.filter](panels/before/table.filter.png) | The frame dominates: in *Filter rows* (410 px) the only parameter takes 70 px, the frame ~300 px |

Findings that apply to all panels:

1. **Header twice.** The title row shows the block type, the line under the label shows the
   block type again with the category.
2. **Description always open** (2–4 lines) although it is read once.
3. **Stored output line** takes two lines and breaks "kB" onto its own line.
4. **Help text under every field** doubles the height; much of it repeats the label
   ("Lines to skip before the header").
5. **Inputs/Outputs list** at the bottom repeats what the canvas shows.
6. **Narrow panel** (340 px, not resizable): long paths wrap, code and descriptions are cut.
7. **No grouping:** main and rarely used settings are mixed in one list.
8. **Parameter labels** are derived from field names: "Eids", "Id columns".
9. **Connected inputs** are not shown where the parameter they replace is (requirement
   limit; the HDF5 results panel already does this for the file).
10. **Inconsistent widgets** for the same kind of value: units as text or drop-down; column
    lists as text or multi-select; subcases as text or drop-down.

## 3. Layout rules (proposal)

| ID | Rule | Prio |
|----|------|------|
| PNL-01 | **Fixed header** that does not scroll: the block label (editable in place), under it one small line "block type · category", on the right *Run to here* and *Delete*. The description is behind an ⓘ button next to the block type (tooltip on hover, click to expand) | M |
| PNL-02 | **One status line** under the header: state and origin of the result ("Done · stored 10:53 · 6.9 kB") with a small *Clear output* icon; validation problems below it in red, one line each | M |
| PNL-03 | **Sections that fold:** *Settings* (open), *Advanced* (folded), *Ports* (folded). Each block marks its rarely changed parameters as advanced (`Field(json_schema_extra={"advanced": True})`), so generated forms get the split without a custom panel. A folded section shows a short summary, e.g. "Advanced · 2 changed" | M |
| PNL-04 | **Help text as tooltip:** an ⓘ icon after the field label shows the description on hover. Examples move into the placeholder of the empty field ("e.g. SUBCASE in [1, 2]"). Only messages that change with the value (errors, "file found", "present: 17 to 19") stay visible under the field | M |
| PNL-05 | **Wider, resizable panel:** default 400 px, the border can be dragged (300–800 px); the width is remembered per browser | M |
| PNL-06 | **Editor dialog for big content:** "Open editor" opens a large dialog (80 % of the window) for content that does not fit a side panel: report sections and items, Python code, a constants table with many rows. The side panel keeps a compact summary ("20 sections · 11 items") | M |
| PNL-07 | **Connected inputs replace their parameter:** when an optional input that overrides a parameter is connected (requirement `limit`, HDF5 `file`), the field is replaced by a read-only chip "from *Project constants* · f_min = 20 Hz" | M |
| PNL-08 | **Paths** are shown relative to the pipeline folder; long ones are shortened in the middle (`…/fem/modal.h5`) with the full path in a tooltip | M |
| PNL-09 | **Readable labels:** every parameter has an explicit title (`Field(title=...)`): "RBE2 element IDs", "ID columns"; units go into the label where fixed | M |
| PNL-10 | **Ports section:** the Inputs/Outputs list moves into the folded *Ports* section (the canvas shows the ports); blocks with editable ports (Python) show their port editor there | S |
| PNL-11 | **Same widget for the same kind of value:** unit labels → unit drop-down; column lists → multi-select with the upstream columns when known (free text when not); subcases → drop-down when the file is readable | S |
| PNL-12 | **Spacing:** dense fields, 8 px between fields, section titles in small caps; no field taller than one line unless it holds text or code | S |

PNL-01 … PNL-05 and PNL-09/10 change the common frame and the generated form: every
panel benefits at once. PNL-06/07/11 are per panel.

## 4. Order of the rework (proposal)

| # | Sub-step | Panels | Why first |
|---|----------|--------|-----------|
| 4.3 | **Common frame**: PNL-01 … 05, 08, 09, 10, 12 in `app.py` and `forms.py`; `advanced` and `title` marks on all block parameters | all generated panels (16 small ones at once) + the frame of the custom ones | Biggest gain for the least code; the custom panels below then only deal with their own content |
| 4.4 | **Word report**: sections and items in the editor dialog (PNL-06); side panel = output file, base document, summary, *Open editor* | report.word | Longest panel (2270 px), used in every pipeline |
| 4.5 | **MSC Nastran analysis**: status lines stay on top; run mode in *Settings*; command, path, options, timeout, messages in *Advanced*; run-mode text once | nastran.run | Second-longest; most settings are set once per computer |
| 4.6 | **Nastran HDF5 results**: taller result list, filters next to it, short file path; RBE2 loads gets the same subcase drop-down (PNL-11) | nastran.hdf5_results, nastran.hdf5_rbe2_loads | Used the most when building a pipeline |
| 4.7 | **Python**: script path short, code in the editor dialog, ports in *Ports* (no double list) | python.script | |
| 4.8 | **Requirement check** (PNL-07 connected limit, unit drop-down) and **table blocks** (column multi-selects, PNL-11): envelope, load case table, select, sort, join, compute, pick | check.requirement, table.* | Smaller changes, many blocks |
| 4.9 | **Remaining**: fastener loads, Excel in/out, CSV, constants (wider description, editor dialog when > 8 rows) | | |

Each sub-step: short note in this spec → code → screenshots next to the "before" ones
(`panels/after/`) → your check → commit when you ask.

## 5. Out of scope (this step)

A different overall window layout (panel positions, preview/log area), themes (GUI-16),
editing several blocks at once, keyboard shortcuts.

## 6. Decisions (review 2026-11-21)

1. Panel width: 400 px by default, resizable — to be tried.
2. Description: behind the ⓘ button.
3. Editor: a large dialog over the canvas.
4. Order as in §4.

## 7. Progress

### 4.3 Common frame — done (2026-11-21)

Implemented: PNL-01 (fixed header: editable label as title, "Block type · category" with an
ⓘ that shows the description), PNL-02 (one status line, *Clear output* as icon), PNL-03
(*Settings*, folded *Advanced* with "· n changed", folded *Ports*), PNL-04 (help texts
behind ⓘ, examples as placeholders), PNL-05 (400 px, drag the left border 300–800 px,
kept per browser), PNL-08 (short relative paths with the full path as tooltip, in the
HDF5 and Python panels), PNL-09 (titles for unclear parameter names), PNL-10, PNL-12.

Implementation notes: the rarely changed parameters are listed per block type in
`Block.advanced_params` (instead of a mark on each field), e.g. the four format settings of
*Read CSV*; generated forms fold them automatically. Custom panels (report, Python,
Nastran, HDF5, constants) get the new frame now and their own layout in the next
sub-steps. Screenshots: [`panels/after/`](panels/after/), made with
`uv run python tools/panel_screenshots.py docs/spec/panels/after`.

| Block type | Before (px) | After 4.3 (px) |
|------------|------------:|---------------:|
| report.word | 2270 | 1977 |
| python.script | 1310 | 961 |
| nastran.run | 1275 | 766 |
| nastran.hdf5_results | 1090 | 841 |
| table.load_case_table | 850 | 404 |
| nastran.hdf5_rbe2_loads | 790 | 445 |
| check.requirement | 770 | 353 |
| table.envelope | 690 | 353 |
| table.constants | 650 | 439 |
| table.read_csv | 645 | 261 |
| table.filter | 412 | 223 |

### 4.4 Word report — design

- **Side panel:** *Output file*; a summary card "20 sections · 11 items", with the number of
  problems (sections with warnings, items used nowhere, report-level messages) and the first
  problem; **Open editor**; *Advanced*: base document.
- **Editor dialog** (80 % × 85 % of the window, over the canvas), title "Report editor —
  *label*", three tabs:
  - *Sections*: the section list on the left (40 %, full summaries, add / move / duplicate
    / delete as today) and the settings of the selected section on the right — no more
    accordion inside a narrow column;
  - *Items*: the connected items with their names (rename as today);
  - *Document*: title, document number, revision, author, date (the metadata).
- Every change is stored at once, as before; *Close* just closes the dialog and the summary
  in the side panel is updated. The pipeline file format does not change.

### 4.4 Word report — done (2026-11-21)

As designed above. Side panel 2270 px → 381 px. The dialog keeps the selected section
while it is open; the summary of the side panel is updated on every change and when the
dialog closes. The `sections` parameter given as a `${variable}` still gets its plain field
(in the *Sections* tab).

### 4.5 MSC Nastran analysis — done (2026-11-21)

*Settings*: input deck, the status lines (command, program found, input, results and how
they relate to the input), *Open .f06* / *Open folder*, run mode, fail on. *Advanced*
(folded, "· n changed"): command, path, options, time limit, ignored message numbers,
information messages. The run-mode explanation is only in the ⓘ of the run mode. A found
program is shown with a short path (full path as tooltip). 1275 px → 525 px.

### 4.6 Nastran HDF5 results and RBE2 loads — done (2026-11-21)

- *HDF5 results*: a connected file is a "from block" line with a short path (PNL-07); the
  file information and the *Open in viewer* icon share one row; the results list is about
  twice as tall (26 rem, ~18 results visible); the filters follow under the title
  "Filters · *result*"; their help texts are behind the ⓘ.
- The generated form got two general options used by the other HDF5 blocks (RBE2 loads,
  eigenvalues, point masses): a parameter delivered by a connected input is shown as a
  "from block" line instead of its field (PNL-07), and a list parameter can offer the values
  present (PNL-11): `subcases` is a drop-down of the subcases in the result file (free
  text when the file cannot be read, or for a `${variable}`).

### 4.7 Python — done (2026-11-21)

*Settings*: script file, a short folder line ("scripts · changed …", full path as
tooltip), the check result with a ↻ *Check* icon, the first ~10 lines of the code (click
to open the large editor), *Edit* and an *Open in editor* icon (system program).
*Advanced*: inline code (in *Settings* instead when the block uses inline code and no
file). The port editors moved into the *Ports* section, which replaces the read-only port
list for this block, so the ports are no longer shown twice. 1310 px → 512 px.

### 4.8 Requirement check and table blocks — design

**Requirement check** (`check.requirement`)

- *Limit*: when the `limit` input is connected, the field is replaced by the "from block"
  line (PNL-07): "Limit · from *Project constants* · f_min = 20 Hz". The value is shown
  when the upstream block has an output (last run or stored), else only the block name.
- *Comparison*: the drop-down shows symbols with words: "> greater than", "≥ greater or
  equal", "< less than", "≤ less or equal" (stored values unchanged).
- *Unit*: a drop-down of the known units with "(none)" (same list as the Constants block);
  stays under *Advanced*, since the unit normally comes with the value or limit.

**Column choices for table blocks** (PNL-11)

- A column parameter is marked in its block's `Params` with
  `json_schema_extra={"apb_columns": "<input port>"}`. The form takes the columns of the
  table that arrives on that port from the upstream block's output (last run or stored
  output). Nothing new is computed or run to find them.
- Known columns → list parameters get a multi-select with chips (the order of selection
  is kept, e.g. *Sort by*), single-column parameters a drop-down. Both still accept a typed
  name (for a column that only appears after an upstream change); a stored name that is
  not in the table is shown as "EID (not in table)".
- Columns not known (upstream not run yet, or a `${variable}` value) → the text field as
  today, with the hint "Run the upstream block to choose from its columns".
- Join: the key columns offered are those present in *both* the left and the right table.

| Block | Parameters with column choices |
|-------|-------------------------------|
| Select columns | Columns to keep |
| Sort | Sort by |
| Envelope | Group by, Value columns, Governing columns |
| Join | Key columns (both tables) |
| Load case table | ID columns, Load case column, Value columns |
| Pick value | Column |

**Unit drop-downs** (PNL-11): the `unit` of *Compute* and *Constant value* gets the same
unit drop-down as the requirement check (marker `apb_widget: "unit"`). *Constant value*
already accepts only known units. *Compute* accepts any label today, so its drop-down also
takes a typed label that is not in the list.

Not in 4.8: *Rename* mappings of *Select columns* (key/value editor stays), filter and
compute expressions (no column completion yet).

### 4.8 Requirement check and table blocks — done (2026-11-22)

Implemented as designed above. The generated form knows three new field marks:
`apb_columns: "<port>"` (or `"left,right"` for columns in both tables), `apb_widget: "unit"`
(with `apb_other: True` for free labels, *Compute*) and `apb_labels` (display labels of a
drop-down). Any parameter with the same name as a connected input is now shown as a "from
block" line, not only the HDF5 file. *Envelope*'s `columns` got the title "Value columns".
Screenshots in [`panels/after/`](panels/after/), e.g.
[check.requirement](panels/after/check.requirement.png),
[table.envelope](panels/after/table.envelope.png), [table.join](panels/after/table.join.png).
