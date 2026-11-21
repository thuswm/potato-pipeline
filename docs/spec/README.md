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

The current development plan is in [`PLAN.md`](../../PLAN.md); completed plans are in [`docs/plans/`](../plans/).
