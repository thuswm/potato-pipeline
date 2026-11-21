# 07 — Concrete Test Case

## 1. Purpose

One realistic pipeline, taken from the analyst's own work (review round 3), that is built and
checked as part of step 4 of the development plan. It drives the requirements of the report
editor (spec 05 §3.4) and the requirement check (spec 03 §3.4), and it is the first test of
the whole chain on **real MSC Nastran 2023.3 files**: HDF5 results → tables → Python margins
→ Word report and Excel. The data is fake, the analysis runs are not repeated (the result
files are supplied).

Files: pipeline `examples/test_case.apb.json`, margin script `examples/scripts/cbush_margins.py`,
joint definitions `examples/test_case/locations.csv`, Nastran files `examples/fem/`
(spec 02 §3.9), outputs in `examples/output/` (not under version control).

## 2. The three analysis chains

```
 modal.h5 ──▶ [Effective mass] ──┬─▶ [Excel: Effective mass sheet]
                                 ├─▶ [Pick lowest FREQUENCY] ─▶ [Requirement check] ─┐
                                 └───────────────────────────────────────────────────┤
 global_loads.h5 ─▶ [CBUSH forces] ─┐                                                 │
 locations.csv ─────────────────────┼─▶ [Python: margins] ──▶ Global margins ─────────┤
 safety factor ─────────────────────┤                                                 ▼
 local_loads.h5 ──▶ [CBUSH forces] ─┴─▶ [Python: margins] ──▶ Local margins ───▶ [Word report]
```

**Eigenmode calculation** (`modal`)
1. *Nastran HDF5 results*: `MODAL/MODAL_EFFECTIVE_MASS`, unit system `SI` → table `SUBCASE,
   MODE, FREQUENCY [Hz], T1–T3 [kg], R1–R3 [kg·m²]` (10 modes).
2. Branch to Excel (`table.write_excel`, sheet "Effective mass") and to the report.
3. *Pick value* (`FREQUENCY`, `min`) → 25.71 Hz → *Requirement check* (`greater_than`,
   limit from the constant `f_min` = 20 Hz of *Project constants*) → `PASS`, margin 0.286.
4. Next version: plots of the main global modes, chosen from the effective mass.

**Global loads** (`global_loads`) and **local loads** (`local_loads`), identical structure
1. *Nastran HDF5 results*: `ELEMENTAL/ELEMENT_FORCE/BUSH` → CBUSH forces `EID, SUBCASE, FX …
   MZ` (global: elements 17–19; local: 2–5).
2. *Python* block with `scripts/cbush_margins.py` (the same file for both blocks), inputs
   `forces`, `locations` (from *Read CSV*), `safety_factor` (port `SF` of *Project
   constants*); outputs `margins` (Table),
   `min_ms` (Scalar).
3. The margin tables go into the same report.
4. Next version: *load table → model update → Nastran run* in front of the analysis block
   (spec README, "Later").

**Analysis blocks (step 6, spec 09).** Each of the three result blocks gets a *Nastran
analysis* block in front of it, in run mode `never` (the analysis is not repeated: the
supplied `.bdf`/`.f06`/`.h5` are used as they are). The analysis block delivers the `.h5`
to the result block through a connector and checks the `.f06`: `modal` and `global_loads`
are clean (`OK`), `local_loads` has the warning 9058 (`WARNINGS`). The messages of the three
analyses are stacked into one table in the report ("Nastran messages") — one row: the 9058
warning of the local loads. Switching a block to `if_changed` would run the analysis again
when the `.bdf` changes (needs Nastran on the computer and the command/path set in the
block or the config file, spec 09 §3.3).

## 3. Locations

A **location** is a place where the *same set of loads* is assessed, e.g. several joints that
use the same CBUSH forces, each with its own allowables. The script therefore produces one
row per load case, element **and location**. Locations are data, not code: the CSV file

```
LOCATION,EID,AXIS,TENSION_ALLOWABLE,SHEAR_ALLOWABLE
Joint A (M6 steel),,Z,12000,9000
Joint B (M5 aluminium),,Z,5500,4000
```

| Column | Meaning |
|--------|---------|
| `LOCATION` | Name shown in the margin table |
| `EID` | Restrict the location to one element; empty = every element in the forces table |
| `AXIS` | Component of the force along the fastener axis (`X`, `Y`, `Z`; default `Z`, the CBUSH of the example models has its axis in global Z); the other two give the shear |
| `TENSION_ALLOWABLE`, `SHEAR_ALLOWABLE` | In the unit of the forces table (N) |

**All allowables, the safety factor (1.5) and the requirement (20 Hz) are made-up values** to
show the mechanics; they are in the Constants block *Project constants* (spec 03 §3.5) or in
the CSV data and are replaced by the analyst. Only the document number and revision are
pipeline variables.

## 4. The margin script (example only)

`examples/scripts/cbush_margins.py` — small, readable, and plainly marked *example, not a
validated criterion*, like `fastener_margins.py`:

- Per row of `forces` and per matching location: `TENSION` = force along `AXIS` (negative =
  compression, counted as 0), `SHEAR` = resultant of the other two force components.
- `R = TENSION / TENSION_ALLOWABLE + SHEAR / SHEAR_ALLOWABLE`;
  `MS = 1 / (safety_factor · R) − 1`; zero load (`R = 0`) gives an empty margin (NaN), not
  infinity.
- Output columns: `SUBCASE, EID, LOCATION, TENSION, SHEAR, MS`, sorted by `SUBCASE, EID,
  LOCATION`; `TENSION` and `SHEAR` keep the force unit, `MS` has none; `min_ms` is the
  smallest margin (NaN ignored), logged with its element and location.
- Errors, not silent defaults: missing columns, a location whose `EID` is not in the forces
  table, a non-positive allowable.

## 5. Expected results

Checked by the automated test (§7); computed by hand from the f06 forces.

| Check | Value |
|-------|-------|
| Analysis status (modal / global / local) | `OK` / `OK` / `WARNINGS` |
| Nastran messages table | 1 row: `local_loads.f06`, WARNING 9058, SUBDMAP SESTATIC (loads are zero) |
| First eigenfrequency | 25.7119 Hz (mode 1) |
| Requirement `> 20 Hz` | PASS, margin 0.2856 |
| Global loads: rows | 1 subcase × 3 elements × 2 locations = 6 |
| Element 17 (FZ = 2500 N), Joint A / Joint B | MS 2.200 / 0.467 |
| Element 18 (FZ = 1500 N), Joint A / Joint B | MS 4.333 / 1.444 |
| Element 19 (FZ = 500 N), Joint A / Joint B | MS 15.000 / 6.333 |
| `min_ms` global | 0.467 (element 17, Joint B) |
| Local loads: rows | 1 subcase × 4 elements × 2 locations = 8; all forces are 0, all margins empty, `min_ms` empty |

The **local loads result is all zero** because the model has no mass (spec 02 §3.9). The
pipeline reports this faithfully: empty margins and a log line "all loads are zero". The
Nastran analysis block (plan step 6) will also report the f06 warning 9058. If you want a
meaningful local case, add mass or a force to `local_loads.bdf`.

## 6. The report

Built with the section editor; only what is placed appears (REP-11):

1. **Title**, **1 Introduction** (text with `{f_min}` and `{SF}`, the table "Constants used in
   this analysis"), **units**
2. **2 Modal analysis**: the effective mass table (`MODE, FREQUENCY, T1 … R3`, formats `0.00`
   for frequency, `0.0` for masses), the requirement table, and a sentence "The first
   eigenfrequency is {f1:.2f}: requirement {status}."
3. **3 Global loads**: margin table, highlight `MS < 0`, "Minimum margin of safety:
   {min_ms_global:.2f}"
4. **4 Local loads**: same, with a sentence saying the loads are zero in this example
5. **Traceability** (pipeline, the three `.h5` files with Nastran version, script, tool version)

Item names are set explicitly (`effective_mass`, `requirement`, `global_margins`,
`local_margins`, `min_ms_global`, …) because both Python blocks deliver `margins`.

## 7. Verification

Implemented in step 4; `tools/make_test_case.py` generates the pipeline (fixed ids, so the
file is reproducible and a test compares it with the generator output).

- `tests/test_case/test_modal_and_loads.py` runs the pipeline headless (`Executor`) on the real
  files and checks the values of §5, the Excel sheet (10 modes) and the report: table rows,
  the PASS sentence, highlighted rows, traceability entries.
- A browser test opens the pipeline in the GUI, runs it, and checks states and the preview of
  the requirement table.
- A second run without changes takes everything from the cache.

## 8. Out of scope (this step)

Mode plots, load table and model update blocks, Nastran/Abaqus run blocks, a real fastener
criterion (separate project), local loads with non-zero results.

The pipeline also uses the optional `limit` input: the Constants block *Project constants*
(`f_min` = 20 Hz, `SF` = 1.5) feeds the requirement check, both margin scripts and the report
(the two values by their ports, the whole table as item `constants`). Round 4 replaced the two
*Constant* blocks and the variables `min_frequency` / `safety_factor` with it.

## 9. Open questions

1. The requirement (20 Hz), safety factor (1.5) and allowables are placeholders: send the
   numbers you want in the example, or replace them in the CSV and in *Project constants*.
2. Should the Excel workbook also get the margin tables (one sheet each)? The notes ask for
   the effective mass table only; adding them is a one-line change of the pipeline.
