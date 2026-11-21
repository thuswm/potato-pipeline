"""Browse any HDF5 file: tree, details, paged raw and result views (spec 10).

Pure library next to :mod:`apb.blocks.nastran.h5reader`; no GUI code. Every function opens
the file read-only and closes it before returning, so a file is never locked (H5V-12).

Typical use::

    from apb.blocks.nastran import h5browse

    for node in h5browse.list_tree("model.h5"):
        print(node.path, node.rows)
    raw = h5browse.read_rows("model.h5", "/NASTRAN/INPUT/NODE/GRID", start=0, count=100)
    sel = h5browse.select_rows("model.h5", "NODAL/DISPLACEMENT", subcases=[2], ids="1-50")
    page = h5browse.read_result_page("model.h5", sel, start=0, count=100)
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import h5py
import numpy as np
import numpy.typing as npt
import pandas as pd

from apb.blocks.nastran.h5reader import (
    ANALYSIS_MODES,
    INDEX_ROOT,
    RESULT_ROOT,
    NastranH5Error,
    ResultDetail,
    _attr_text,
    _id_field,
    _id_mask,
    _open,
    describe_result,
    normalise_result_path,
    parse_id_filter,
    read_domains,
    result_table,
)
from apb.blocks.nastran.mapping import RESULT_MAPPINGS
from apb.units import UnitSystem, get_units, set_units, unit_label

#: Rows read at a time when a filter or an export walks through a dataset (H5V spec §3.4).
CHUNK_ROWS = 100_000

#: Above this many rows the GUI asks before a CSV export (H5V-11).
LARGE_EXPORT_ROWS = 1_000_000

type RowIndex = npt.NDArray[np.int64]


@dataclass(frozen=True)
class H5Node:
    """One group or dataset in the file tree (H5V-03).

    Attributes:
        path: Absolute path, e.g. ``/NASTRAN/RESULT/NODAL/DISPLACEMENT``.
        name: Last part of the path.
        kind: ``"group"`` or ``"dataset"``.
        shape: Dataset shape (``()`` for groups and scalars).
        rows: Number of rows (first dimension; 1 for scalars, 0 for groups).
        columns: Number of fields of a compound dataset, else columns of the flattened view.
        attributes: Number of attributes.
        children: Number of members of a group (0 for datasets).
    """

    path: str
    name: str
    kind: Literal["group", "dataset"]
    shape: tuple[int, ...] = ()
    rows: int = 0
    columns: int = 0
    attributes: int = 0
    children: int = 0

    @property
    def parent(self) -> str:
        """Return the path of the parent group (``"/"`` for top-level items)."""
        head = self.path.rsplit("/", 1)[0]
        return head or "/"

    @property
    def size_label(self) -> str:
        """Return a short size for display, e.g. ``"3008 x 7"`` or ``"4 items"``."""
        if self.kind == "group":
            return f"{self.children} item" + ("" if self.children == 1 else "s")
        return f"{self.rows} x {self.columns}" if self.columns else f"{self.rows}"


@dataclass(frozen=True)
class H5Column:
    """One column of a dataset (H5V-04).

    Attributes:
        name: Field name (``PARENT.CHILD`` for nested compound fields).
        dtype: HDF5/NumPy type, e.g. ``"float64"``, ``"bytes8"``.
        shape: Shape of an array field (``()`` for plain values).
        kind: Quantity kind from the result mapping (``""`` if unknown).
        unit: Unit label in the requested unit system (``""`` if unknown).
    """

    name: str
    dtype: str
    shape: tuple[int, ...] = ()
    kind: str = ""
    unit: str = ""


@dataclass(frozen=True)
class H5Details:
    """Details of a group or dataset (H5V-04).

    Attributes:
        node: The tree entry.
        attributes: Attribute name -> value as text.
        columns: Columns of a dataset (empty for groups).
        result: Path relative to ``/NASTRAN/RESULT`` for result datasets, else ``""``.
        title: Display name from the mapping (``""`` if none).
    """

    node: H5Node
    attributes: dict[str, str]
    columns: tuple[H5Column, ...] = ()
    result: str = ""
    title: str = ""


@dataclass(frozen=True)
class SubcaseInfo:
    """Summary of one subcase from the DOMAINS table (H5V-09).

    Attributes:
        subcase: Subcase ID.
        domains: Number of domains (e.g. one per mode).
        modes: Number of modes (0 for non-modal subcases).
    """

    subcase: int
    domains: int
    modes: int

    @property
    def label(self) -> str:
        """Return e.g. ``"1 (10 modes)"`` or ``"2"``."""
        return f"{self.subcase} ({self.modes} modes)" if self.modes else str(self.subcase)


@dataclass(frozen=True)
class RowSelection:
    """Rows of a dataset that match the filters of a view (computed once per filter change).

    Attributes:
        file: The ``.h5`` file.
        dataset: Absolute dataset path.
        result: Path relative to ``/NASTRAN/RESULT`` (``""`` for the raw view).
        rows: Matching row numbers in increasing order, or ``None`` for all rows.
        total: Number of matching rows.
        domain_scope: Domain IDs of the matching rows (decides the domain columns).
    """

    file: Path
    dataset: str
    result: str
    rows: RowIndex | None
    total: int
    domain_scope: tuple[int, ...] | None = None


# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------


def absolute_path(dataset: str) -> str:
    """Return an absolute HDF5 path (leading ``/``, no trailing ``/``)."""
    return "/" + dataset.strip().strip("/")


def result_path(dataset: str) -> str:
    """Return the path relative to ``/NASTRAN/RESULT`` of a result dataset, else ``""``.

    ``DOMAINS`` and the ``/INDEX`` group are not results.
    """
    full = absolute_path(dataset)
    prefix = RESULT_ROOT + "/"
    if not full.startswith(prefix):
        return ""
    rel = full[len(prefix) :]
    return "" if rel == "DOMAINS" else rel


def copy_path(dataset: str) -> str:
    """Return the path to copy for a dataset (H5V-10).

    Result datasets give the short form the HDF5 block uses (``NODAL/DISPLACEMENT``), other
    datasets their absolute path.
    """
    return result_path(dataset) or absolute_path(dataset)


# --------------------------------------------------------------------------------------
# Tree and details
# --------------------------------------------------------------------------------------


def _column_count(ds: h5py.Dataset) -> int:
    """Return the number of columns of the flattened raw view of a dataset."""
    if ds.dtype.names:
        return len(_flat_names(ds.dtype))
    if len(ds.shape) >= 2:
        return int(np.prod(ds.shape[1:]))
    return 1


def _node(name: str, obj: Any) -> H5Node:
    """Build a tree entry for an h5py object at path ``name`` (relative to the root)."""
    path = absolute_path(name)
    short = path.rsplit("/", 1)[-1]
    if isinstance(obj, h5py.Group):
        return H5Node(path, short, "group", attributes=len(obj.attrs), children=len(obj))
    shape = tuple(int(n) for n in obj.shape) if obj.shape is not None else ()
    return H5Node(
        path,
        short,
        "dataset",
        shape=shape,
        rows=shape[0] if shape else 1,
        columns=_column_count(obj),
        attributes=len(obj.attrs),
    )


def list_tree(path: str | Path) -> list[H5Node]:
    """List every group and dataset of a file, sorted by path (H5V-03).

    No data is read, only the structure.

    Args:
        path: ``.h5`` file.

    Returns:
        One entry per group/dataset; the root group itself is not listed.

    Raises:
        NastranH5Error: If the file is missing or not an HDF5 file.
    """
    nodes: list[H5Node] = []
    with _open(path) as h5:
        h5.visititems(lambda name, obj: nodes.append(_node(name, obj)))
    return sorted(nodes, key=lambda n: n.path)


def _flat_names(dtype: np.dtype[Any], prefix: str = "") -> list[str]:
    """Return the flattened column names of a (possibly nested) compound type (H5V-06)."""
    names: list[str] = []
    for field in dtype.names or ():
        sub = dtype[field]
        label = f"{prefix}{field}"
        base, shape = (sub.base, sub.shape) if sub.shape else (sub, ())
        inner = _flat_names(base, label + ".") if base.names else [label]
        if shape:
            names += [
                f"{n}{_index_label(i, shape)}" for i in range(int(np.prod(shape))) for n in inner
            ]
        else:
            names += inner
    return names


def _index_label(flat: int, shape: tuple[int, ...]) -> str:
    """Return ``[i]`` or ``[i,j]`` for a flat position in an array field."""
    return "[" + ",".join(str(i) for i in np.unravel_index(flat, shape)) + "]"


def _type_label(dtype: np.dtype[Any]) -> str:
    """Return a readable type name, e.g. ``float64`` or ``bytes8``."""
    if dtype.kind == "S":
        return f"bytes{dtype.itemsize}"
    if dtype.kind == "U":
        return f"str{dtype.itemsize // 4}"
    if dtype.kind == "O":
        return "string"
    return str(dtype)


def _columns(
    dtype: np.dtype[Any], result: str, unit_system: UnitSystem | None, prefix: str = ""
) -> list[H5Column]:
    """Describe the fields of a compound type, with quantity kinds of known results."""
    mapping = RESULT_MAPPINGS.get(result) if result else None
    out: list[H5Column] = []
    for field in dtype.names or ():
        sub = dtype[field]
        base = sub.base if sub.shape else sub
        if base.names:
            out += _columns(base, "", unit_system, f"{prefix}{field}.")
            continue
        kind_name, unit = "", ""
        if mapping is not None and not prefix:
            out_name = mapping.id_column if field == mapping.id_field else field
            out_name = mapping.rename.get(field, out_name)
            kind = mapping.kinds.get(out_name)
            if kind is not None:
                kind_name = kind.value
                unit = unit_label(kind, unit_system) if unit_system is not None else ""
        out.append(
            H5Column(
                f"{prefix}{field}",
                _type_label(base),
                tuple(int(n) for n in sub.shape),
                kind_name,
                unit,
            )
        )
    return out


def node_details(path: str | Path, node: str, unit_system: UnitSystem | None = None) -> H5Details:
    """Return attributes and columns of a group or dataset (H5V-04).

    Args:
        path: ``.h5`` file.
        node: Absolute path of the group or dataset (``"/"`` for the root).
        unit_system: Unit system for the unit labels of known result columns.

    Returns:
        The details; only attributes and the type are read, no data.

    Raises:
        NastranH5Error: If the item does not exist.
    """
    full = absolute_path(node)
    with _open(path) as h5:
        if full != "/" and full not in h5:
            raise NastranH5Error(f"{full} not found in {path}")
        obj = h5[full]
        attributes = {str(k): _attr_text(v) for k, v in obj.attrs.items()}
        if full == "/":
            entry = H5Node("/", "/", "group", attributes=len(obj.attrs), children=len(obj))
        else:
            entry = _node(full, obj)
        result = result_path(full) if entry.kind == "dataset" else ""
        columns: list[H5Column] = []
        if isinstance(obj, h5py.Dataset):
            if obj.dtype.names:
                columns = _columns(obj.dtype, result, unit_system)
            else:
                columns = [H5Column("value", _type_label(obj.dtype), tuple(obj.shape[1:]))]
    mapping = RESULT_MAPPINGS.get(result) if result else None
    return H5Details(entry, attributes, tuple(columns), result, mapping.title if mapping else "")


# --------------------------------------------------------------------------------------
# Raw view
# --------------------------------------------------------------------------------------


def _decode(values: npt.NDArray[Any]) -> npt.NDArray[Any]:
    """Decode byte strings to stripped text; other arrays are returned unchanged."""
    if values.dtype.kind == "S":
        return np.char.strip(np.char.decode(values, "ascii", errors="replace"))
    if values.dtype.kind == "O":
        return np.array(
            [v.decode(errors="replace") if isinstance(v, bytes) else v for v in values],
            dtype=object,
        )
    return values


def _flatten(data: npt.NDArray[Any], prefix: str = "") -> dict[str, npt.NDArray[Any]]:
    """Flatten structured rows into columns: ``NAME[i]`` and ``PARENT.CHILD`` (H5V-06)."""
    columns: dict[str, npt.NDArray[Any]] = {}
    n = len(data)
    for field in data.dtype.names or ():
        values = data[field]
        label = f"{prefix}{field}"
        shape = data.dtype[field].shape
        if shape:
            flat = values.reshape(n, -1, *values.shape[1 + len(shape) :])
            for i in range(flat.shape[1]):
                part = flat[:, i]
                tag = f"{label}{_index_label(i, shape)}"
                if part.dtype.names:
                    columns.update(_flatten(part, tag + "."))
                else:
                    columns[tag] = _decode(part)
        elif values.dtype.names:
            columns.update(_flatten(values, label + "."))
        else:
            columns[label] = _decode(values)
    return columns


def raw_frame(data: npt.NDArray[Any]) -> pd.DataFrame:
    """Turn rows read from any dataset into a flat table (H5V-05, H5V-06).

    Args:
        data: Rows as read with h5py: structured, plain 1-D/N-D or 0-d (scalar).

    Returns:
        Compound datasets: one column per (flattened) field. Plain 1-D data: column
        ``value``. N-D data: columns ``[0]``, ``[1]``, … (``[i,j]`` beyond 2-D).
    """
    arr = np.asarray(data)
    if arr.ndim == 0:
        arr = arr.reshape(1)
    if arr.dtype.names:
        return pd.DataFrame(_flatten(arr))
    if arr.ndim == 1:
        return pd.DataFrame({"value": _decode(arr)})
    inner = arr.shape[1:]
    flat = arr.reshape(arr.shape[0], -1)
    return pd.DataFrame({_index_label(i, inner): _decode(flat[:, i]) for i in range(flat.shape[1])})


def _dataset(h5: h5py.File, dataset: str) -> h5py.Dataset:
    """Return a dataset of an open file or raise a readable error."""
    full = absolute_path(dataset)
    obj = h5.get(full)
    if not isinstance(obj, h5py.Dataset):
        raise NastranH5Error(f"{full} is not a dataset in {h5.filename}")
    return obj


def _read_selected(ds: h5py.Dataset, rows: RowIndex) -> npt.NDArray[Any]:
    """Read given (increasing) row numbers: one block read when they are close together."""
    if rows.size == 0:
        return np.asarray(ds[0:0])
    first, last = int(rows[0]), int(rows[-1])
    if last - first + 1 <= 4 * rows.size + 1000:
        block = np.asarray(ds[first : last + 1])
        return block[rows - first]
    return np.asarray(ds[rows])


def read_rows(path: str | Path, dataset: str, start: int = 0, count: int = 100) -> pd.DataFrame:
    """Read rows ``start … start+count-1`` of any dataset, flattened (H5V-05).

    Only those rows are read from the file.

    Args:
        path: ``.h5`` file.
        dataset: Absolute dataset path.
        start: First row (0-based).
        count: Number of rows.

    Returns:
        The rows as a flat table; the index holds the row numbers in the dataset.

    Raises:
        NastranH5Error: If the dataset does not exist.
    """
    with _open(path) as h5:
        ds = _dataset(h5, dataset)
        if not ds.shape:
            return raw_frame(ds[()])
        stop = min(start + count, int(ds.shape[0]))
        df = raw_frame(ds[start:stop])
    df.index = pd.RangeIndex(start, start + len(df))
    return df


# --------------------------------------------------------------------------------------
# Result view
# --------------------------------------------------------------------------------------


def subcase_summary(path: str | Path) -> list[SubcaseInfo]:
    """Return the subcases in a file with their number of domains and modes (H5V-09).

    Args:
        path: ``.h5`` file.

    Returns:
        One entry per subcase > 0; empty if the file has no DOMAINS table.
    """
    try:
        domains = read_domains(path)
    except NastranH5Error:
        return []
    if "SUBCASE" not in domains:
        return []
    out: list[SubcaseInfo] = []
    for subcase, group in domains[domains["SUBCASE"] > 0].groupby("SUBCASE"):
        modes = 0
        if {"ANALYSIS", "MODE"} <= set(group.columns):
            modal = group[(group["ANALYSIS"] == ANALYSIS_MODES) & (group["MODE"] > 0)]
            modes = int(modal["MODE"].nunique())
        out.append(SubcaseInfo(int(str(subcase)), len(group), modes))
    return out


def mode_choices(path: str | Path, result: str, detail: ResultDetail | None = None) -> list[int]:
    """Return the modes a result dataset can be filtered on (H5V-07).

    Per-mode results (mode shapes) have them through their domains; modal summary tables
    (effective mass, eigenvalues) have their own ``MODE`` field.

    Args:
        path: ``.h5`` file.
        result: Path relative to ``/NASTRAN/RESULT``.
        detail: :func:`h5reader.describe_result` of the dataset, if already at hand.

    Returns:
        Sorted mode numbers (empty for non-modal results).
    """
    detail = detail or describe_result(path, result)
    if detail.modes:
        return list(detail.modes)
    with _open(path) as h5:
        ds = _dataset(h5, f"{RESULT_ROOT}/{result}")
        if "MODE" not in (ds.dtype.names or ()):
            return []
        found: set[int] = set()
        for begin in range(0, int(ds.shape[0]), CHUNK_ROWS):
            found.update(int(m) for m in np.unique(ds.fields("MODE")[begin : begin + CHUNK_ROWS]))
    return sorted(m for m in found if m > 0)


def _domain_rows(h5: h5py.File, ds: h5py.Dataset, rel: str, domain_ids: RowIndex) -> RowIndex:
    """Return the rows of a result dataset that belong to the given domains.

    Uses ``/INDEX`` (position and length per domain) when present, so no data is read;
    otherwise the ``DOMAIN_ID`` field is read in chunks.
    """
    index = h5.get(f"{INDEX_ROOT}/{rel}")
    if isinstance(index, h5py.Dataset) and {"DOMAIN_ID", "POSITION", "LENGTH"} <= set(
        index.dtype.names or ()
    ):
        table = index[()]
        keep = table[np.isin(table["DOMAIN_ID"], domain_ids)]
        parts = [
            np.arange(int(p), int(p) + int(n), dtype=np.int64)
            for p, n in zip(keep["POSITION"], keep["LENGTH"], strict=True)
        ]
        rows = np.concatenate(parts) if parts else np.empty(0, dtype=np.int64)
        return np.unique(rows)
    parts = []
    for begin in range(0, int(ds.shape[0]), CHUNK_ROWS):
        chunk = ds.fields("DOMAIN_ID")[begin : begin + CHUNK_ROWS]
        parts.append(begin + np.flatnonzero(np.isin(chunk, domain_ids)))
    return np.concatenate(parts).astype(np.int64) if parts else np.empty(0, dtype=np.int64)


def _field_filter(ds: h5py.Dataset, field: str, rows: RowIndex | None, keep: Any) -> RowIndex:
    """Keep the rows (all if ``rows`` is None) whose ``field`` value passes ``keep``.

    The field is read in chunks of :data:`CHUNK_ROWS` rows, so memory stays bounded.
    """
    total = int(ds.shape[0])
    parts: list[RowIndex] = []
    if rows is None:
        for begin in range(0, total, CHUNK_ROWS):
            values = ds.fields(field)[begin : begin + CHUNK_ROWS]
            parts.append(begin + np.flatnonzero(keep(values)))
    else:
        for begin in range(0, rows.size, CHUNK_ROWS):
            chunk = rows[begin : begin + CHUNK_ROWS]
            values = _read_selected(ds, chunk)[field]
            parts.append(chunk[keep(values)])
    return np.concatenate(parts).astype(np.int64) if parts else np.empty(0, dtype=np.int64)


def _dataset_domains(h5: h5py.File, ds: h5py.Dataset, rel: str) -> RowIndex:
    """Return the domain IDs a result dataset has rows for (``/INDEX`` or ``DOMAIN_ID``)."""
    index = h5.get(f"{INDEX_ROOT}/{rel}")
    if isinstance(index, h5py.Dataset) and "DOMAIN_ID" in (index.dtype.names or ()):
        return np.unique(index.fields("DOMAIN_ID")[()]).astype(np.int64)
    if "DOMAIN_ID" not in (ds.dtype.names or ()):
        return np.empty(0, dtype=np.int64)
    found: set[int] = set()
    for begin in range(0, int(ds.shape[0]), CHUNK_ROWS):
        found.update(int(d) for d in np.unique(ds.fields("DOMAIN_ID")[begin : begin + CHUNK_ROWS]))
    return np.array(sorted(found), dtype=np.int64)


def select_rows(
    path: str | Path,
    dataset: str,
    *,
    subcases: Iterable[int] | None = None,
    modes: Iterable[int] | None = None,
    ids: str | Sequence[tuple[int, int]] = "",
) -> RowSelection:
    """Find the rows of a dataset that match the filters (H5V-07).

    For result datasets the subcase and mode filters go through the domains (using the
    ``/INDEX`` group when present); tables with their own ``MODE`` field (modal summaries)
    are filtered on that field. The ID filter reads only the ID field, chunk by chunk.
    Other datasets are not filtered (raw view: all rows).

    Args:
        path: ``.h5`` file.
        dataset: Absolute dataset path (leading ``/``), or a result path relative to
            ``/NASTRAN/RESULT`` such as ``NODAL/DISPLACEMENT``.
        subcases: Subcases to keep (``None`` or empty = all).
        modes: Modes to keep (``None`` or empty = all).
        ids: ID filter such as ``"1001-1200, 3005"`` or parsed ranges.

    Returns:
        The selection, to be passed to :func:`read_result_page` or :func:`export_csv`.

    Raises:
        NastranH5Error: If the dataset does not exist, the ID filter is invalid or the
            dataset has no ID field to filter on.
    """
    if dataset.strip().startswith("/"):
        full = absolute_path(dataset)
    else:
        full = absolute_path(f"{RESULT_ROOT}/{normalise_result_path(dataset)}")
    rel = result_path(full)
    ranges = parse_id_filter(ids) if isinstance(ids, str) else list(ids)
    wanted_sc = sorted(set(subcases or []))
    wanted_md = sorted(set(modes or []))
    file = Path(path)

    with _open(file) as h5:
        ds = _dataset(h5, full)
        total = int(ds.shape[0]) if ds.shape else 1
        if not rel:
            return RowSelection(file, full, "", None, total)
        names = ds.dtype.names or ()
        mapping = RESULT_MAPPINGS.get(rel)
        scope = _dataset_domains(h5, ds, rel)
        domains = read_domains(file) if scope.size else pd.DataFrame()
        rows: RowIndex | None = None

        own_mode = "MODE" in names
        dom_filter = wanted_sc or (wanted_md and not own_mode)
        if dom_filter and scope.size:
            used = domains[domains["ID"].isin(scope)]
            if wanted_sc:
                used = used[used["SUBCASE"].isin(wanted_sc)]
            if wanted_md and not own_mode:
                used = used[used["MODE"].isin(wanted_md)]
            scope = used["ID"].to_numpy(dtype=np.int64)
            rows = _domain_rows(h5, ds, rel, scope)
        if wanted_md and own_mode:
            rows = _field_filter(ds, "MODE", rows, lambda v: np.isin(v, wanted_md))
        if ranges:
            id_field = _id_field(names, mapping)
            if id_field is None or id_field not in names:
                raise NastranH5Error(f"Result {rel!r} has no ID field to filter on")
            rows = _field_filter(ds, id_field, rows, lambda v: _id_mask(v, ranges))

    count = total if rows is None else int(rows.size)
    return RowSelection(
        file, full, rel, rows, count, tuple(int(d) for d in scope) if scope.size else None
    )


def _page_rows(selection: RowSelection, start: int, count: int) -> RowIndex:
    """Return the dataset row numbers of one page of a selection."""
    stop = min(start + count, selection.total)
    if start >= stop:
        return np.empty(0, dtype=np.int64)
    if selection.rows is None:
        return np.arange(start, stop, dtype=np.int64)
    return selection.rows[start:stop]


def read_page(
    selection: RowSelection,
    start: int = 0,
    count: int = 100,
    *,
    result_view: bool = True,
    unit_system: UnitSystem | None = None,
) -> pd.DataFrame:
    """Read one page of a selection, as raw rows or as the result table (H5V-05, H5V-07).

    Args:
        selection: From :func:`select_rows`.
        start: First matching row (0-based) of the page.
        count: Number of matching dataset rows on the page.
        result_view: Result datasets: build the table as the HDF5 block delivers it
            (domain joined, mapped names). ``False`` (or a non-result dataset): raw rows.
        unit_system: Unit system for the unit labels of the result view.

    Returns:
        The page. A result row may expand to several table rows (beam stations, 6 x 6
        matrices); the raw view keeps the dataset row numbers as index.
    """
    rows = _page_rows(selection, start, count)
    domains = read_domains(selection.file) if selection.result and result_view else None
    with _open(selection.file) as h5:
        ds = _dataset(h5, selection.dataset)
        if not ds.shape:
            return raw_frame(ds[()])
        data = _read_selected(ds, rows)
    if domains is None or not selection.result:
        df = raw_frame(data)
        df.index = pd.Index(rows, name="row")
        return df
    df = result_table(data, selection.result, domains, selection.domain_scope)
    mapping = RESULT_MAPPINGS.get(selection.result)
    units: dict[str, str] = {}
    if mapping and unit_system is not None:
        units = {col: unit_label(kind, unit_system) for col, kind in mapping.kinds.items()}
    set_units(df, unit_system, units)
    return df


def read_result_page(
    selection: RowSelection,
    start: int = 0,
    count: int = 100,
    unit_system: UnitSystem | None = None,
) -> pd.DataFrame:
    """Read one page of a result selection as the HDF5 block would deliver it (H5V-07).

    Shorthand for :func:`read_page` with ``result_view=True``.
    """
    return read_page(selection, start, count, result_view=True, unit_system=unit_system)


def iter_pages(
    selection: RowSelection,
    *,
    result_view: bool = True,
    unit_system: UnitSystem | None = None,
    chunk: int = CHUNK_ROWS,
) -> Iterator[pd.DataFrame]:
    """Yield the whole selection in chunks (for the CSV export)."""
    for start in range(0, max(selection.total, 1), chunk):
        page = read_page(selection, start, chunk, result_view=result_view, unit_system=unit_system)
        if len(page) or start == 0:
            yield page


def export_csv(
    selection: RowSelection,
    target: str | Path,
    *,
    result_view: bool = True,
    unit_system: UnitSystem | None = None,
) -> int:
    """Write a whole selection (all pages, with filters) to a CSV file (H5V-11).

    The header has one row; in the result view units are added as ``T1 [m]``. Values are
    written at full precision.

    Args:
        selection: From :func:`select_rows`.
        target: CSV file to write (overwritten).
        result_view: Export the result table (``True``) or the raw rows.
        unit_system: Unit system for the unit labels.

    Returns:
        Number of table rows written.
    """
    written = 0
    columns: list[str] | None = None
    with Path(target).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for page in iter_pages(selection, result_view=result_view, unit_system=unit_system):
            if columns is None:
                columns = [str(c) for c in page.columns]
                units = get_units(page)
                writer.writerow([f"{c} [{units[c]}]" if units.get(c) else c for c in columns])
            page = page.reindex(columns=columns)
            page.to_csv(fh, header=False, index=False, lineterminator="\n")
            written += len(page)
    return written
