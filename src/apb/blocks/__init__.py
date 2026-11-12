"""Standard block library."""

from __future__ import annotations

from typing import Any

from apb.engine.block import Block


def builtin_blocks() -> list[type[Block[Any]]]:
    """Return all block types shipped with the tool."""
    from apb.blocks.nastran.blocks import BLOCKS as NASTRAN
    from apb.blocks.table.excel import BLOCKS as EXCEL

    return [*NASTRAN, *EXCEL]
