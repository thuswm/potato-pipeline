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
| PY-11 | **Code view:** the property panel shows the script of a Python block (read-only, syntax highlighted) with its file name, folder and modification time, and buttons to open it in the user's own editor or to edit it in the GUI (§3.5) | M |
| PY-12 | **Templates:** a new Python block can start from a template (a ready-made `run()` with example code and, for the specialised templates, default ports) as an alternative to choosing an existing file (§3.5) | M |
| PY-13 | **A script that exists is never overwritten** by a template or skeleton, in the GUI or on the command line without `--force`; the GUI has no overwrite option at all (§3.5) | M |
| PY-14 | The panel shows whether `run()` matches the declared ports and reports syntax errors, **without executing the script** (static check) (§3.5) | M |
| PY-16 | **Ports and code in step (round 6):** adding, renaming or removing an input port rewrites the `def run(...)` line of the script (file or inline code), and only that line (§3.9) | M |
| PY-17 | `run()` arguments without an input port are offered as new ports ("Add input port(s): …", type from the annotation); ports missing from `run()` can be added to it with one click (§3.9) | M |
| PY-18 | After a run, returned keys that are not output ports and output ports that were not returned are reported by name, with what to do (§3.9) | M |
| PY-19 | **When a script fails** the block log says where (the lines of the user's code in the traceback) and lists the variables at the failing line, tables as size, columns and first rows; the preview shows that log open, right under the error (§3.10) | M |
| PY-20 | `from apb import show`; `show(value, name)` adds the value as a tab *show: name* to the block's preview, also when the script fails later (§3.10) | M |
| PY-21 | **Fixes in both directions (round 6 re-test):** a port without a `run()` argument offers *Remove input port(s)* as well as *Add … to run()*; the ports stay the definition of the block (§3.9.1) | M |
| PY-22 | **Output ports from the code:** when every `return` of `run()` is a dict literal with fixed keys (or a name assigned one, §3.9.1), the keys are compared with the output ports without running the script, and *Add output port(s)* / *Remove output port(s)* are offered (§3.9.1) | M |
| PY-15 | Templates of the user (a folder of `.py` files offered next to the built-in ones) | L |

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
everyone who installs the package. Round 6 (re-test question "how do I add a block to the
library?"): the steps and a complete example are in the README, *Adding your own blocks*
(the example is checked to run). Saving a Python block to a user/team library from the
GUI is a round 7 proposal (PLAN, *Moved out*).

### 3.5 Review round 3: code view and templates (PY-11 … PY-15)

**Start of a Python block.** A newly added Python block has no script yet. Its panel shows a
"Script" section with three ways to start:

1. **Use existing file…** — file dialog (as today). The ports are *not* touched: declare
   them to match the script, or use *Check* (below) to see what differs.
2. **New from template…** — choose a template, then a file name in a dialog that offers
   "New folder" (spec 06 GUI-17). Creates the file, stores the path in the block and shows
   it in the code view. If the chosen name already exists it is **refused** with the
   message "*name* already exists — use *Use existing file…* to link it", not replaced.
3. **Inline code** — the existing option for short snippets (PY-08), unchanged. Round 6
   re-test: the panel calls it **Code in the pipeline file** (under *Advanced*), with the
   help "Instead of a .py file: short code defining run(...), stored inside the .apb.json.
   Use a script file for anything longer (editor, version control)".

**Built-in templates** (`apb.blocks.python.templates`; each is one `.py` text with
placeholders for the port names, so the result is ordinary Python):

| Template | Default ports | Content |
|----------|---------------|---------|
| Blank (matches ports) | none; uses the declared ports | what `apb new-script` creates today (PY-05b) |
| Table calculation | in: `table` (Table), `allowable` (Scalar); out: `result` (Table) | adds a margin column per row from a column and an allowable; shows units handling (`df.attrs["units"]`) |
| Table summary | in: `table`; out: `max_value` (Scalar), `where` (Text) | reduces a column to a worst value and where it occurs |
| Plot | in: `table`; out: `plot` (Figure) | matplotlib figure from a table column |

Default ports are applied **only when the block has no ports declared yet**; otherwise the
template body is generated for the ports that are declared (as the skeleton is today), so
declared ports and connectors are never changed by choosing a template. After a template was
applied the code is just a file: later changes of the ports do not rewrite it.

**Code view (PY-11).** Once the block has a script file the "Script" section shows:

```
Script   scripts/fastener_margins.py          [ Choose another… ]
         /home/…/examples/scripts · changed 2026-11-15 14:20
         run() matches the ports ✔                      [ Check ]
         +----------------------------------------------+
         | """Fastener margins.                         |  read-only,
         | …                                            |  highlighted,
         | def run(loads: pd.DataFrame, …               |  scrollable
         +----------------------------------------------+
         [ Open in editor ]  [ Edit here ]
```

- *Open in editor* opens the file with the program the system has registered for `.py`
  files (same mechanism as the *Open* buttons for Excel and Word results).
- *Edit here* is the existing editor dialog (save writes the file; the block turns stale
  because the script is an input file, PY-10).
- The view reads the file every time the panel is built and after *Check* or *Edit here*, so
  changes made in an external editor show up after *Check* (no file watching in v1).
- A missing script shows "File not found" and the start choices again; a script that cannot
  be read (permissions, encoding) shows the error instead of the code.
- The view shows the whole file in a scroll area of fixed height.

**Static check (PY-14).** *Check* (and building the panel) parses the script with `ast`;
nothing is imported or executed, so opening a pipeline from someone else and selecting a
block is safe (§3.3). It reports: a syntax error with its line number; no `run` function;
argument names of `run()` that differ from the declared input ports (missing / unknown).
Whether the returned dict has the declared output keys is *not* checked (too fragile): that
stays a run-time error (PY-07).

**Never overwritten (PY-13).**
- Files are created in exclusive mode (`open(path, "x")`), so an existing file — also one
  that appeared after the dialog was shown — makes the creation fail with a message.
- The GUI has no "replace" or "reset to template" action. To start over, the user removes
  the file in the file system or links another file.
- `apb new-script` keeps its refusal; `--force` remains for the command line only. It gets
  the option `--template NAME`, and `apb new-script --list-templates` prints the names.

**Implementation notes.**
- Code: `blocks/python/templates.py` (templates, port matching, `create_script`),
  `blocks/python/scripting.py` (`check_script_source`, `write_new_file`, `read_script`),
  `gui/python_panel.py` (panel). Templates are Python data in `templates.py` with `@role@`
  placeholders in the body, not separate files.
- A specialised template written for *declared* ports matches each role to the first unused
  port of the same type (a port of type `Any` fits every role). If a role has no match the
  template is **refused** with a message naming the missing port ("add the port, or choose
  the blank template"), nothing is created. Declared ports without a role are still
  arguments of `run()` / keys of the returned dict, so the script always matches the ports.
- `apb new-script --template` on a block without ports adds the template's ports and saves
  the pipeline file; with declared ports the pipeline file is not touched.
- The "Use existing file…" and "Choose another…" actions only change the `script` path.

**Not in v1:** templates supplied by the user from a folder (PY-15) — it depends on how
toolboxes and shared data are organised (spec README, "Later") and is decided together with
the toolbox restructure. A script does not record which template it came from.

### 3.9 Ports and code in step (PY-16 … PY-18) — round 6

Found in the Windows test: a `run()` argument written in the code did not become a port,
and ports defined in the panel did not appear in the code. Agreed approach (2026-11-23):
**the ports stay the definition of the block**, the code follows on request or
automatically for the `def` line only.

- **Port change → `def run(...)` line** (`blocks/python/signature.py`,
  `rewrite_run_signature`). Arguments are matched to ports by position when the number of
  ports is unchanged, so a renamed port renames its argument; kept arguments keep their
  type hints and defaults; new ports get the hint of their type (`pd.DataFrame`, `float`,
  `str`, `Figure`, `object`; optional ports `| None = None`); arguments of removed ports
  are dropped; other arguments, `*args`, `**kwargs` and the return annotation are kept;
  arguments without default come first (Python requires it). A long line is written with
  one argument per line. The body is **not** changed: after a rename the panel says
  "Only the run() line is changed: rename the name in the code too". A script with a
  syntax error is not touched ("run() not updated: …"). This is the one change the panel
  makes to an existing script (PY-13 still holds for templates and skeletons).
- **Arguments → ports** (`ports_from_signature`): when the check finds `run()` arguments
  without default and without port, a button *Add input port(s): a, b* adds them (type
  from the annotation: `DataFrame` → Table, `float`/`int` → Scalar, `str` → Text,
  `Figure` → Figure, else Any). When ports are missing from `run()`, a button *Add the
  ports to run()* rewrites the `def` line.
- **Outputs** cannot be read reliably from the code, so they are checked after the run:
  "run() returned 'x', which is not an output port: add an output port 'x' (Ports) or leave
  it out of the returned dict" and "the output port 'y' is missing from the dict run()
  returned".

### 3.9.1 Re-test findings: fixes both ways, outputs from `return` (PY-21, PY-22)

Found in the Windows re-test (2026-11-24): (1) adding a key to the returned dict did not
give an output port; (2) a port was removed in the panel, which rewrote `run()` in the
script file at once, but the pipeline was not saved; after reopening, the block had the
old port again and the only fix offered was to put the argument back into `run()`. The
question "what is the master?" was answered: **the ports** (decided 2026-11-23,
confirmed 2026-11-24), but the panel must offer the fix in both directions:

| Mismatch found by the check | Fixes offered |
|---|---|
| `run()` argument without default and without port | *Add input port(s): a* (as before) |
| Input port without `run()` argument (no `**kwargs`) | *Remove input port(s): b* · *Add b to run()* |
| Key returned by `run()` that is not an output port | *Add output port(s): x* (type Any) |
| Output port not among the returned keys | *Remove output port(s): y* |

- **Returned keys** (`scripting.returned_keys`; the panel uses `signature.output_differences`
  and `signature.ports_missing_from_run`): the `return` statements of `run()` itself
  (not of nested functions) are read with `ast`. Only when every one returns a dict literal
  whose keys are all plain strings (no `**other`) are the keys known: the union of them,
  in order. Anything else (a variable, `dict(...)`, a comprehension) → unknown: no output
  check before the run, the after-run report (PY-18) stays.
- The static check (`check_script_source`) reports the output mismatches with the input
  ones, so the panel shows one list of problems.
- Removing a port also removes its connectors (as any port change, spec 06 §3.2.2).
- **Port edits that rewrite a script file** say so: "run() in calc.py updated and saved:
  … Save the pipeline to keep the ports." The file cannot wait for the pipeline save, since
  it may be open in an external editor. With inline code the change is part of the pipeline
  and is saved with it.

**Second re-test (2026-11-26): "able to update the block from the code from the input side,
not the output".** Two gaps:

- **`return result`** is common (`result = {"table": out}` … `return result`). The keys are
  also read when a `return` returns a plain name that `run()` itself assigns exactly once,
  as a statement of its own body, to a dict literal with plain string keys; keys added by
  `result["x"] = ...` (plain string key, anywhere in `run()` outside nested functions) are
  included. Any other assignment to the name (`result = other`, `result.update(...)`,
  `result[key] = ...` with a variable key, augmented assignment) → unknown, as before.
- **Unknown keys are said:** when the keys cannot be read, the check line adds the grey
  note "Output ports are checked after the run: run() does not return a {...} literal."
  (also when the input side matches), so the user knows why no output fix is offered.
- **Code in the pipeline file** (inline code) gets the same check line and fix buttons as
  a script file (shown above the code field); before, it got none.
- **Bug found meanwhile:** the code field (CodeMirror) stored its text on `blur`, which
  CodeMirror never emits, so typed inline code was lost. It is stored on `focusout` now
  (same for the JSON fields of the panel).

### 3.10 Debugging a script (PY-19, PY-20) — round 6

Found in the Windows test: "I need some way to get printouts of tables and variables in
case there is an error." `print()` already went to the block log; now:

- **On an error** the block log gets (`scripting.error_details`):
  ```
  Where it failed (your code):
    line 38 in run(): ratio = allowable_shear / (safety_factor - 1.5)
  Variables in run() at line 38:
    loads = DataFrame 24 rows x 4 columns: EID, SUBCASE, TENSION, SHEAR
            EID  SUBCASE  TENSION  SHEAR
        0  1001        1      0.0  1350.0
        …
    safety_factor = 1.5
  ```
  Only frames in the user's file (or inline code) are listed; the variables are those of
  the innermost such frame (modules, functions, classes and `_names` left out, at most 25).
  Tables: size, columns and the first 5 rows; other values: `repr`, cut at 200 characters
  (`apb.debug.summarize`).
- **The preview of a failed block** shows the error, then the block log **open**, then
  the shown values.
- **`show(value, name="")`** (`apb/debug.py`, importable as `from apb import show`): the
  value is copied at that moment and shown as an extra preview tab *show: name* (default
  names `value1`, `value2` …), also when the script fails later; the log gets one line
  `show: name = <summary>`. It returns the value, so `df = show(df.merge(...), "merged")`
  works. Outside the tool (script run on its own) it prints the summary. The values travel
  as `RunContext.shown` → `BlockResult.shown`; they are kept for the last run only (not
  cached, not stored).

## 4. Out of scope (v1)

Sandboxing, per-block virtual environments / extra pip packages, Jupyter integration.

## 5. Open questions

1. ~~Python block: external `.py` files?~~ Decided in review round 2.
2. Are the four built-in templates the right starting set? A template is one text file in
   `apb/blocks/python/templates/`, so more are easy to add. A candidate is a *Requirement
   check* (value against a limit, pass/fail) once the check block of plan step 4 exists.
