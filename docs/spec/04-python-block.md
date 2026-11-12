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

Units: input tables keep their `attrs` (unit system, column units). Inputs with different unit
systems are refused (spec 00 §5.1). Returned tables inherit the inputs' unit system unless the
script sets `attrs` itself.

### 3.2 Execution

- The script is loaded fresh on every run (`importlib.util.spec_from_file_location` under a
  unique module name), so edits are always picked up; tracebacks show the real file name and
  line numbers. Helper modules from the script's folder are importable during the run and are
  reloaded each run. Inline code is compiled with the pseudo filename `<block b3>`.
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
