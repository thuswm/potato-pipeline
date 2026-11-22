"""Command line interface ``apb`` (spec 01 §3.6)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from pydantic import ValidationError

from apb import __version__
from apb.engine import (
    BlockState,
    EventKind,
    Executor,
    PipelineModel,
    PipelineValidationError,
    RunEvent,
    default_registry,
    load_pipeline,
    prepare,
)
from apb.engine.store import OutputStore, StoreError, format_size
from apb.engine.variables import parse_assignment

app = typer.Typer(help="Potato Pipeline", no_args_is_help=True, add_completion=False)


def _print_version(value: bool) -> None:
    """Print the version and exit when ``--version`` is given (spec 12 VER-03)."""
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _main(
    show_version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Print the version and exit.",
        ),
    ] = False,
) -> None:
    """Potato Pipeline."""


def _load(path: Path) -> PipelineModel:
    """Load a pipeline file or exit with a readable error."""
    if not path.is_file():
        typer.secho(f"File not found: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    try:
        return load_pipeline(path)
    except ValidationError as exc:
        typer.secho(f"{path} is not a valid pipeline file:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc


def _overrides(assignments: list[str] | None) -> dict[str, Any]:
    """Parse ``--set name=value`` options."""
    try:
        return dict(parse_assignment(a) for a in assignments or [])
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--set") from exc


def _print_event(event: RunEvent, names: dict[str, str], verbose: bool = False) -> None:
    """Print a run event to the terminal (tracebacks only if ``verbose``)."""
    name = names.get(event.block_id or "", event.block_id or "")
    match event.kind:
        case EventKind.BLOCK_STARTED:
            typer.echo(f"▶ {name}")
        case EventKind.BLOCK_FINISHED | EventKind.BLOCK_CACHED:
            typer.secho(f"✔ {name}: {event.message}", fg=typer.colors.GREEN)
        case EventKind.BLOCK_FAILED:
            typer.secho(f"✘ {name}: {event.message}", fg=typer.colors.RED, err=True)
            if verbose:
                typer.echo(event.detail, err=True)
        case EventKind.BLOCK_SKIPPED:
            typer.secho(f"- {name}: skipped ({event.message})", fg=typer.colors.YELLOW)
        case EventKind.LOG if event.block_id is None:
            typer.secho(f"! {event.message}", fg=typer.colors.YELLOW)
        case EventKind.LOG:
            typer.echo(f"  {name}: {event.message}")
        case _:
            pass


@app.command()
def run(
    pipeline: Annotated[Path, typer.Argument(help="Pipeline file (*.apb.json)")],
    set_: Annotated[
        list[str] | None,
        typer.Option("--set", help="Override a pipeline variable, e.g. --set h5_file=new.h5"),
    ] = None,
    until: Annotated[str | None, typer.Option(help="Run only up to this block ID")] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(help="Directory for written files (default: next to the pipeline)"),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show Python tracebacks of errors")
    ] = False,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Recompute every block (stored results are ignored)")
    ] = False,
) -> None:
    """Run a pipeline. Exit code 1 if a block fails.

    Results of earlier runs are stored next to the pipeline (folder .apb) and reused for
    blocks whose inputs did not change. Runs with --set do not use or change the store.
    """
    model = _load(pipeline)
    names = {b.id: b.label or b.type_id for b in model.blocks}
    overrides = _overrides(set_)
    store = None if overrides else OutputStore.for_pipeline(pipeline)
    executor = Executor(
        default_registry(),
        sink=lambda e: _print_event(e, names, verbose),
        store=store,
        use_stored=not no_cache,
    )
    for block_id, reason in executor.index_problems().items():
        typer.secho(
            f"! Stored output of {names.get(block_id, block_id)} ignored: {reason}",
            fg=typer.colors.YELLOW,
        )
    try:
        result = executor.run(
            model,
            pipeline.parent,
            until=until,
            overrides=overrides,
            output_dir=output_dir,
            pipeline_file=pipeline.resolve(),
        )
    except PipelineValidationError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    except KeyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    failed = [b for b, r in result.results.items() if r.state is not BlockState.DONE]
    if failed:
        typer.secho(f"{len(failed)} block(s) not completed", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho("Pipeline completed", fg=typer.colors.GREEN)


@app.command()
def outputs(
    pipeline: Annotated[Path, typer.Argument(help="Pipeline file (*.apb.json)")],
) -> None:
    """List the stored outputs of a pipeline: block, state, time, size."""
    model = _load(pipeline)
    store = OutputStore.for_pipeline(pipeline)
    executor = Executor(default_registry(), store=store)
    states = executor.states(model, pipeline.parent)
    entries, problems = store.entries()
    typer.echo(f"Store: {store.folder} ({format_size(store.size())})")
    for block in model.blocks:
        entry = entries.get(block.id)
        state = states.get(block.id, BlockState.IDLE)
        when = f"{entry.finished}  {format_size(entry.size)}" if entry else ""
        label = block.label or block.type_id
        typer.echo(f"{block.id:<16} {state.value:<6} {when}  {label}".rstrip())
    for block_id, reason in problems.items():
        typer.secho(f"! {block_id}: unreadable ({reason})", fg=typer.colors.YELLOW)
    known = {b.id for b in model.blocks}
    orphans = store.orphans(known)
    if orphans:
        typer.echo(f"Entries of blocks that are not in the pipeline: {', '.join(orphans)}")


@app.command("clear-outputs")
def clear_outputs(
    pipeline: Annotated[Path, typer.Argument(help="Pipeline file (*.apb.json)")],
    block: Annotated[
        list[str] | None,
        typer.Option("--block", help="Clear this block and everything downstream (repeatable)"),
    ] = None,
) -> None:
    """Delete the stored outputs of a pipeline (all, or some blocks and what is downstream)."""
    model = _load(pipeline)
    unknown = [b for b in block or [] if b not in {x.id for x in model.blocks}]
    if unknown:
        typer.secho(f"No such block: {', '.join(unknown)}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    executor = Executor(default_registry(), store=OutputStore.for_pipeline(pipeline))
    try:
        cleared = executor.clear_outputs(model, block or None)
    except StoreError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Cleared {len(cleared)} block(s): {', '.join(cleared) or 'nothing was stored'}")


@app.command()
def validate(
    pipeline: Annotated[Path, typer.Argument(help="Pipeline file (*.apb.json)")],
    set_: Annotated[list[str] | None, typer.Option("--set", help="Override a variable")] = None,
) -> None:
    """Check a pipeline without running it."""
    graph = prepare(_load(pipeline), default_registry(), _overrides(set_))
    if graph.issues:
        for issue in graph.issues:
            typer.secho(f"- {issue}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho(
        f"OK: {len(graph.blocks)} blocks, {len(graph.connections)} connections",
        fg=typer.colors.GREEN,
    )


@app.command()
def blocks() -> None:
    """List the available block types."""
    registry = default_registry()
    category = None
    for cls in registry:
        if cls.category != category:
            category = cls.category
            typer.secho(category, bold=True)
        typer.echo(f"  {cls.type_id:<28} {cls.title}")
    for err in registry.load_errors:
        typer.secho(err, fg=typer.colors.YELLOW, err=True)


@app.command("new-script")
def new_script(
    pipeline: Annotated[Path | None, typer.Argument(help="Pipeline file (*.apb.json)")] = None,
    block_id: Annotated[str | None, typer.Argument(help="ID of the Python block")] = None,
    force: Annotated[bool, typer.Option(help="Overwrite an existing script")] = False,
    template: Annotated[
        str, typer.Option(help="Template to start from (see --list-templates)")
    ] = "blank",
    list_templates: Annotated[bool, typer.Option(help="List the templates and exit")] = False,
) -> None:
    """Create the script of a Python block with a run() matching its ports.

    With --template the script starts from a template. A block without ports gets the
    ports of the template (the pipeline file is updated); otherwise the ports are kept.
    """
    from apb.blocks.python.block import PythonBlock
    from apb.blocks.python.scripting import PortSpec, ScriptError, ports_to_params
    from apb.blocks.python.templates import create_script, template_titles
    from apb.engine.model import save_pipeline

    if list_templates:
        for key, text in template_titles().items():
            typer.echo(f"{key}: {text}")
        return
    if pipeline is None or block_id is None:
        typer.secho("Give the pipeline file and the block id", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    model = _load(pipeline)
    try:
        inst = model.block(block_id)
    except KeyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    if inst.type_id != PythonBlock.type_id:
        typer.secho(f"Block {block_id!r} is not a Python block", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    script = str(inst.params.get("script", ""))
    if not script:
        typer.secho("The block uses inline code, not a script file", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    path = Path(script)
    path = path if path.is_absolute() else pipeline.parent / path
    ins = [PortSpec.model_validate(p) for p in inst.params.get("inputs", [])]
    outs = [PortSpec.model_validate(p) for p in inst.params.get("outputs", [])]
    try:
        new_ins, new_outs = create_script(path, template, ins, outs, overwrite=force)
    except ScriptError as exc:
        hint = " (use --force to overwrite)" if "already exists" in str(exc) else ""
        typer.secho(f"{exc}{hint}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    if (new_ins, new_outs) != (ins, outs):
        inst.params["inputs"] = ports_to_params(new_ins)
        inst.params["outputs"] = ports_to_params(new_outs)
        save_pipeline(model, pipeline)
        typer.echo(f"Added the ports of the template to block {block_id!r} in {pipeline}")
    typer.secho(f"Created {path}", fg=typer.colors.GREEN)


if TYPE_CHECKING:
    from apb.gui.launcher import WindowSettings

_WINDOW_HELP = (
    "How to show the GUI: app (Chrome/Edge app window, default), browser (a tab in the "
    "default browser) or none (start only the server). Default from [gui] window in the "
    "config file"
)


def _window_settings(window: str | None, no_browser: bool) -> WindowSettings:
    """Return the window settings of ``--window`` / ``--no-browser`` and the config file."""
    from apb.config import ConfigError
    from apb.gui.launcher import WindowMode, window_settings

    try:
        return window_settings(WindowMode.NONE if no_browser else window)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc


def _server_answers(port: int) -> bool:
    """Return ``True`` if an APB GUI server already answers on the local port."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/h5", timeout=2) as response:
            return response.status == 200 and b"HDF5 viewer" in response.read()
    except OSError:
        return False


def _show(paths: list[str], pipeline: Path | None, port: int, settings: WindowSettings) -> None:
    """Open the pages in a running server, or start the server and open them."""
    from apb.gui.launcher import open_windows_only

    if _server_answers(port):
        typer.echo(f"Using the GUI server already running on port {port}")
        open_windows_only(settings, [f"http://127.0.0.1:{port}{p}" for p in paths])
        return
    from apb.gui.app import run_gui

    run_gui(pipeline, port=port, window=settings, open_paths=paths)


@app.command()
def gui(
    pipeline: Annotated[Path | None, typer.Argument(help="Pipeline to open")] = None,
    port: Annotated[int, typer.Option(help="Local port of the GUI server")] = 8765,
    window: Annotated[str | None, typer.Option(help=_WINDOW_HELP)] = None,
    no_browser: Annotated[bool, typer.Option(help="Same as --window none")] = False,
) -> None:
    """Start the graphical editor in its own window (stop with File > Quit or Ctrl+C)."""
    if pipeline is not None and not pipeline.is_file():
        typer.secho(f"File not found: {pipeline}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    settings = _window_settings(window, no_browser)
    if pipeline is not None and _server_answers(port):
        typer.secho(
            f"A GUI server is already running on port {port}; open the pipeline there with "
            "File > Open, or use --port for a second one.",
            fg=typer.colors.YELLOW,
        )
    _show(["/"], pipeline, port, settings)


@app.command()
def h5view(
    files: Annotated[list[Path], typer.Argument(help="HDF5 file(s) to look into")],
    port: Annotated[int, typer.Option(help="Local port of the GUI server")] = 8765,
    units: Annotated[
        str, typer.Option(help="Unit system for the unit labels: SI, SI_mm or empty")
    ] = "",
    window: Annotated[str | None, typer.Option(help=_WINDOW_HELP)] = None,
    no_browser: Annotated[bool, typer.Option(help="Same as --window none")] = False,
) -> None:
    """Open the HDF5 viewer for one or more .h5 files, one window each (spec 10)."""
    from apb.gui.h5viewer import UNIT_CHOICES, viewer_url

    missing = [f for f in files if not f.is_file()]
    if missing:
        for f in missing:
            typer.secho(f"File not found: {f}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    if units not in UNIT_CHOICES:
        typer.secho(
            f"Unknown unit system {units!r} (use SI or SI_mm)", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(2)
    settings = _window_settings(window, no_browser)
    urls = [viewer_url(f, units=units) for f in files]
    for url in urls:
        typer.echo(f"http://127.0.0.1:{port}{url}")
    _show(urls, None, port, settings)


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
