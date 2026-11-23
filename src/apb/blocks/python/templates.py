"""Templates for new Python block scripts (spec 04 §3.5, PY-12).

A template is a ready-made ``run()`` with example code. The specialised templates come
with default ports, applied only to a block that has no ports yet; for a block with
declared ports the template is written for those ports (matched by port type, in order),
so choosing a template never changes ports or connectors.

The result is ordinary Python: nothing remembers which template a script came from.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from apb.blocks.python.scripting import (
    SCRIPT_NOTE,
    PortSpec,
    ScriptError,
    port_hint,
    port_placeholder,
    script_skeleton,
    write_new_file,
)
from apb.engine.types import PortType

BLANK = "blank"


@dataclass(frozen=True)
class PortRole:
    """A port a template needs.

    Attributes:
        name: Default port name; also the placeholder (``@name@``) used in the template body.
        type: Port type.
    """

    name: str
    type: PortType


@dataclass(frozen=True)
class ScriptTemplate:
    """One template.

    Attributes:
        key: Identifier (command line ``--template``).
        title: Name shown to the user.
        description: One-line description.
        inputs: Input ports the template needs (empty for the blank template).
        outputs: Output ports the template needs.
        imports: Import lines of the script.
        summary: First line of the ``run`` docstring.
        body: Indented body of ``run`` before the ``return``; ``@role@`` is replaced by the
            name of the port playing that role.
    """

    key: str
    title: str
    description: str
    inputs: tuple[PortRole, ...] = ()
    outputs: tuple[PortRole, ...] = ()
    imports: tuple[str, ...] = ()
    summary: str = ""
    body: str = ""


TEMPLATES: dict[str, ScriptTemplate] = {
    t.key: t
    for t in (
        ScriptTemplate(
            BLANK,
            "Blank (matches the ports)",
            "Empty run() for the declared ports, with placeholders for the outputs.",
        ),
        ScriptTemplate(
            "table_calculation",
            "Table calculation",
            "Adds a margin of safety column to a table from an allowable.",
            inputs=(PortRole("table", PortType.TABLE), PortRole("allowable", PortType.SCALAR)),
            outputs=(PortRole("result", PortType.TABLE),),
            imports=("import numpy as np", "import pandas as pd"),
            summary="Add the margin of safety MS = allowable / (factor * |value|) - 1.",
            body="""\
    # The column to assess and the factor: edit these two lines.
    column = "SHEAR"
    safety_factor = 1.5

    @result@ = @table@.copy()
    load = @result@[column].abs().replace(0.0, np.nan)  # zero load: no margin (NaN)
    @result@["MS"] = @allowable@ / (safety_factor * load) - 1.0
    # Units travel in df.attrs["units"]; the margin has none.
    @result@.attrs.setdefault("units", {})["MS"] = "-"
    # The title is the default sheet name in Write Excel.
    @result@.attrs["title"] = f"Margins of safety ({column})"
    unit = @table@.attrs.get("units", {}).get(column, "")
    print(f"MS from {column} [{unit}] with allowable {@allowable@:g}, factor {safety_factor}")
""",
        ),
        ScriptTemplate(
            "table_summary",
            "Table summary",
            "Reduces a column to its largest value and says where it occurs.",
            inputs=(PortRole("table", PortType.TABLE),),
            outputs=(PortRole("max_value", PortType.SCALAR), PortRole("where", PortType.TEXT)),
            imports=("import pandas as pd",),
            summary="Find the largest value of a column and where it occurs.",
            body="""\
    # The column to reduce: edit this line.
    column = "SHEAR"

    if @table@.empty:
        raise ValueError("The table is empty")
    row = @table@[column].idxmax()
    @max_value@ = float(@table@.loc[row, column])
    keys = [c for c in ("EID", "SUBCASE", "MODE") if c in @table@.columns]
    @where@ = ", ".join(f"{c} {@table@.loc[row, c]}" for c in keys) or f"row {row}"
    print(f"Maximum {column}: {@max_value@:g} at {@where@}")
""",
        ),
        ScriptTemplate(
            "plot",
            "Plot",
            "A bar chart of one table column, ready for the report.",
            inputs=(PortRole("table", PortType.TABLE),),
            outputs=(PortRole("plot", PortType.FIGURE),),
            imports=("import matplotlib.pyplot as plt", "import pandas as pd"),
            summary="Plot one column of the table as a bar chart.",
            body="""\
    # The column to plot: edit this line.
    column = "SHEAR"

    labels = @table@["EID"].astype(str) if "EID" in @table@.columns else @table@.index.astype(str)
    unit = @table@.attrs.get("units", {}).get(column, "")
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.bar(labels, @table@[column])
    ax.set_ylabel(f"{column} [{unit}]" if unit else column)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    @plot@ = fig
""",
        ),
    )
}


def template_titles() -> dict[str, str]:
    """Return ``key -> "Title — description"`` for all templates, in display order."""
    return {k: f"{t.title} — {t.description}" for k, t in TEMPLATES.items()}


def get_template(key: str) -> ScriptTemplate:
    """Return a template by key.

    Raises:
        ScriptError: If there is no such template.
    """
    try:
        return TEMPLATES[key]
    except KeyError:
        raise ScriptError(f"Unknown template {key!r}; available: {', '.join(TEMPLATES)}") from None


def _match(
    roles: Sequence[PortRole], declared: Sequence[PortSpec], side: str, template: ScriptTemplate
) -> dict[str, str]:
    """Map each role to the first unused declared port of its type (``Any`` fits all)."""
    taken: set[str] = set()
    mapping: dict[str, str] = {}
    for role in roles:
        for port in declared:
            if port.name not in taken and port.type in (role.type, PortType.ANY):
                taken.add(port.name)
                mapping[role.name] = port.name
                break
        else:
            raise ScriptError(
                f"Template '{template.title}' needs an {side} port of type {role.type} "
                f"('{role.name}'); the block's ports do not have one. "
                "Add the port, or choose the blank template."
            )
    return mapping


def plan_ports(
    template: ScriptTemplate, inputs: Sequence[PortSpec], outputs: Sequence[PortSpec]
) -> tuple[list[PortSpec], list[PortSpec], dict[str, str]]:
    """Decide the ports of the block and which port plays which role of the template.

    Args:
        template: The template.
        inputs: Input ports the block has.
        outputs: Output ports the block has.

    Returns:
        ``(inputs, outputs, role -> port name)``. A block without any port gets the
        template's default ports; otherwise the ports are returned unchanged.

    Raises:
        ScriptError: If the declared ports have no match for a role of the template.
    """
    if not template.inputs and not template.outputs:
        return list(inputs), list(outputs), {}
    if not inputs and not outputs:
        ins = [PortSpec(name=r.name, type=r.type) for r in template.inputs]
        outs = [PortSpec(name=r.name, type=r.type) for r in template.outputs]
        return ins, outs, {r.name: r.name for r in (*template.inputs, *template.outputs)}
    mapping = _match(template.inputs, inputs, "input", template)
    mapping |= _match(template.outputs, outputs, "output", template)
    return list(inputs), list(outputs), mapping


def render_template(
    template: ScriptTemplate,
    inputs: Sequence[PortSpec],
    outputs: Sequence[PortSpec],
    mapping: dict[str, str],
    title: str,
) -> str:
    """Write the source of a script from a template.

    Args:
        template: The template.
        inputs: The block's input ports (become the arguments of ``run``).
        outputs: The block's output ports (become the keys of the returned dict).
        mapping: Role -> port name, from :func:`plan_ports`.
        title: First line of the module docstring.

    Returns:
        Python source code. Ports the template has no role for are accepted as arguments
        and returned with placeholder values, so the script always matches the ports.
    """
    if template.key == BLANK:
        return script_skeleton(inputs, outputs, title)
    imports = list(template.imports)
    if any(p.type is PortType.FIGURE for p in inputs):
        imports.append("from matplotlib.figure import Figure")
    args = ", ".join(
        f"{p.name}: {port_hint(p.type)}{' | None = None' if p.optional else ''}" for p in inputs
    )
    arg_docs = "\n".join(f"        {p.name}: {p.type} input." for p in inputs) or "        -"
    out_docs = ", ".join(f"{p.name} ({p.type})" for p in outputs) or "none"
    body = template.body
    for role, name in mapping.items():
        body = body.replace(f"@{role}@", name)
    extra = "".join(
        f"    {p.name} = {port_placeholder(p.type)}  # TODO: not used by the template\n"
        for p in outputs
        if p.name not in mapping.values()
    )
    result = ", ".join(f'"{p.name}": {p.name}' for p in outputs)
    return f'''"""{title}.

{template.description}

{SCRIPT_NOTE}
"""

from __future__ import annotations

{chr(10).join(imports)}


def run({args}) -> dict[str, object]:
    """{template.summary}

    Args:
{arg_docs}

    Returns:
        Dict with: {out_docs}.
    """
{body}{extra}    return {{{result}}}
'''


def create_script(
    path: Path,
    key: str,
    inputs: Sequence[PortSpec] = (),
    outputs: Sequence[PortSpec] = (),
    *,
    overwrite: bool = False,
) -> tuple[list[PortSpec], list[PortSpec]]:
    """Create a script file from a template; an existing file is not replaced (PY-13).

    Args:
        path: File to create (missing folders are created).
        key: Template key.
        inputs: Input ports the block has now.
        outputs: Output ports the block has now.
        overwrite: Replace an existing file (command line ``--force`` only; the GUI never
            sets it).

    Returns:
        The ports the block must have afterwards (the template's defaults for a block
        without ports, else the given ports).

    Raises:
        ScriptError: Unknown template, ports without a match, or the file exists.
    """
    template = get_template(key)
    ins, outs, mapping = plan_ports(template, inputs, outputs)
    title = path.stem.replace("_", " ").capitalize()
    write_new_file(path, render_template(template, ins, outs, mapping, title), overwrite=overwrite)
    return ins, outs
