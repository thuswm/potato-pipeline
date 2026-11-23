# 03 — Table Blocks (data manipulation, Excel & CSV)

## 1. Purpose

Replace the typical Excel steps between Nastran and the report with explicit, re-runnable
blocks. All table blocks take and return `pandas.DataFrame`s.

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| TAB-01 | Every table block has a data preview in the GUI (first N rows, row/column count) | M |
| TAB-02 | Blocks never modify their input in place (copy-on-write) | M |
| TAB-03 | Column selection parameters offer the actual column names of the connected input (drop-down) when available | S |
| TAB-04 | Each block validates that referenced columns exist and gives a clear error otherwise | M |

## 3. v1 block set

| type_id | Title | Inputs → Outputs | Params |
|---------|-------|------------------|--------|
| `table.read_excel` | Read Excel | – → `table` | `file`, `sheet`, `header_row`, `range` (optional, e.g. `A1:F200`) |
| `table.read_csv` | Read CSV | – → `table` | `file`, `delimiter`, `decimal` (`.`/`,`), `skip_rows`, `encoding` |
| `table.write_excel` | Write Excel | `tables` (multiple) → `file` (Text) | `file`, `sheet_names`, `units_in_header` (see §3.3) |
| `table.write_csv` | Write CSV | `table` → `file` (Text) | `file`, `separator`, `decimal`, `units`, `encoding` (see §3.12) |
| `table.select` | Select / rename columns | `table` → `table` | `columns`, `rename` (rename table, §3.13) |
| `table.filter` | Filter rows | `table` → `table` | `expression` (pandas query syntax, e.g. `SUBCASE in [1,2] and EID > 1000`) |
| `table.sort` | Sort | `table` → `table` | `by`, `ascending` |
| `table.envelope` | Envelope | `table` → `table` | `group_by` (empty = whole table), `columns`, `mode` (`max`, `min`, `abs_max` – sign kept), `governing_columns` (columns of the governing row reported as `<value>_<column>`, default `SUBCASE`; e.g. add `EID` to see which element governs), `keep_row` (full governing row, one value column) |
| `table.join` | Join | `left`, `right` → `table` | `on`, `how` (inner/left/right/outer); clashing right columns get `_RIGHT` |
| `table.concat` | Stack tables | `tables` (multiple) → `table` | `add_source_column` (`SOURCE` = file name or title) |
| `table.constants` | Constants | – → `table` + one Scalar port per constant | `constants` (rows: name, value, unit, description), see §3.5 |
| `table.compute` | Computed column | `table` → `table` | `new_column`, `expression` (pandas eval, e.g. `sqrt(FX**2 + FY**2)`), `unit` |
| `table.pick` | Pick value | `table` → `scalar` | `column`, `row` (`max`, `min`, `abs_max`, `first`, `last`), `where` (optional filter); value carries the column unit |
| `table.constant` | Constant | – → `scalar` | `value`, `unit` (must be a known unit label) |
| `units.convert` | Convert units | `value` (Any: table or scalar with unit) → same | `to` (`SI` / `SI_mm`); uses the units metadata (spec 00 §5.1) |
| `table.set_units` | Set units | `table` → `table` | `unit_system`, `units` (column → unit label, e.g. `MPa`), `replace` (for tables from Excel/CSV) |
| `table.load_case_table` | Load case table | `table`, `labels` (optional names table) → `table` | `id_columns` (default `EID`), `case_column` (default `SUBCASE`), `value_columns`, `envelope` (list of `max`/`min`/`abs_max`, each adds `<NAME>` and `<NAME>_CASE`), `layout` (`side_by_side` / `flat`), `labels` (case ID → name, overrides the input) |
| `check.requirement` | Requirement check | `value`, `limit` (optional) → `result` (table), `status` (text), `margin` | `requirement`, `comparison`, `limit`, `unit` (see §3.4) |
| `table.case_labels` | Load case names | – → `table` | `labels` (subcase ID → name); output `SUBCASE, LABEL`, connects to `load_case_table.labels` or joins onto any result |

Expressions use pandas' `query`/`eval`, which are restricted expression languages (no
arbitrary code). Anything more complex goes into the Python block (spec 04).

### 3.1 Collecting results over load cases

This is the main Excel task today and gets first-class support:

- **Long format** (one row per entity *and* load case) is what the Nastran block delivers and
  what the blocks above work on.
- **`table.load_case_table`** turns it into the **wide format** analysts put in reports and
  spreadsheets: one row per entity, one column per load case (`LC1`, `LC2`, … or names from
  `case_labels`), optionally followed by envelope columns, e.g. `MAX`, `MAX_CASE`.
- Load cases without a value for an entity are left empty; the envelope ignores them.
  Several rows per entity and load case (e.g. CBEAM stations) are an error: filter first or
  add ID columns.
- With **several `value_columns`** (e.g. `TENSION` and `SHEAR`) the groups are placed **side by
  side in one table** with a two-level header, which `table.write_excel` writes to one sheet
  and the report writes as one table:

  ```
  |      |   TENSION [N]                  |   SHEAR [N]                    |
  | EID  | LC1 | LC2 | LC3 | MAX | MAX_CASE | LC1 | LC2 | LC3 | MAX | MAX_CASE |
  ```
- Results from **several `.h5` files** (separate runs) are combined with `table.concat`
  (`add_source_column=True`), so load cases from different runs end up in one table.

### 3.2 Fastener loads (`loads.fastener_loads`)

| | |
|---|---|
| Inputs | `forces: Table` (CBUSH element forces or RBE2 loads from spec 02, long format) |
| Outputs | `loads: Table` — `EID, SUBCASE, TENSION, SHEAR` (+ original force columns if wanted) |
| Params | `axial_axis` (`X`/`Y`/`Z` of the force components, default `X`), `tension_mode` (`signed` = keep compression as negative, `tension_only` = compression set to 0), `include_moments` (adds `BENDING`, `TORSION`), `keep_forces` |

- `TENSION` = force along `axial_axis`; `SHEAR` = √(F_a² + F_b²) of the two other components.
- Typical chain: Nastran CBUSH forces → `loads.fastener_loads` → `table.load_case_table`
  (value `TENSION`, then `SHEAR`, each with envelope) → report / Excel.
- Optional `fastener_ids` mapping (EID → fastener name/position) via `table.join`.
- This block is the seed of the **fastener library** (your planned separate project); the
  detailed fastener calculations (bearing, pull-through, interaction, margins) will live in
  that project as an installable block package (ARC-08).

### 3.3 Write Excel (`table.write_excel`)

- One sheet per connected table, in connection order. Sheet name: the user's name for that
  input, else the table title (e.g. "CQUAD4 stresses"), else `Table1`, `Table2` …; invalid
  characters replaced, cut to 31 characters, duplicates numbered.
- Column headers with units (`SX_Z1 [MPa]`) unless `units_in_header` is off; header row
  frozen, column widths fitted to the content; no index column.
- Relative paths are relative to the output folder (default: the pipeline folder;
  `apb run --output-dir` changes it).
- Written via a temporary file and then moved into place: a failed run never leaves a
  half-written workbook. If the workbook is open in Excel (Windows file lock) the block
  fails with "close it in Excel and run again".
- Always runs (ENG-17), so a deleted or edited workbook is rewritten on the next run.
- Two-level headers (side-by-side load case table): top level written once as a merged cell
  with unit, load case names below, header rows frozen.

- **Sheet name = table title (round 6, answer to the Windows test).** The title is not a
  column: it travels with the table in `DataFrame.attrs["title"]`. The block that makes a
  table sets it (HDF5 result → its display name, e.g. "CBUSH forces"; CSV/Excel reader →
  file or sheet name; the f06 blocks → "Modal effective mass fractions"; the requirement
  check → "Requirement check"). Table blocks keep it (`carry_units`). A Python block can
  set it with `df.attrs["title"] = "..."`. The preview shows it above the table (§3.14).

### 3.4 Requirement check (`check.requirement`)

Compares one value with a requirement and says pass or fail — the typical "the first
eigenfrequency must be higher than X Hz" of a report. It does not stop the pipeline when the
requirement is not met: the report must still be produced, showing the FAIL.

| | |
|---|---|
| Category | Checks |
| Inputs | `value: Scalar`; `limit: Scalar` (optional — overrides the `limit` parameter, e.g. from a Constant block) |
| Outputs | `result: Table` (one row, CHK-04), `status: Text` (`PASS` / `FAIL`), `margin: Scalar` |
| Params | `requirement` (text: id and description, e.g. `REQ-DYN-001 First eigenfrequency`), `comparison` (`greater_than`, `greater_equal`, `less_than`, `less_equal`), `limit` (number or `${variable}`; empty if the `limit` input is connected), `unit` (unit label shown when neither value nor limit carries one) |

| ID | Requirement | Prio |
|----|-------------|------|
| CHK-01 | `PASS` if `value <comparison> limit` holds on the numbers; equality passes only for `greater_equal` / `less_equal` | M |
| CHK-02 | **Margin of safety** `MS = value / limit − 1` for `greater_*`, `MS = limit / value − 1` for `less_*`; `PASS` corresponds to `MS > 0` (`≥ 0` with equality allowed). If the margin is undefined (limit ≤ 0 for `greater_*`, value ≤ 0 for `less_*`) it is empty (NaN) and a line in the block log says why; the status is still decided by CHK-01 | M |
| CHK-03 | Units: if the value carries a unit and the limit is given as a number, the number is read in the unit of the value (logged: "limit 20 interpreted as Hz"). If value and limit both carry units they must be equal, otherwise the block fails and recommends *Convert units* (spec 00 §5.1) | M |
| CHK-04 | The result table has the columns `REQUIREMENT`, `VALUE`, `CONDITION` (`>`, `≥`, `<`, `≤`), `LIMIT`, `MARGIN`, `STATUS`; `VALUE` and `LIMIT` carry the unit, `MARGIN` is dimensionless; it can go to the report as a table, to Excel, or be stacked with other checks (`table.concat`) into one requirements table | M |
| CHK-05 | A failed requirement is logged with the prefix `WARNING:` (like the synthetic-file notice); the block itself is `done`, not in error. A missing value (NaN) or missing limit is an error | M |
| CHK-06 | `status` and `margin` can be used in report text, e.g. "The requirement is {status} (margin {margin:.2f})." — with several checks give the connectors names (spec 05 §3.4, *Items*) | M |
| CHK-07 | Checks on whole tables (every row against its own limit, with the worst row reported) — the margin table of the Python block covers this for now | L |

Implementation: `apb.blocks.check.requirement`; the block is in the library category
*Checks*. The value passed to the `limit` input wins over the `limit` parameter; a `limit`
given as `Constant` with a unit (as in the test case) needs no unit interpretation.

Example (test case, spec 07): `table.pick` takes the smallest `FREQUENCY` of the modal
results (25.71 Hz), `check.requirement` with `greater_than` and limit `${min_frequency}`
(20 Hz) gives `PASS`, margin 0.286.

### 3.5 Constants (`table.constants`) — approved 2026-11-21 (round 4 step 2)

One block that holds the **project constants** of an analysis: safety factors, allowables,
requirement limits, fitting factors. Today each needs its own *Constant* block
(`table.constant`) or a pipeline variable, the values are scattered, and the report has no
single "constants used" table. The Constants block keeps them in one table that the analyst
edits in one place and that goes into the report as it is.

| | |
|---|---|
| Category | Input/Values |
| Inputs | none |
| Outputs | `table: Table` (all constants, CON-05); one `Scalar` port **per constant**, named after it (CON-06) |
| Params | `constants`: list of rows `{name, value, unit, description}` |

Example:

| Name | Value | Unit | Description |
|------|-------|------|-------------|
| `SF_yield` | 1.25 | – | Safety factor on yield |
| `SF_ult` | 1.5 | – | Safety factor on ultimate |
| `f_min` | 20 | Hz | Required first eigenfrequency (REQ-DYN-001) |
| `FF_bolt` | 1.15 | – | Fitting factor, bolted joints |

| ID | Requirement | Prio |
|----|-------------|------|
| CON-01 | **Name:** a letter or `_` first, then letters, digits and `_` (no spaces): the Python identifier rule, so a name also works in Python scripts and in `${name}`. Case-sensitive. Unique within the block, across all Constants blocks of the pipeline, and with the pipeline variables (CON-08) | M |
| CON-02 | **Value:** a finite number (no text, CON-11). Typed values like `1.5`, `2e5`, `20` | M |
| CON-03 | **Unit:** optional unit label from the unit table (spec 00 §5.1, as for *Constant*), empty = dimensionless; shown wherever the constant is shown, so units are always visible in reports | M |
| CON-04 | **Description:** free text, one line (what the constant is and where it comes from, e.g. a requirement ID or a standard) | M |
| CON-05 | Output `table`: columns `NAME`, `VALUE`, `UNIT`, `DESCRIPTION`, one row per constant in the order of the editor. It goes into the Word report like any table (e.g. caption "Constants used in this analysis") or into Excel | M |
| CON-06 | One output port per constant, named after it, delivering the value with its unit (`Quantity`, as *Constant* does). It connects to anything that takes a Scalar: the `limit` of a requirement check, a Python block input, a report item (the connector name defaults to the constant name, so report text can say `{SF_yield:.2f}`) | M |
| CON-07 | **`${name}` in parameters:** every constant of every Constants block in the pipeline can be used in any block parameter as `${name}`, exactly like a pipeline variable (spec 01 ENG-14), e.g. a *Computed column* expression `FZ * ${SF_ult}` or the `limit` of a requirement check. No connector is needed (§3.5.2) | M |
| CON-08 | Name clashes (two constants with the same name, or a constant and a pipeline variable) are validation errors that name both places; the pipeline does not run until they are fixed | M |
| CON-09 | `apb run --set SF_yield=1.4` overrides a constant like a variable (parameter studies) | S |
| CON-10 | Property panel: the constants as an editable table — add, delete, move up/down; inline errors per row (CON-01…03); a "used by" list per constant (blocks that reference it through a port or `${name}`) | M |
| CON-11 | Text values (e.g. a material name) | L |
| CON-12 | Import/export of the constants from/to Excel or CSV | L |

#### 3.5.1 Decisions (review 2026-11-21: all accepted)

1. **Numbers only** (CON-02, CON-11 later). Constants are engineering values that are
   compared, multiplied and reported with units; text values (document number, material
   name, file names) stay pipeline variables. This keeps the table typed and the `VALUE`
   column numeric in Excel and in the report.
2. **Unit column: yes** (CON-03). Units must always be visible in reports.
3. **Two ways to use a constant, both without a new input on other blocks:**
   - **Connector from its own port** (CON-06) where a block has a Scalar input: requirement
     check `limit`, Python block inputs, report items. The canvas shows the dependency.
   - **`${name}`** (CON-07) in any parameter, also where there is no port (expressions,
     filters, number fields). This works because a Constants block has no inputs: its values
     are known before the run, so they are resolved like pipeline variables while the
     pipeline is prepared. The cache stays right without a connector: the resolved value is
     part of the receiving block's cache key, so changing a constant re-runs exactly the
     blocks that use it.

   This replaces the plan's first idea (a new optional `constants` input on the requirement
   check, Python and report blocks): fewer changes, works for every block type, and the
   canvas is not filled with extra connectors. The "used by" list (CON-10) shows the
   `${name}` dependencies that the canvas does not.
4. **Pipeline variables stay**, with a clear split: **Constants block** = engineering values
   with units that belong in the report; **pipeline variables** (*Pipeline > Variables and
   settings*) = file names, document number, revision and other text. The variables dialog
   gets a note pointing to the Constants block for engineering values. Merging the two can
   be revisited when text constants (CON-11) come.
5. **The single-value *Constant* block (`table.constant`) stays** for existing pipelines and
   quick use; its library description says "for several values use *Constants*". No
   automatic conversion.

#### 3.5.2 Engine

- While preparing a pipeline (`engine/graph.prepare`), the constants of all
  `table.constants` blocks are collected **before** parameters are resolved, and added to
  the variables: CLI overrides (`--set`) > constants > pipeline variables, with clashes
  between constants and variables reported (CON-08). Only the stored parameters of the
  Constants blocks are read; nothing has to run first.
- A Constants block's own parameters may not contain `${…}` (its values must be known
  without resolving anything); this is a validation error.
- A Constants block that is invalid (e.g. a bad name) still lets the other blocks be
  checked: its constants are left out and blocks that use them report "undefined
  variable", next to the Constants block's own error.
- The per-constant ports are dynamic ports derived from the parameters, like the Python
  block's ports (spec 04).
- **Exact staleness through ports.** A downstream cache key normally contains the key of
  the whole upstream block, so changing `f_min` would also re-run the margin scripts that
  only use `SF`. A constant's port therefore has its own fingerprint (name, value, unit):
  blocks connected to it go stale only when that constant changes. The `table` output
  depends on every row (including descriptions) and uses the block's key.
- Engine hooks on `Block` (generic, not specific to this block): `provides_variables` /
  `provided_variables(params)` (collected by `graph.collect_variables` before parameters
  are resolved), `apply_overrides(params, overrides)` (CLI `--set` also changes the
  block's own outputs, CON-09) and `port_fingerprint(params, port)`.

#### 3.5.3 GUI

- Panel: one card per constant with Name, Value, Unit (drop-down of the known units, or
  none) and Description; *Add constant*, and per row *up*, *down*, *delete*. A row is taken
  when Enter is pressed or the field is left. An error is shown under its row; an invalid
  row is not stored (the last valid version stays, so a half-typed name never removes a
  port and its connectors). Below each valid row: "Used by: ..." or "Not used yet". Values
  accept `1.5`, `2e5` and a decimal comma.
- **Renaming** a constant keeps the connectors of its port and updates `${old}` references
  in all blocks of the pipeline (an escaped literal is left alone); report texts that use
  the constant through a connector without explicit name follow the new item name. The
  changes are listed in a notification and in the log. Deleting a constant that is still used asks first and lists
  the users.
- On the canvas the block shows one output port per constant plus `table`.

#### 3.5.4 Test case (spec 07)

The test case gets one Constants block *Project constants* with `f_min` = 20 Hz
(REQ-DYN-001) and `SF` = 1.5 (CBUSH loads). It replaces the two *Constant* blocks and the
pipeline variables `min_frequency` and `safety_factor`; `doc_number` and `revision` stay
variables. Ports feed the requirement check `limit`, both margin scripts and the report
items; the report gets a "Constants used" table.

Implementation: `apb.blocks.table.constants` (block and params model),
`gui/constants_panel.py` (editor), `engine/graph.py` (collection and clash check).

### 3.12 Write CSV (`table.write_csv`) — round 6

- **One table, one file.** CSV has no sheets, so the block has one table input; one block
  per file. (The plan proposed one file per connected table; a single input is clearer and
  needs no file naming rule.)
- `separator`: `,` (default), `;` or tab; `decimal`: `.` (default) or `,`. They must
  differ. Excel with Swedish or most other European regional settings opens `;` + `,`
  directly.
- `units` (advanced): `header` (default, `S [MPa]`), `row` (a second header row with the
  units) or `none`.
- `encoding` (advanced): `utf-8-sig` (default: UTF-8 with byte order mark, so Excel shows
  `°C` and `µm` correctly), `utf-8`, `cp1252`.
- Two-level headers are joined into one (`TENSION_LC1`). Line ending `\r\n` on every
  platform. No index column.
- Relative paths are relative to the output folder; written via a temporary file (a file
  open in Excel gives "close it and run again"); always runs, like Write Excel.

### 3.13 Renaming columns — round 6

Found in the Windows test: renaming table headers was wanted; the *Select* block could
already do it, but the rename field was an empty key/value list that was hard to find.

- The block is called **Select / rename columns** (type id unchanged).
- Once the input table is known (upstream has run), *Rename* is a table: one row per kept
  column (the selected columns, else all), the old name and a box for the new name; empty =
  unchanged. Changing the selected columns updates the rename table at once. Entries for
  columns that are not kept are listed in red with a remove button. Before the upstream has
  run the key/value list is shown as before.
- Units move with the renamed column (as before). The data processing tool (round 7) will
  cover renaming too.

### 3.14 Table title in the preview — round 6

The preview shows the table title (`attrs["title"]`) above the row count, with a tooltip
saying that it is the default sheet name. The *Table calculation* Python template sets a
title, as an example.

## 4. Out of scope (v1)

General pivot tables beyond the load case table, unit systems other than `SI`/`SI_mm`, interpolation blocks,
writing back into existing formatted Excel workbooks (only new workbooks are written),
fastener strength calculations (separate project).

## 5. Open questions

1. Tension axis convention for fasteners — to be specified later. Until then it is a block
   parameter (`axial_axis`). CBUSH output is not always fastener related, so the Nastran
   block stays generic and the fastener interpretation lives only in `loads.fastener_loads`.
