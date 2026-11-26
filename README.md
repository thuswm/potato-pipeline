# Potato Pipeline

Build structural analysis pipelines graphically (Simulink style): MSC Nastran HDF5 results →
data manipulation, hand calculations, custom Python → Word report. Re-run the pipeline when
the input changes and the report is regenerated.

Status: under development (see [`CHANGELOG.md`](CHANGELOG.md)). Specification:
[`docs/spec/`](docs/spec/README.md).

## Installation

Potato Pipeline needs **Python 3.12 or later** and runs on Windows and Linux. The GUI opens
in Chrome or Edge, or in a Firefox tab.

### With pip (no uv needed)

1. Install Python 3.12 or later, e.g. from [python.org](https://www.python.org/downloads/).
   Check with `python --version`.
2. Get the code: on GitHub, *Code > Download ZIP*, and unpack it. You can also use
   `git clone https://github.com/thuswm/potato-pipeline.git`. The download includes the
   examples.
3. In the unpacked folder, create a virtual environment and install the tool into it:

   Windows (PowerShell or cmd):

   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   python -m pip install .
   ```

   If `python` is not found but the `py` launcher is installed (it comes with the
   python.org installer), use `py -m venv .venv` for the first line.

   If PowerShell refuses to run `activate`, use cmd instead, or allow local scripts once:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

   Linux:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install .
   ```

4. Run it, while the virtual environment is active:

   ```sh
   apb --version
   apb gui examples/test_case.apb.json
   ```

   In a new terminal, activate the environment again first (`.venv\Scripts\activate`). You
   can also call it without activating: `.venv\Scripts\apb gui …` (Windows) or
   `.venv/bin/apb gui …` (Linux). `python -m apb …` works as well.

If git is installed, the tool alone, without the examples, can be installed straight from
GitHub, a given release or the latest:

```sh
python -m pip install git+https://github.com/thuswm/potato-pipeline@v0.1.0
python -m pip install git+https://github.com/thuswm/potato-pipeline
```

To update, download the new version and run `python -m pip install .` again in the same
environment.

### With uv

```sh
uv sync
uv run apb gui examples/test_case.apb.json
```

## Quick start

```sh
apb --help                                  # all commands
apb gui examples/fastener_loads.apb.json    # graphical editor in its own window
apb gui examples/test_case.apb.json         # modal + CBUSH margins on real Nastran files
apb run examples/test_case.apb.json         # run a pipeline without the GUI
apb h5view examples/fem/modal.h5            # look through an HDF5 result file
```

## Blocks

| Category | Blocks |
|----------|--------|
| Nastran | MSC Nastran analysis (run or reuse results), HDF5 results, eigenvalues, f06 tables (effective mass, grid point weight) |
| Table | Read Excel/CSV, constants, select/rename, filter, sort, computed column, envelope, join, stack, load case table, pick value, convert/set units |
| Loads / checks | Fastener loads (tension/shear), requirement check |
| Python | Your own `run(...)` function in a `.py` file, with templates |
| Figures | **Graph**: line, scatter, bar, horizontal bar, step, area, histogram and box plots from a table, with series per load case, reference lines and highlighting, sized for the report |
| Output | Write Excel, write CSV, Word report |

## Configuration

The GUI opens in a Chrome or Edge app window (no tabs or address bar). It is a separate
window, not a tab in a browser you already have open: it uses its own browser profile, so
the tool can tell when the window is closed. Closing the window or *File > Quit* ends the
program. Without Chrome or Edge, or with `--window browser`, it opens a tab in the default
browser instead; the program then ends 30 s after the last tab was closed. The console
says which of the two was opened.

Settings that are the same for every pipeline on a computer — how MSC Nastran is started
and how the GUI is shown — are set in *File > Settings of this computer…*. They are stored
in `config.toml` (`~/.config/apb/config.toml`, Windows `%APPDATA%\apb\config.toml`),
which can also be edited by hand:

```toml
[nastran]
path = "C:/MSC.Software/MSC_Nastran/2024.1/bin"   # folder with the nastran program
options = ["mem=4gb"]                             # added to every run

[gui]
window = "browser"        # app (default), browser or none
browser = "C:/Program Files/Google/Chrome/Application/chrome.exe"   # optional
```

A value set in a Nastran block wins over the file.

## Adding your own blocks

A block in the library is a small Python class. The usual way to make one:

1. **Try it out in a Python block.** Write the calculation as `run(...)` in a script and
   connect it in a pipeline until the result is right.
2. **Move it into a block class** in a package of your own (`my_blocks/__init__.py`). The
   parameters become a Pydantic model, so the GUI builds the property panel from them:

   ```python
   from typing import Any

   from pydantic import BaseModel, Field

   from apb.engine.block import Block, RunContext
   from apb.engine.types import Port, PortType


   class MarginParams(BaseModel):
       column: str = Field(description="Column holding the load")
       allowable: float = Field(description="Allowable load, same unit as the column")
       safety_factor: float = Field(default=1.5, description="Safety factor")


   class Margin(Block[MarginParams]):
       """Margin of safety per row: MS = allowable / (SF * load) - 1."""

       type_id = "my_blocks.margin"  # unique, never change it once used
       category = "My blocks"  # group in the library
       title = "Margin of safety"
       description = "Adds a column MS = allowable / (SF * load) - 1."
       inputs = (Port("table", PortType.TABLE, label="Loads"),)
       outputs = (Port("table", PortType.TABLE, label="With margins"),)
       Params = MarginParams

       def run(self, params: MarginParams, inputs: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
           table = inputs["table"].copy()
           table["MS"] = params.allowable / (params.safety_factor * table[params.column]) - 1
           ctx.log(f"Lowest margin: {table['MS'].min():.3f}")  # goes to the block log
           return {"table": table}


   BLOCKS = (Margin,)
   ```

3. **Register it** with an entry point in the package's `pyproject.toml` and install the
   package into the same environment as the tool (`pip install -e .`):

   ```toml
   [project.entry-points."apb.blocks"]
   my_blocks = "my_blocks:BLOCKS"
   ```

   After a restart the block is in the library, for everyone who installs the package.

The standard blocks in `src/apb/blocks/` are built the same way and are good examples.
Saving a Python block to a personal or team library straight from the GUI is planned.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync                     # create .venv with Python 3.12 and all dependencies
uv run pytest               # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run apb --help           # command line
uv run apb gui examples/fastener_loads.apb.json   # graphical editor in its own window
uv run apb gui examples/test_case.apb.json        # modal + CBUSH margins on real Nastran files
uv run apb h5view examples/fem/modal.h5           # look through an HDF5 result file
uv run playwright install chromium                # once, for the browser tests
```
