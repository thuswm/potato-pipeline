# 04 — Python Code Block

## 1. Purpose

Let the analyst write custom calculations (hand calculations, special criteria, plots) in
Python directly inside the pipeline, without creating a new block type. A Python block that
proves useful can later be promoted into a standard block (see §3.4).

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| PY-01 | The user defines the block's input and output ports (name + type) in the GUI | M |
| PY-02 | The user writes a `run()` function whose arguments are the input ports; outputs are returned in a dict | M |
| PY-03 | Code editor with Python syntax highlighting, line numbers, indentation | M |
| PY-04 | `print()` output and exceptions (with line number in the user's code) are shown in the block's log | M |
| PY-05 | **Default: the code lives in an external `.py` file** referenced by the block (path relative to the pipeline file) — version control, IDE editing, reuse | M |
| PY-05b | "New script" creates the `.py` file with a skeleton `run()` generated from the declared ports | M |
| PY-05c | The `run()` signature is checked against the declared ports before running; mismatches give a clear error | M |
| PY-06 | The script is ordinary Python and does its own imports; helper modules next to the script can be imported | M |
| PY-07 | Returned values are checked against the declared output port types | M |
| PY-08 | Option to store short code **inline** in the pipeline file instead (pre-imported `np`, `pd`, `plt`, `math`) | S |
| PY-10 | Editing the script (in the GUI or in an external editor) marks the block stale (file is in `input_files()`) | M |
| PY-09 | "Export as block" — generate a skeleton `Block` subclass from a Python block | L |

## 3. Design

### 3.1 User code contract

Block parameters: `script: Path` (e.g. `scripts/margins.py`), `inputs` / `outputs` (port name +
type), `inline_code` (only if PY-08 is used). A script file looks like this:

```python
"""Margins of safety for rib elements."""

import matplotlib.pyplot as plt
import pandas as pd


def run(loads: pd.DataFrame, allowable: float) -> dict[str, object]:
    """Compute margins of safety against the allowable."""
    ms = allowable / (1.5 * loads["VON_MISES"]) - 1.0
    result = loads.assign(MS=ms)
    fig, ax = plt.subplots()
    ax.bar(result["EID"].astype(str), result["MS"])
    return {"margins": result, "min_ms": float(ms.min()), "plot": fig}
```

The file can also be run and tested outside the tool (it is plain Python).

Block type `python.script`. "New script" is available on the command line as
`apb new-script <pipeline> <block id>` (the GUI button will call the same function); it
refuses to overwrite an existing file unless `--force` is given.

Values passed to `run`: tables as DataFrames, **scalars as plain numbers** (a scalar with
unit is logged, e.g. `allowable = 410 MPa (passed as a number)`). A script may return a
`apb.units.Quantity(value, unit)` for a scalar with unit. If the block has exactly one
output, `run` may return the value itself instead of a dict. NumPy scalars are converted to
Python numbers.

Inline code (PY-08) also defines a complete `run(...)` function.

Units: input tables keep their `attrs` (unit system, column units). Inputs with different unit
systems are refused (spec 00 §5.1). Returned tables inherit the inputs' unit system unless the
script sets `attrs` itself.

### 3.2 Execution

- The script is compiled from its source on every run (never from `__pycache__`, whose
  timestamp check has a one-second resolution), so edits are always picked up. It runs as a
  module with a unique name registered in `sys.modules` during the run (needed by e.g.
  dataclasses). Helper modules from the script's folder are importable during the run and
  are forgotten afterwards; no bytecode is written while a script runs. Inline code is
  compiled with the pseudo filename `<block b3>`.
- Errors name the user's file and line, e.g. `ZeroDivisionError: division by zero
  (margins.py, line 12)`; the full traceback is in the block's error details
  (`apb run --verbose`).
- The script and all `.py` files in its folder are the block's input files: editing any of
  them marks the block stale (PY-10).
- matplotlib is switched to its off-screen backend (`Agg`), so scripts never open windows.
- `stdout` is captured (`contextlib.redirect_stdout`) into the block log.
- Inputs are passed as copies (TAB-02 also applies here).

### 3.3 Security note

The code runs with the same rights as the user, in the same process. This is acceptable for a
local single-user tool and is identical to running a Python script. **Opening a pipeline file
from an untrusted source is equivalent to running an untrusted script.** The GUI shows a
warning when a pipeline containing Python blocks is opened for the first time.
(Sandboxing / a separate worker process is *later*.)

### 3.4 Growing the block library

A standard block is just a Python class (spec 01 §3.1) in a package registered via the
`apb.blocks` entry point. The documented path is: prototype in a Python block → move the code
into a `Block` subclass with a Pydantic `Params` model → it appears in the library for
everyone who installs the package.

## 4. Out of scope (v1)

Sandboxing, per-block virtual environments / extra pip packages, Jupyter integration.

## 5. Open questions

None (external `.py` files decided in review round 2).
