# Potato Pipeline

Build structural analysis pipelines graphically (Simulink style): MSC Nastran HDF5 results →
data manipulation, hand calculations, custom Python → Word report. Re-run the pipeline when
the input changes and the report is regenerated.

Status: under development. Specification: [`docs/spec/`](docs/spec/README.md).

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

The GUI opens in a Chrome or Edge app window (no tabs or address bar); closing the window or
*File > Quit* ends the program. Without Chrome or Edge, or with `--window browser`, it opens
a tab in the default browser instead. The default can be set in the config file
(`~/.config/apb/config.toml`, Windows `%APPDATA%\apb\config.toml`):

```toml
[gui]
window = "browser"        # app (default), browser or none
browser = "C:/Program Files/Google/Chrome/Application/chrome.exe"   # optional
```
