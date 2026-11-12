"""Block registry: finds the available block types (ENG-12).

Built-in blocks are registered directly. Other packages add blocks through the entry point
group ``apb.blocks``; each entry point must load an iterable of :class:`Block` subclasses,
e.g. in the package's ``pyproject.toml``::

    [project.entry-points."apb.blocks"]
    fasteners = "fastener_blocks:BLOCKS"
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from importlib.metadata import entry_points
from typing import Any

from apb.engine.block import AnyBlockType, Block

ENTRY_POINT_GROUP = "apb.blocks"

_log = logging.getLogger(__name__)


class RegistryError(Exception):
    """Raised for invalid or duplicate block types."""


class BlockRegistry:
    """Mapping of ``type_id`` to block class."""

    def __init__(self, blocks: Iterable[AnyBlockType] = ()) -> None:
        """Create a registry.

        Args:
            blocks: Block classes to register immediately.
        """
        self._blocks: dict[str, AnyBlockType] = {}
        self.load_errors: list[str] = []
        for cls in blocks:
            self.register(cls)

    def register(self, cls: AnyBlockType) -> None:
        """Register a block class.

        Raises:
            RegistryError: If the class is not a complete block type or its ID is taken.
        """
        if not (isinstance(cls, type) and issubclass(cls, Block)):
            raise RegistryError(f"{cls!r} is not a Block subclass")
        for attr in ("type_id", "category", "title"):
            if not getattr(cls, attr, None):
                raise RegistryError(f"Block class {cls.__name__} lacks '{attr}'")
        existing = self._blocks.get(cls.type_id)
        if existing is not None and existing is not cls:
            raise RegistryError(
                f"Duplicate block type {cls.type_id!r}: {existing.__module__}.{existing.__name__}"
                f" and {cls.__module__}.{cls.__name__}"
            )
        self._blocks[cls.type_id] = cls

    def get(self, type_id: str) -> AnyBlockType:
        """Return the block class for ``type_id``.

        Raises:
            KeyError: If the type is unknown.
        """
        try:
            return self._blocks[type_id]
        except KeyError:
            raise KeyError(f"Unknown block type {type_id!r}") from None

    def __contains__(self, type_id: object) -> bool:
        return type_id in self._blocks

    def __iter__(self) -> Iterator[AnyBlockType]:
        return iter(sorted(self._blocks.values(), key=lambda c: (c.category, c.title)))

    def __len__(self) -> int:
        return len(self._blocks)

    def load_entry_points(self) -> None:
        """Register blocks from installed packages (entry point group ``apb.blocks``).

        A broken package does not stop the others; its error is kept in ``load_errors``.
        """
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            try:
                loaded: Any = ep.load()
                for cls in loaded:
                    self.register(cls)
            except Exception as exc:  # third-party code: report, don't crash
                msg = f"Could not load blocks from entry point {ep.name!r} ({ep.value}): {exc}"
                _log.warning(msg)
                self.load_errors.append(msg)


def default_registry() -> BlockRegistry:
    """Return a registry with all built-in blocks and installed block packages."""
    from apb.blocks import builtin_blocks

    registry = BlockRegistry(builtin_blocks())
    registry.load_entry_points()
    return registry
