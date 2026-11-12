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
| `table.select` | Select columns | `table` → `table` | `columns`, `rename` (mapping) |
| `table.filter` | Filter rows | `table` → `table` | `expression` (pandas query syntax, e.g. `SUBCASE in [1,2] and EID > 1000`) |
| `table.sort` | Sort | `table` → `table` | `by`, `ascending` |
| `table.envelope` | Envelope | `table` → `table` | `group_by` (empty = whole table), `columns`, `mode` (`max`, `min`, `abs_max` – sign kept), `governing_columns` (columns of the governing row reported as `<value>_<column>`, default `SUBCASE`; e.g. add `EID` to see which element governs), `keep_row` (full governing row, one value column) |
| `table.join` | Join | `left`, `right` → `table` | `on`, `how` (inner/left/right/outer); clashing right columns get `_RIGHT` |
| `table.concat` | Stack tables | `tables` (multiple) → `table` | `add_source_column` (`SOURCE` = file name or title) |
| `table.compute` | Computed column | `table` → `table` | `new_column`, `expression` (pandas eval, e.g. `sqrt(FX**2 + FY**2)`), `unit` |
| `table.pick` | Pick value | `table` → `scalar` | `column`, `row` (`max`, `min`, `abs_max`, `first`, `last`), `where` (optional filter); value carries the column unit |
| `table.constant` | Constant | – → `scalar` | `value`, `unit` (must be a known unit label) |
| `units.convert` | Convert units | `value` (Any: table or scalar with unit) → same | `to` (`SI` / `SI_mm`); uses the units metadata (spec 00 §5.1) |
| `table.set_units` | Set units | `table` → `table` | `unit_system`, `units` (column → unit label, e.g. `MPa`), `replace` (for tables from Excel/CSV) |
| `table.load_case_table` | Load case table | `table`, `labels` (optional names table) → `table` | `id_columns` (default `EID`), `case_column` (default `SUBCASE`), `value_columns`, `envelope` (list of `max`/`min`/`abs_max`, each adds `<NAME>` and `<NAME>_CASE`), `layout` (`side_by_side` / `flat`), `labels` (case ID → name, overrides the input) |
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

## 4. Out of scope (v1)

General pivot tables beyond the load case table, unit systems other than `SI`/`SI_mm`, interpolation blocks,
writing back into existing formatted Excel workbooks (only new workbooks are written),
fastener strength calculations (separate project).

## 5. Open questions

1. Tension axis convention for fasteners — to be specified later. Until then it is a block
   parameter (`axial_axis`). CBUSH output is not always fastener related, so the Nastran
   block stays generic and the fastener interpretation lives only in `loads.fastener_loads`.
