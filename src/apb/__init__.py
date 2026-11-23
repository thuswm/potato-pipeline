"""Potato Pipeline: graphical pipelines from Nastran results to Word reports."""

from importlib.metadata import PackageNotFoundError, version

DISTRIBUTION = "analysis-pipeline-builder"
"""Name of the installed distribution; its metadata holds the version (spec 12 §3.2)."""

try:
    __version__: str = version(DISTRIBUTION)
except PackageNotFoundError:  # pragma: no cover - only when src/ is used without install
    __version__ = "0+unknown"


def show(value: object, name: str = "") -> object:
    """Show a value in the Python block's preview: see :func:`apb.debug.show`."""
    from apb.debug import show as _show

    return _show(value, name)
