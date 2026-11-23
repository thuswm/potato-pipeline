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

## Configuration

The GUI opens in a Chrome or Edge app window (no tabs or address bar). It is a separate
window, not a tab in a browser you already have open: it uses its own browser profile, so
the tool can tell when the window is closed. Closing the window or *File > Quit* ends the
program. Without Chrome or Edge, or with `--window browser`, it opens a tab in the default
browser instead; the program then ends 30 s after the last tab was closed. The console
says which of the two was opened. The default can be set in the config file
(`~/.config/apb/config.toml`, Windows `%APPDATA%\apb\config.toml`):

```toml
[gui]
window = "browser"        # app (default), browser or none
browser = "C:/Program Files/Google/Chrome/Application/chrome.exe"   # optional
```

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
