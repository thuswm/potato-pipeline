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
- Until real files are supplied, tests use **synthetic `.h5` fixtures** generated with h5py
  in the test suite, following the layout below. When you supply sample files, they are added
  as reference tests and the mapping table is corrected where needed.

Expected layout (MSC Nastran, to be verified against sample files):

```
/NASTRAN/RESULT/DOMAINS                      # ID, SUBCASE, STEP, ANALYSIS, TIME_FREQ_EIGR, MODE, ...
/NASTRAN/RESULT/NODAL/DISPLACEMENT           # ID, X, Y, Z, RX, RY, RZ, DOMAIN_ID
/NASTRAN/RESULT/NODAL/SPC_FORCE
/NASTRAN/RESULT/ELEMENTAL/STRESS/QUAD4       # EID, fibre/stress fields ..., DOMAIN_ID
/NASTRAN/RESULT/ELEMENTAL/ELEMENT_FORCE/BAR
/NASTRAN/RESULT/SUMMARY/EIGENVALUE
/NASTRAN/INPUT/...                           # model data (not used in v1)
```

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

## 4. Out of scope (v1)

Reading the rest of the input model (geometry, properties, materials) from `/NASTRAN/INPUT`
— only CONM2 and RBE2 connectivity are read —, coordinate system transformations (may become
a dedicated block later), complex results (frequency response real/imag), OP2 / F06 / PCH
files, running Nastran.

## 5. Open questions

1. Sample `.h5` files from MSC Nastran 2023.3 (one CQUAD4/CBEAM model, one fastener model with
   CBUSH and RBE2) — to be supplied; until then synthetic fixtures are used (§3.2).
