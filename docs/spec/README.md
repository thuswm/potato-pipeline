# Specification — Analysis Pipeline Builder

Status: **DRAFT v0.4 — for review** (2026-11-15)

Change log:
- v0.4 — Review round 3 (first hands-on test, real Nastran files supplied): development plan
  below; spec 02 extended with the result browser, peek preview, corrections from the real
  files and the reference files.
- v0.3 — Review round 2: unit systems (SI m / SI mm) with m↔mm conversion and explicit units
  in reports; macro blocks marked *later*; target MSC Nastran 2023.3; RBE2 loads as a
  first-class result (fastener output); load case table side-by-side in one sheet; Python
  block uses external `.py` files by default; report contains only user-placed content;
  GUI stays browser-based (Qt considered and rejected).
- v0.2 — Review round 1: Nastran version kept open (version-tolerant reader, synthetic test
  files); Windows + Linux; test base Word document; load case tables and fastener loads
  (tension/shear) blocks; element scope CQUAD4, CBEAM, CBUSH, RBE2, CONM2.
- v0.1 — First proposal.

| # | Document | Scope |
|---|----------|-------|
| 00 | [Architecture & overview](00-architecture.md) | Goals, stack, components, data types, project layout |
| 01 | [Pipeline engine & file format](01-pipeline-engine.md) | Blocks, ports, graph, execution, caching, `.apb.json` format, CLI |
| 02 | [Nastran HDF5 input block](02-nastran-hdf5-input.md) | Reading MSC Nastran `.h5` results into tables |
| 03 | [Table blocks](03-table-blocks.md) | Filter / select / combine / envelope, load case tables, fastener loads, Excel & CSV in/out |
| 04 | [Python code block](04-python-block.md) | User-written Python inside a pipeline |
| 05 | [Word report block](05-word-report.md) | Generating `.docx` reports; later user-supplied Word templates |
| 06 | [GUI node editor](06-gui.md) | Browser GUI, canvas, property panel, running pipelines |
| 07 | [Concrete test case](07-test-case.md) | The analyst's modal + global/local loads case as example pipeline and acceptance test |
| 08 | [Stored outputs](08-stored-outputs.md) | Block outputs on disk (Parquet), reopening a pipeline as already run, clearing outputs |
| 09 | [Nastran analysis block](09-nastran-analysis.md) | Running MSC Nastran from the pipeline, run modes, f06 check, the shared solver base |

Each feature spec has the same structure: *Purpose · Requirements · Design · Out of scope (v1) · Open questions*.

Requirement IDs (e.g. `ENG-03`) are used to trace requirements to code and tests.
Priority: **M** = must have in v1, **S** = should have in v1, **L** = later version.

## Development plan (review round 3, 2026-11-15)

Agreed order. Each step: spec update → approval → code → commit.

| Step | Content | Spec | Status |
|------|---------|------|--------|
| 1 | HDF5 result browser, peek preview, reader corrections, real reference files | 02 §3.7–3.9 | done `a0ad326` |
| 2 | Small GUI fixes: New folder in Save as; block type shown on every block and in the property panel; Quit command that stops the server; one term ("block", "connector") everywhere | 06 | done `255b34e` |
| 3 | Python block: view the code in the property panel, open the file, start from a template; a template never overwrites an existing file | 04 | done `024fcbf` |
| 4 | Report section editor (with item names and previews); requirement check block; the concrete test case as example pipeline `examples/test_case.apb.json` with an example margin script | 05 §3.4, 03 §3.4, 07 | done `fd7f443` |
| 5 | Block outputs stored on disk (Parquet via `pyarrow`) so a reopened pipeline is recognised as already run; "Clear outputs" | 01 §3.4, 08 | done `e2edf44` |
| 6 | Nastran analysis block: points at the `.bdf`, run mode never / if input changed / always, outputs `.h5` and `.f06`, reports fatal errors and warnings from the f06; analysis blocks (mode *never*) added to the test case | 09, 07 | coded, waiting for commit |

Later (noted, not scheduled): load table → model update block; mode shape plots; Abaqus
blocks; Word template as block input; start/exit as an application that hides the server;
data organisation for collaboration; blocks separated from the pipeline program in an
extendable toolbox folder with a block definition standard (after the test case, which will
show what the standard needs). The fastener loads block stays as it is until it moves into
a fastener toolbox.

**The concrete test case** (one pipeline, three chains, one report; analysis runs
deactivated; detailed in [07](07-test-case.md)):

- *Eigenmodes* (`modal`): effective mass table → Excel and report; lowest eigenfrequency
  checked against a "must be higher than" requirement.
- *Global loads* and *local loads*: CBUSH forces → Python margin script → margin table per
  load case, element and **location** → same report. A location is a place where the same
  set of loads is assessed, e.g. several joints using the same CBUSH loads, each with its
  own allowables; the script therefore outputs one row per load case, element and location.
