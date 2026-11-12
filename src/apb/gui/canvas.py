"""NiceGUI element wrapping the Drawflow node canvas (spec 06 §3.2).

Only this module and ``canvas.js`` know about Drawflow; replacing the canvas library later
only touches these two files.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nicegui import ui
from nicegui.element import Element
from nicegui.events import GenericEventArguments

STATIC = Path(__file__).parent / "static"


def add_canvas_css() -> None:
    """Add the Drawflow and canvas style sheets to the current page."""
    ui.add_css(STATIC / "drawflow" / "drawflow.min.css")
    ui.add_css(Path(__file__).parent / "canvas.css")


type Payload = dict[str, Any]


class NodeCanvas(Element, component="canvas.js", dependencies=["static/drawflow/drawflow.min.js"]):
    """The pipeline canvas. Events carry dicts (see ``canvas.js``)."""

    def __init__(self, graph: Payload | None = None) -> None:
        """Create the canvas element.

        Args:
            graph: Initial content (see ``EditorState.graph_spec``), drawn on page load.
        """
        super().__init__()
        self._props["graph"] = graph or {"nodes": [], "connections": []}

    def on_event(self, name: str, handler: Callable[[Payload], Any]) -> None:
        """Register a handler for a canvas event (``block_moved``, ``connection_created`` ...).

        Args:
            name: Event name.
            handler: Receives the event payload.
        """

        def call(e: GenericEventArguments) -> Any:
            return handler(e.args if isinstance(e.args, dict) else {})

        self.on(name, call)

    def load_graph(self, graph: Payload) -> None:
        """Replace the whole canvas content."""
        self.run_method("load_graph", graph)

    def add_node(self, spec: Payload) -> None:
        """Add a block."""
        self.run_method("add_node", spec)

    def remove_node(self, block_id: str) -> None:
        """Remove a block (and its connections) from the view."""
        self.run_method("remove_node", block_id)

    def update_node(self, spec: Payload) -> None:
        """Redraw a block's label (same ports)."""
        self.run_method("update_node", spec)

    def replace_node(self, spec: Payload, connections: list[dict[str, str]]) -> None:
        """Rebuild a block whose ports changed, keeping its position, and reconnect it."""
        self.run_method("replace_node", spec, connections)

    def connect(self, conn: Payload) -> None:
        """Draw a connection."""
        self.run_method("connect", conn)

    def disconnect(self, conn: Payload) -> None:
        """Remove a connection from the view."""
        self.run_method("disconnect", conn)

    def set_states(self, states: dict[str, str]) -> None:
        """Colour blocks by run state (block id -> state name)."""
        self.run_method("set_states", states)

    def select_node(self, block_id: str | None) -> None:
        """Highlight a block (e.g. when an error in the log is clicked)."""
        self.run_method("select_node", block_id)

    async def view_center(self) -> tuple[float, float]:
        """Return the canvas coordinates of the middle of the visible area."""
        result = await self.run_method("view_center")
        if isinstance(result, dict):
            return float(result.get("x", 100.0)), float(result.get("y", 100.0))
        return 100.0, 100.0

    def fit_view(self) -> None:
        """Zoom and pan so that the whole pipeline is visible."""
        self.run_method("fit_view")

    def reset_view(self) -> None:
        """Reset zoom and panning."""
        self.run_method("reset_view")

    def zoom(self, direction: int) -> None:
        """Zoom in (``+1``) or out (``-1``)."""
        self.run_method("zoom", direction)
