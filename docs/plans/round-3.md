# Plan — review round 3 (2026-11-15), completed

Agreed order. Each step: spec update → approval → code → commit.

| Step | Content | Spec | Status |
|------|---------|------|--------|
| 1 | HDF5 result browser, peek preview, reader corrections, real reference files | 02 §3.7–3.9 | done `a0ad326` |
| 2 | Small GUI fixes: New folder in Save as; block type shown on every block and in the property panel; Quit command that stops the server; one term ("block", "connector") everywhere | 06 | done `255b34e` |
| 3 | Python block: view the code in the property panel, open the file, start from a template; a template never overwrites an existing file | 04 | done `024fcbf` |
| 4 | Report section editor (with item names and previews); requirement check block; the concrete test case as example pipeline `examples/test_case.apb.json` with an example margin script | 05 §3.4, 03 §3.4, 07 | done `fd7f443` |
| 5 | Block outputs stored on disk (Parquet via `pyarrow`) so a reopened pipeline is recognised as already run; "Clear outputs" | 01 §3.4, 08 | done `e2edf44` |
| 6 | Nastran analysis block: points at the `.bdf`, run mode never / if input changed / always, outputs `.h5` and `.f06`, reports fatal errors and warnings from the f06; analysis blocks (mode *never*) added to the test case | 09, 07 | done `c448b9e` |

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
