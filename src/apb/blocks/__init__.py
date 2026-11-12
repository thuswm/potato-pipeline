"""Standard block library."""

from __future__ import annotations

from typing import Any

from apb.engine.block import Block


def builtin_blocks() -> list[type[Block[Any]]]:
    """Return all block types shipped with the tool."""
    from apb.blocks.loads.fastener import BLOCKS as FASTENER
    from apb.blocks.nastran.blocks import BLOCKS as NASTRAN
    from apb.blocks.table.excel import BLOCKS as EXCEL
    from apb.blocks.table.loadcases import BLOCKS as LOADCASES
    from apb.blocks.table.readers import BLOCKS as READERS
    from apb.blocks.table.transform import BLOCKS as TRANSFORM
    from apb.blocks.table.values import BLOCKS as VALUES

    return [*NASTRAN, *READERS, *VALUES, *TRANSFORM, *LOADCASES, *FASTENER, *EXCEL]
