# Analysis Pipeline Builder

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
uv run apb gui examples/fastener_loads.apb.json   # graphical editor in the browser
uv run apb gui examples/test_case.apb.json        # modal + CBUSH margins on real Nastran files
uv run playwright install chromium                # once, for the browser tests
```
