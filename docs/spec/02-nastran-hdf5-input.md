# 02 — Nastran HDF5 Input Block

## 1. Purpose

Bring MSC Nastran results into the pipeline as tables. Source: the HDF5 result file (`.h5`)
written by MSC Nastran (requested with e.g. `MDLPRM,HDF5,0` in the bulk data). Reader: `h5py`.
pyNastran is deliberately **not** used.

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| NAS-01 | Open an `.h5` file and list the result datasets it contains (with row counts) | M |
| NAS-02 | Read a selected result dataset into a `pandas.DataFrame` | M |
| NAS-03 | Join each row with its **domain** (subcase, mode, time/frequency, eigenvalue …) so the table has a `SUBCASE` column etc. | M |
| NAS-04 | Filter on subcases and on entity IDs (GRID / element IDs, ranges like `1001-1200, 3005`) | M |
| NAS-05 | Multi-dimensional fields (e.g. stress at several fibres / corner points) are flattened into named columns | M |
| NAS-06 | v1 supported results (see §3.5): nodal displacement, SPC force, MPC force, grid point force balance; CQUAD4 forces, stresses and strains; CBEAM forces and stresses; CBUSH forces; RBE2 loads; eigenvalue summary | M |
| NAS-06a | **RBE2 loads per element**: forces carried by each RBE2, identified by its element ID (used for fastener load output), see §3.6 | M |
| NAS-06b | Point masses: list CONM2 data (grid, mass, offsets) from the model part of the file, and the grid point weight generator summary (total mass, CG) if present | S |
| NAS-06c | Further element types (CTRIA3, CQUAD8, CBAR, CROD, CELAS, solids) handled by the generic "raw" reader (NAS-07), dedicated support added on demand | L |
| NAS-07 | Unknown/other datasets can still be read "raw" (all columns, no special handling) | S |
| NAS-08 | Parameter `unit_system` (`SI` / `SI_mm`); unit labels attached to every column from its quantity kind (spec 00 §5.1) | M |
| NAS-09 | Reading is lazy: only the selected dataset and rows are loaded (important for large files) | S |
| NAS-10 | The file is reported via `input_files()` so a changed `.h5` invalidates the cache | M |
| NAS-11 | **Result browser:** when an `.h5` file is chosen in the GUI, the property panel lists every result in the file (display name, dataset path, row count), grouped and searchable; clicking a result selects it as the block's output (§3.7) | M |
| NAS-12 | **Peek:** the selected result is shown in the preview panel straight from the file, with the current filters applied, without running the pipeline | M |
| NAS-13 | The subcase and column choices are filled from the selected result (only subcases/columns that exist); the ID range and number of IDs present are shown next to the ID filter | M |
| NAS-14 | File information (Nastran version, solution number, input deck name, run date) is shown in the browser and attached to the output table for traceability | S |
| NAS-15 | Matrix results (effective mass matrix, rigid body mass matrix) are returned as a 6 x 6 table, not as one row with 36 columns (§3.8) | M |
| NAS-16 | Modal effective mass results (`MEFFMASS` request) are supported with units: modal effective mass, effective weight, mass fractions, participation factors, total mass fraction | M |
| NAS-17 | The reader is tested against real MSC Nastran 2023.3 files kept in the repository (`examples/fem/`) | M |
| NAS-18 | Grid point weight generator output as a mass / centre of gravity table (§3.8) | S |
| NAS-19 | Browse and read the model data (`/NASTRAN/INPUT`, e.g. CBUSH connectivity) through the same browser. *Browsing* is done by the HDF5 viewer (spec 10, button **Open in viewer** in this panel); reading model tables into the pipeline stays later | L |
| NAS-20 | The result blocks (results, eigenvalues, RBE2 loads, point masses) accept the `.h5` path from a connector (`file` input), so an analysis block can feed them (spec 09 §3.8) | M |

## 3. Design

### 3.1 Module split

- `apb.blocks.nastran.h5reader` — **pure library**, no block/GUI code:
  - `list_results(path) -> list[ResultInfo]`
  - `read_result(path, result, subcases=None, ids=None) -> pd.DataFrame`
  - `read_domains(path) -> pd.DataFrame`
- `apb.blocks.nastran.blocks` — the pipeline blocks using the reader.

This keeps the reader reusable (e.g. by your future fastener project).

### 3.2 HDF5 layout and version tolerance

The reference version is **MSC Nastran 2023.3**: the mapping table is written and tested
against its HDF5 schema. The reader is still written to be tolerant rather than tied to one
schema, so older/newer versions only need mapping entries:

- Datasets are discovered by walking the file (`h5py.File.visititems`), not by hard-coded
  lists only.
- Column names are read from the compound dtype of each dataset; the per-result knowledge
  (which columns are IDs, which are multi-valued and need flattening, and the **quantity kind**
  of each column — length, force, moment, stress … — used for unit labels) lives in a small
  **mapping table** (a Python dict/JSON in the package), keyed by dataset path. A version
  that renames a field only needs a mapping entry, not new code.
- Unknown datasets fall back to the raw reader (NAS-07).
- The Nastran version string stored in the file (if present) is logged and attached to the
  output table as `attrs["nastran_version"]` for traceability in the report.
- Tests use the **real reference files** in `examples/fem/` (§3.9) for everything they
  contain. The **synthetic `.h5` fixtures** (`examples/data/`) remain only for results the
  real files do not contain (CQUAD4, CBEAM, MPC forces, grid point forces); those mapping
  entries are still unverified.

Layout (confirmed against MSC Nastran 2023.3 files, 2026-11-15):

```
/NASTRAN/RESULT/DOMAINS                      # ID, SUBCASE, STEP, ANALYSIS, TIME_FREQ_EIGR, MODE, ...
/NASTRAN/RESULT/NODAL/DISPLACEMENT           # ID, X, Y, Z, RX, RY, RZ, DOMAIN_ID
/NASTRAN/RESULT/NODAL/SPC_FORCE
/NASTRAN/RESULT/ELEMENTAL/STRESS/QUAD4       # EID, fibre/stress fields ..., DOMAIN_ID
/NASTRAN/RESULT/ELEMENTAL/ELEMENT_FORCE/BAR
/NASTRAN/RESULT/SUMMARY/EIGENVALUE
/NASTRAN/RESULT/MODAL/MODAL_EFFECTIVE_MASS   # MODE, FREQUENCY, T1..R3, DOMAIN_ID (MEFFMASS request)
/NASTRAN/INPUT/...                           # model data (CONM2 and RBE2 connectivity are used)
/INDEX/NASTRAN/RESULT/...                    # per dataset: DOMAIN_ID, POSITION, LENGTH
```

File information is stored as attributes of the `/NASTRAN` group: `VERSION` (e.g.
`msc20233`), `SOL`, `INPUT` (deck name) and `TIME`.

Datasets are HDF5 compound arrays → read with h5py into a NumPy structured array → DataFrame.
Each result row has `DOMAIN_ID`, joined against `/NASTRAN/RESULT/DOMAINS` (NAS-03).
Version differences in the schema are handled by a small mapping table per supported Nastran
version; the reader detects the version from file attributes where possible.

### 3.3 Blocks

**`nastran.hdf5_results`** — "Nastran HDF5 results"

| | |
|---|---|
| Inputs | none |
| Outputs | `table: Table` |
| Params | `file: Path`, `result: str` (dataset, chosen from a drop-down filled by `list_results`), `subcases: list[int]` (empty = all), `ids: str` (ID filter expression, empty = all), `columns: list[str]` (empty = all), `unit_system` (`SI` / `SI_mm`, default from pipeline setting) |

**`nastran.hdf5_eigenvalues`** — "Nastran eigenvalues" → `table` with mode, eigenvalue,
frequency [Hz], generalised mass/stiffness.

### 3.4 Output table conventions

- ID column named `GRID` for nodal results and `EID` for element results.
- Domain columns `SUBCASE`, and where relevant `MODE`, `FREQ`, `TIME`.
- Column names in upper case as in Nastran; flattened fields use suffixes, e.g. `VON_MISES_Z1`,
  `VON_MISES_Z2`.

**`nastran.hdf5_masses`** — "Nastran point masses" (NAS-06b) → `table` of CONM2 (EID, GRID,
CID, MASS, X1–X3 offsets) and optionally `summary: Table` from the grid point weight generator.

### 3.5 Element-specific notes

| Element / result | Content in output table | Note |
|------------------|-------------------------|------|
| CQUAD4 forces | `MX, MY, MXY, BMX, BMY, BMXY, TX, TY` per element (centre; corner output as extra rows with `GRID` column if requested) | Element coordinate system |
| CQUAD4 stresses / strains | Normal/shear/von Mises at fibres Z1/Z2 → suffix columns `_Z1`, `_Z2` | |
| CBEAM forces / stresses | One row per element **and station** (`STATION` / end A–B column): axial, shears, moments, torque | Element coordinate system |
| CBUSH forces | `FX, FY, FZ, MX, MY, MZ` per element | Element coordinate system (as defined by the CBUSH CID/orientation). Main input for fastener load tables (spec 03 §3.2) |
| RBE2 | Forces per RBE2 element (§3.6) | Main input for fastener loads when fasteners are modelled with RBE2 |
| CONM2 | Mass data from the model section (see NAS-06b) | No result output for point masses themselves |

### 3.6 RBE2 loads (`nastran.hdf5_rbe2_loads`)

Fasteners are often modelled with RBE2 elements and their loads are read "from the RBE2".
Depending on the output request, MSC Nastran stores this in the `.h5` file in one of two
ways; the block supports both and picks whichever is present (or as chosen):

1. **MPC forces** (`MPCFORCES` request) — a *nodal* result: forces of constraint at the grids
   of the RBE2. The block reads the RBE2 connectivity (independent and dependent grids) from
   `/NASTRAN/INPUT/ELEMENT/RBE2` and groups the grid forces by RBE2 element ID.
2. **Grid point force balance** (`GPFORCE` request) — per grid, the contribution of each
   element; the rows whose element type is RBE2 give the force the RBE2 applies at that grid.

| | |
|---|---|
| Inputs | none |
| Outputs | `table: Table` — `EID` (RBE2), `GRID`, `SUBCASE`, `FX, FY, FZ, MX, MY, MZ` |
| Params | `file`, `source` (`auto` / `mpc_force` / `gpforce`), `eids` (ID filter), `side` (`independent` / `dependent` grids), `subcases`, `unit_system` |

Forces are in the **output coordinate system of the grid** (CD field), not an element system;
no transformation is done in v1. The output feeds `loads.fastener_loads` (spec 03 §3.2) like
CBUSH forces do. Which source you use is confirmed when you supply a sample file.

### 3.7 Result browser (NAS-11 … NAS-14)

The analyst builds a pipeline interactively: run the analysis, look into the `.h5` file, pick
a result, then manipulate the table. The "Nastran HDF5 results" block therefore gets its own
property panel (GUI-07) instead of the auto-generated form:

```
File            [ examples/fem/modal.h5            ] [Browse]
                MSC Nastran 2023.3 · SOL 103 · modal.bdf · 2026-11-15 14:25
Results in file [ search…                          ]
  MODAL
    ● Modal effective mass        MODAL/MODAL_EFFECTIVE_MASS       10 rows
      Modal mass fractions        MODAL/MODAL_MASS_FRACTION        10 rows
      Effective mass matrix       MODAL/EFFECTIVE_MASS_MATRIX      6 × 6
  NODAL
      Eigenvectors (mode shapes)  NODAL/EIGENVECTOR            30 080 rows
      SPC forces                  NODAL/SPC_FORCE              30 080 rows
  SUMMARY
      Eigenvalues                 SUMMARY/EIGENVALUE               10 rows
Subcases        [ 1 ▾ ]                (multi-select; empty = all)
IDs             [                 ]    present: 1 – 3008 (3008 IDs)
Columns         [ T1, T2, T3 ▾ ]       (multi-select; empty = all)
Unit system     [ pipeline default ▾ ]
```

- The list is filled as soon as a readable file is chosen. Results without a mapping are
  listed with their dataset path as name and are read raw (NAS-07).
- Clicking a result sets the `result` parameter and **peeks** (NAS-12): the first rows
  (max. 200) are read from the file with the current filters and shown in the preview
  panel, marked "Preview from file — not yet run". Running the block replaces the peek with
  the real output. A peek never changes block states or the cache.
- Subcases, IDs and columns of a previously selected result that do not exist in the newly
  selected one are cleared, with a note in the log.
- A missing or unreadable file shows the error in the panel; the parameters stay editable
  as text so a pipeline can be prepared before the analysis has run.
- The pipeline file format does not change: the panel only edits the existing parameters
  `file`, `result`, `subcases`, `ids`, `columns`, `unit_system`.

Reader additions in `h5reader` (pure library, also usable from scripts):

- `file_info(path) -> FileInfo` — version, solution number, input deck, run time.
- `describe_result(path, result) -> ResultDetail` — output column names with units,
  subcases and modes present, ID range and count. The domains of a dataset are taken from
  the `/INDEX` group when present (cheap), otherwise from the `DOMAIN_ID` column.
- `read_result(..., max_rows=None)` — row limit used by the peek.

Implementation notes:

- Code: `gui/nastran_panel.py` (panel) and `h5reader.file_info / describe_result`.
- The whole file (model data, DOMAINS, INDEX, attributes) can be looked through in the HDF5
  viewer (spec 10), opened with **Open in viewer**. The viewer shares the table step
  `h5reader.result_table` with `read_result`, so its result view equals the block output.
- While the file is readable, the result is chosen from the list only; the plain text field
  for `result` (and for subcases, IDs, columns) appears when the file cannot be read.
- Changing a filter (subcases, IDs, columns, unit system) peeks again.
- Stored subcases or columns that are not in the file are offered as "(not in file)" so they
  can be removed; `${variable}` values keep their plain text field.
- The ID filter is hidden for results without IDs (summary and modal tables).
- The unit labels in the peek follow the block's unit system, else the pipeline default —
  for the reference files (SI) set the unit system to `SI`.

### 3.8 Corrections from the real files

Found when the reader was run against the reference files; fixed in this step:

1. **Domain join.** Result tables that carry their own `MODE` column (`MODAL/*`,
   `SUMMARY/EIGENVALUE`) reference a domain without mode data. `MODE` and `EIGENVALUE` are
   therefore joined from DOMAINS only when the referenced domains are per-mode domains
   (`MODE > 0`); a `SUBCASE` column is only added when the referenced domains have a subcase
   (`SUBCASE > 0`). This removes the bogus `EIGENVALUE = 0` column from the effective mass
   tables and the `SUBCASE = 0` column from the eigenvalue summary.
2. **Matrix results** (NAS-15). `MODAL/EFFECTIVE_MASS_MATRIX` and
   `MODAL/RIGID_BODY_MASS_MATRIX` become a table with a `DOF` column (`T1 … R3`) and the
   value columns `T1 … R3` (6 rows).
3. **Grid point weight** (NAS-18). `NODAL/GRID_WEIGHT` becomes a table with one row per
   mass direction: `DIRECTION` (X, Y, Z), `MASS`, `X_CG`, `Y_CG`, `Z_CG`. The inertia and
   transformation matrices in that dataset are not output in this step.
4. **New mappings** with units: the modal effective mass results (NAS-16),
   `NODAL/APPLIED_LOAD`, `ELEMENTAL/ENERGY/STRAIN_ELEM` (element strain energy; `ID` →
   `EID`). Rotational effective mass columns `R1 … R3` are labelled as mass moment of inertia.

Confirmed correct without changes: displacements, SPC forces, eigenvectors, CBUSH forces,
CONM2 masses, eigenvalue summary values.

### 3.9 Reference files (NAS-17)

`examples/fem/` (moved from `examples/example_fem_data/`), each as `.bdf`, `.f06`, `.h5`,
MSC Nastran 2023.3, unit system `SI` (m, kg, N):

| File | Analysis | Content used in tests |
|------|----------|-----------------------|
| `modal` | SOL 103, 10 modes, `MEFFMASS` | eigenvalues, effective mass tables and matrices, eigenvectors, grid point weight |
| `global_loads` | SOL 101, 1 subcase (gravity) | CBUSH forces (17–19), displacements, SPC forces, applied loads, CONM2, grid point weight |
| `local_loads` | SOL 101, 1 subcase | CBUSH forces (2–5). All results are zero: the model has no mass, so the gravity load is zero (f06 warning 9058). Kept as the test case for the f06 warning check of the Nastran analysis block |

Test values are checked against the `.f06` (e.g. CBUSH 17: FZ = 2500 N; mode 1: 25.71 Hz).

## 4. Out of scope (v1)

Reading the rest of the input model (geometry, properties, materials) from `/NASTRAN/INPUT`
— only CONM2 and RBE2 connectivity are read —, coordinate system transformations (may become
a dedicated block later), complex results (frequency response real/imag), OP2 / F06 / PCH
files, running Nastran.

## 5. Open questions

1. ~~Sample `.h5` files from MSC Nastran 2023.3.~~ Supplied 2026-11-15 (§3.9).
2. The CQUAD4, CBEAM, MPC force and grid point force mappings are still unverified; a real
   file with shell/beam output and `MPCFORCES`/`GPFORCE` requests would close this.
