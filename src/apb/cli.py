"""Command line interface ``apb`` (spec 01 §3.6)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

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
from apb.engine.variables import parse_assignment

app = typer.Typer(help="Analysis Pipeline Builder", no_args_is_help=True, add_completion=False)


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
) -> None:
    """Run a pipeline. Exit code 1 if a block fails."""
    model = _load(pipeline)
    names = {b.id: b.label or b.type_id for b in model.blocks}
    executor = Executor(default_registry(), sink=lambda e: _print_event(e, names, verbose))
    try:
        result = executor.run(
            model, pipeline.parent, until=until, overrides=_overrides(set_), output_dir=output_dir
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
    pipeline: Annotated[Path, typer.Argument(help="Pipeline file (*.apb.json)")],
    block_id: Annotated[str, typer.Argument(help="ID of the Python block")],
    force: Annotated[bool, typer.Option(help="Overwrite an existing script")] = False,
) -> None:
    """Create the script of a Python block with a run() matching its ports."""
    from apb.blocks.python.block import PythonBlock, PythonParams
    from apb.blocks.python.scripting import ScriptError, write_skeleton

    model = _load(pipeline)
    try:
        inst = model.block(block_id)
    except KeyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    if inst.type_id != PythonBlock.type_id:
        typer.secho(f"Block {block_id!r} is not a Python block", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    params = PythonParams.model_validate(inst.params)
    if not params.script:
        typer.secho("The block uses inline code, not a script file", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    path = Path(params.script)
    path = path if path.is_absolute() else pipeline.parent / path
    try:
        write_skeleton(path, params.inputs, params.outputs, overwrite=force)
    except ScriptError as exc:
        typer.secho(f"{exc} (use --force to overwrite)", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"Created {path}", fg=typer.colors.GREEN)


@app.command()
def gui(pipeline: Annotated[Path | None, typer.Argument(help="Pipeline to open")] = None) -> None:
    """Start the graphical editor (not implemented yet)."""
    typer.echo("The GUI is not implemented yet.")
    raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)
