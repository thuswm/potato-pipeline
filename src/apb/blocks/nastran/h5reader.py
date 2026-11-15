"""Reader for MSC Nastran HDF5 result files (spec 02). Pure library, no block or GUI code.

Typical use::

    from apb.blocks.nastran import h5reader

    print(h5reader.file_info("model.h5").label)
    for info in h5reader.list_results("model.h5"):
        print(info.path, info.size)
    print(h5reader.describe_result("model.h5", "ELEMENTAL/STRESS/QUAD4").subcases)
    df = h5reader.read_result("model.h5", "ELEMENTAL/STRESS/QUAD4", subcases=[1, 2],
                              ids="101-120, 135")
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import h5py
import numpy as np
import numpy.typing as npt
import pandas as pd

from apb.blocks.nastran.mapping import RESULT_MAPPINGS, ResultMapping
from apb.units import QuantityKind, UnitSystem, set_units, unit_label

RESULT_ROOT = "/NASTRAN/RESULT"
INDEX_ROOT = "/INDEX/NASTRAN/RESULT"
INPUT_ROOT = "/NASTRAN/INPUT"
DOMAINS_PATH = f"{RESULT_ROOT}/DOMAINS"

#: Analysis codes in the DOMAINS table.
ANALYSIS_MODES = 2

type IdArray = npt.NDArray[np.int64]


class NastranH5Error(Exception):
    """Raised for missing files, datasets or invalid requests."""


@dataclass(frozen=True)
class ResultInfo:
    """Description of one result dataset in a file.

    Attributes:
        path: Dataset path relative to ``/NASTRAN/RESULT``, e.g. ``ELEMENTAL/STRESS/QUAD4``.
        title: Display name (from the mapping table, else the path).
        rows: Number of rows (all subcases).
        fields: Field names of the dataset.
        known: ``True`` if the dataset has a mapping (otherwise it is read raw).
        size: Size for display, e.g. ``"120 rows"`` or ``"6 x 6"``.
    """

    path: str
    title: str
    rows: int
    fields: tuple[str, ...]
    known: bool
    size: str = ""


@dataclass(frozen=True)
class FileInfo:
    """Information about the analysis that wrote a result file (NAS-14).

    Attributes:
        version: Version string as stored, e.g. ``"msc20233"`` (``""`` if not stored).
        solution: Solution number, e.g. ``101``, or ``None``.
        input_deck: Name of the input deck, e.g. ``"modal.bdf"``.
        run_time: Date and time of the run as stored in the file.
        synthetic: ``True`` for synthetic test files (not real Nastran output).
    """

    version: str = ""
    solution: int | None = None
    input_deck: str = ""
    run_time: str = ""
    synthetic: bool = False

    @property
    def version_label(self) -> str:
        """Return a readable version, e.g. ``"MSC Nastran 2023.3"`` for ``"msc20233"``."""
        m = re.fullmatch(r"msc(\d{4})(\d)", self.version.strip().lower())
        return f"MSC Nastran {m.group(1)}.{m.group(2)}" if m else self.version

    @property
    def label(self) -> str:
        """Return a one-line description: version, solution, input deck, run time."""
        parts = [
            self.version_label,
            f"SOL {self.solution}" if self.solution is not None else "",
            self.input_deck,
            self.run_time,
            "synthetic test file" if self.synthetic else "",
        ]
        return " · ".join(p for p in parts if p)


@dataclass(frozen=True)
class ResultDetail:
    """What one result dataset offers for selection (NAS-13).

    Attributes:
        path: Dataset path relative to ``/NASTRAN/RESULT``.
        title: Display name.
        rows: Number of rows in the dataset (all subcases).
        columns: Value columns of the output table (without ID and domain columns).
        units: Column name -> unit label (empty without unit system).
        subcases: Subcases the dataset has rows for.
        modes: Modes the dataset has rows for (via its domains).
        id_column: Name of the ID column in the output table (``""`` if it has none).
        id_min: Smallest ID present (``None`` without ID column or rows).
        id_max: Largest ID present.
        id_count: Number of different IDs present.
    """

    path: str
    title: str
    rows: int
    columns: tuple[str, ...]
    units: dict[str, str]
    subcases: tuple[int, ...]
    modes: tuple[int, ...]
    id_column: str = ""
    id_min: int | None = None
    id_max: int | None = None
    id_count: int = 0


# --------------------------------------------------------------------------------------
# ID filter expressions
# --------------------------------------------------------------------------------------

_RANGE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


def parse_id_filter(text: str) -> list[tuple[int, int]]:
    """Parse an ID filter such as ``"1001-1200, 3005"`` into inclusive ranges.

    Args:
        text: Comma- or whitespace-separated IDs and ``a-b`` ranges. Empty = no filter.

    Returns:
        List of ``(first, last)`` ranges; empty list means "all IDs".

    Raises:
        NastranH5Error: On syntax errors or reversed ranges.
    """
    ranges: list[tuple[int, int]] = []
    normalised = re.sub(r"\s*-\s*", "-", text.strip())
    for part in re.split(r"[,;\s]+", normalised):
        if not part:
            continue
        m = _RANGE.match(part)
        if not m:
            raise NastranH5Error(f"Invalid ID filter element {part!r} (expected 12 or 10-20)")
        first = int(m.group(1))
        last = int(m.group(2)) if m.group(2) else first
        if last < first:
            raise NastranH5Error(f"Invalid ID range {part!r}: end before start")
        ranges.append((first, last))
    return ranges


def _id_mask(ids: IdArray, ranges: Sequence[tuple[int, int]]) -> npt.NDArray[np.bool_]:
    """Return a mask of the IDs that fall into any of the ranges (all if no ranges)."""
    if not ranges:
        return np.ones(ids.shape, dtype=bool)
    mask = np.zeros(ids.shape, dtype=bool)
    for first, last in ranges:
        mask |= (ids >= first) & (ids <= last)
    return mask


# --------------------------------------------------------------------------------------
# File access helpers
# --------------------------------------------------------------------------------------


def _open(path: str | Path) -> h5py.File:
    """Open an HDF5 file read-only with a clear error message."""
    p = Path(path)
    if not p.is_file():
        raise NastranH5Error(f"File not found: {p}")
    try:
        return h5py.File(p, "r")
    except OSError as exc:
        raise NastranH5Error(f"Cannot open {p} as HDF5 file: {exc}") from exc


def normalise_result_path(result: str) -> str:
    """Return a dataset path relative to ``/NASTRAN/RESULT``.

    Accepts absolute paths, relative paths and short forms such as ``STRESS/QUAD4``.
    """
    rel = result.strip().strip("/")
    prefix = RESULT_ROOT.strip("/") + "/"
    if rel.startswith(prefix):
        rel = rel[len(prefix) :]
    if rel.split("/", 1)[0] in {"STRESS", "STRAIN", "ELEMENT_FORCE", "STRAIN_ENERGY"}:
        rel = "ELEMENTAL/" + rel
    return rel


def _structured_to_frame(data: npt.NDArray[Any], stations: bool = False) -> pd.DataFrame:
    """Convert a NumPy structured array to a DataFrame.

    Array fields are flattened to ``NAME_1 .. NAME_n`` columns, or - with ``stations`` -
    expanded to one row per station (a ``STATION`` column is added; stations after the
    first whose ``SD`` is zero are unused and dropped). Byte strings are decoded.
    """
    names = data.dtype.names or ()
    array_fields = [n for n in names if data.dtype[n].shape]
    if stations and array_fields:
        n_st = data.dtype[array_fields[0]].shape[0]
        columns: dict[str, Any] = {}
        for name in names:
            values = data[name]
            columns[name] = (
                values.reshape(len(data), -1).ravel()
                if data.dtype[name].shape
                else np.repeat(values, n_st)
            )
        columns["STATION"] = np.tile(np.arange(1, n_st + 1), len(data))
        df = pd.DataFrame(columns)
        if "SD" in df.columns:
            df = df[(df["STATION"] == 1) | (df["SD"] > 0)].reset_index(drop=True)
        return df

    columns = {}
    for name in names:
        values = data[name]
        shape = data.dtype[name].shape
        if shape:
            flat = values.reshape(len(data), -1)
            for i in range(flat.shape[1]):
                columns[f"{name}_{i + 1}"] = flat[:, i]
        elif values.dtype.kind == "S":
            columns[name] = np.char.decode(values, "ascii", errors="replace")
            columns[name] = np.char.strip(columns[name])
        else:
            columns[name] = values
    return pd.DataFrame(columns)


def _attr_text(value: Any) -> str:
    """Return an HDF5 attribute value (bytes, string or one-element array) as text."""
    if isinstance(value, np.ndarray):
        value = value.ravel()[0] if value.size else ""
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


def _version(h5: h5py.File) -> str:
    """Return the version attribute of an open file (root or ``/NASTRAN``), else ``""``."""
    for obj in (h5, h5.get("NASTRAN")):
        if obj is None:
            continue
        for key, value in obj.attrs.items():
            if "VERSION" in key.upper():
                return _attr_text(value)
    return ""


def nastran_version(path: str | Path) -> str:
    """Return the Nastran version stored in the file, or ``""`` if not found."""
    with _open(path) as h5:
        return _version(h5)


def file_info(path: str | Path) -> FileInfo:
    """Return version, solution number, input deck and run time of a result file (NAS-14).

    Args:
        path: ``.h5`` file.

    Returns:
        The information stored in the file; fields that are not stored stay empty.
    """
    with _open(path) as h5:
        group = h5.get("NASTRAN")
        attrs = dict(group.attrs) if group is not None else {}
        solution: int | None = None
        if "SOL" in attrs:
            try:
                solution = int(_attr_text(attrs["SOL"]))
            except ValueError:
                solution = None
        return FileInfo(
            version=_version(h5),
            solution=solution,
            input_deck=_attr_text(attrs["INPUT"]) if "INPUT" in attrs else "",
            run_time=_attr_text(attrs["TIME"]) if "TIME" in attrs else "",
            synthetic=bool(h5.attrs.get("APB_SYNTHETIC", False)),
        )


def is_synthetic(path: str | Path) -> bool:
    """Return ``True`` for synthetic test files written by ``tools/make_synthetic_h5.py``."""
    with _open(path) as h5:
        return bool(h5.attrs.get("APB_SYNTHETIC", False))


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


def list_results(path: str | Path) -> list[ResultInfo]:
    """List the result datasets in a file (NAS-01).

    Args:
        path: ``.h5`` file.

    Returns:
        One entry per dataset below ``/NASTRAN/RESULT`` (except ``DOMAINS``), sorted by path.
    """
    found: list[ResultInfo] = []
    with _open(path) as h5:
        root = h5.get(RESULT_ROOT)
        if root is None:
            raise NastranH5Error(f"{path} has no {RESULT_ROOT} group - not a Nastran result file?")

        def visit(name: str, obj: Any) -> None:
            if not isinstance(obj, h5py.Dataset) or name == "DOMAINS":
                return
            mapping = RESULT_MAPPINGS.get(name)
            rows = int(obj.shape[0]) if obj.shape else 0
            found.append(
                ResultInfo(
                    path=name,
                    title=mapping.title if mapping else name,
                    rows=rows,
                    fields=tuple(obj.dtype.names or ()),
                    known=mapping is not None,
                    size=(mapping.size_label if mapping else "")
                    or (f"{rows} rows" if rows != 1 else "1 row"),
                )
            )

        root.visititems(visit)
    return sorted(found, key=lambda r: r.path)


def read_domains(path: str | Path) -> pd.DataFrame:
    """Read the DOMAINS table (subcase, mode, eigenvalue ... per domain ID).

    Args:
        path: ``.h5`` file.

    Returns:
        Table with all DOMAINS fields, ``ID`` being the domain ID.
    """
    with _open(path) as h5:
        if DOMAINS_PATH not in h5:
            raise NastranH5Error(f"{path} has no {DOMAINS_PATH} table")
        return _structured_to_frame(h5[DOMAINS_PATH][()])


def subcases(path: str | Path) -> list[int]:
    """Return the subcase IDs present in a file."""
    return sorted(int(s) for s in read_domains(path)["SUBCASE"].unique())


def _domain_columns(domains: pd.DataFrame) -> pd.DataFrame:
    """Select the domain columns joined onto result rows.

    Args:
        domains: The DOMAINS rows referenced by the result rows.

    Returns:
        ``DOMAIN_ID``, plus ``SUBCASE`` if the domains belong to a subcase, plus ``MODE`` and
        ``EIGENVALUE`` if per-mode domains are referenced, plus ``STEP`` if several steps are
        referenced. Summary tables (eigenvalue summary, modal effective mass, grid point
        weight) reference domains without subcase or mode data (spec 02 §3.8).
    """
    cols = {"ID": "DOMAIN_ID"}
    if "SUBCASE" in domains and (domains["SUBCASE"] > 0).any():
        cols["SUBCASE"] = "SUBCASE"
    if "STEP" in domains and domains["STEP"].nunique() > 1:
        cols["STEP"] = "STEP"
    if (
        "ANALYSIS" in domains
        and "MODE" in domains
        and ((domains["ANALYSIS"] == ANALYSIS_MODES) & (domains["MODE"] > 0)).any()
    ):
        cols["MODE"] = "MODE"
        cols["TIME_FREQ_EIGR"] = "EIGENVALUE"
    return domains[list(cols)].rename(columns=cols)


def _to_frame(data: npt.NDArray[Any], mapping: ResultMapping | None) -> pd.DataFrame:
    """Convert raw dataset rows to a table, using the mapping's transform if it has one."""
    if mapping and mapping.transform:
        return mapping.transform(data)
    return _structured_to_frame(data, stations=bool(mapping and mapping.stations))


def _id_field(names: Sequence[str], mapping: ResultMapping | None) -> str | None:
    """Return the dataset field holding the entity ID, or ``None``."""
    if mapping:
        return mapping.id_field
    return "EID" if "EID" in names else "ID" if "ID" in names else None


def read_result(
    path: str | Path,
    result: str,
    *,
    subcases: Iterable[int] | None = None,
    ids: str | Sequence[tuple[int, int]] = "",
    columns: Sequence[str] | None = None,
    unit_system: UnitSystem | None = None,
    max_rows: int | None = None,
) -> pd.DataFrame:
    """Read one result dataset into a table (NAS-02 .. NAS-05, NAS-08).

    Args:
        path: ``.h5`` file.
        result: Dataset path, e.g. ``"ELEMENTAL/STRESS/QUAD4"`` (``"STRESS/QUAD4"`` and
            absolute paths are accepted too).
        subcases: Subcases to keep (``None`` or empty = all).
        ids: ID filter (expression such as ``"101-120, 135"`` or parsed ranges).
        columns: Value columns to keep (ID and domain columns are always kept).
        unit_system: Unit system of the model; used for the unit labels.
        max_rows: Read at most this many (matching) dataset rows, e.g. for a preview.

    Returns:
        Long-format table: ID column (``GRID``/``EID``), ``SUBCASE`` (and ``MODE``,
        ``EIGENVALUE``, ``STATION`` where relevant) followed by the values. ``attrs`` hold
        units, source file, dataset and Nastran version.

    Raises:
        NastranH5Error: If the dataset or a requested column does not exist.
    """
    rel = normalise_result_path(result)
    mapping = RESULT_MAPPINGS.get(rel)
    ranges = parse_id_filter(ids) if isinstance(ids, str) else list(ids)
    wanted_subcases = sorted(set(subcases or []))

    domains = read_domains(path)
    with _open(path) as h5:
        full = f"{RESULT_ROOT}/{rel}"
        if full not in h5 or not isinstance(h5[full], h5py.Dataset):
            available = ", ".join(r.path for r in list_results(path))
            raise NastranH5Error(f"Result {rel!r} not found in {path}. Available: {available}")
        ds = h5[full]
        names = ds.dtype.names or ()

        # Row selection on IDs and domains, reading only the needed columns first (NAS-09).
        mask = np.ones(ds.shape[0], dtype=bool)
        id_field = _id_field(names, mapping)
        if ranges:
            if id_field is None:
                raise NastranH5Error(f"Result {rel!r} has no ID field to filter on")
            mask &= _id_mask(ds.fields(id_field)[()], ranges)
        if wanted_subcases and "DOMAIN_ID" in names:
            unknown = set(wanted_subcases) - set(domains["SUBCASE"].astype(int))
            if unknown:
                raise NastranH5Error(f"Subcase(s) {sorted(unknown)} not in {path}")
            dom_ids = domains.loc[domains["SUBCASE"].isin(wanted_subcases), "ID"].to_numpy()
            mask &= np.isin(ds.fields("DOMAIN_ID")[()], dom_ids)
        if mask.all():
            data = ds[()] if max_rows is None else ds[:max_rows]
        else:
            rows = np.flatnonzero(mask)
            if max_rows is not None and rows.size > max_rows:
                rows = rows[:max_rows]
                mask[int(rows[-1]) + 1 :] = False
            if rows.size == 0:
                data = ds[0:0]
            else:
                block = ds[int(rows[0]) : int(rows[-1]) + 1]
                data = block[mask[rows[0] : rows[-1] + 1]]

        df = _to_frame(data, mapping)
        info_attrs = dict(h5["NASTRAN"].attrs) if "NASTRAN" in h5 else {}
        version = _version(h5)

    if mapping:
        df = df.rename(columns={**{mapping.id_field or "": mapping.id_column}, **mapping.rename})
    if "DOMAIN_ID" in df.columns:
        used = domains[domains["ID"].isin(df["DOMAIN_ID"].unique())]
        dom = _domain_columns(used)
        dom = dom[[c for c in dom.columns if c == "DOMAIN_ID" or c not in df.columns]]
        df = df.merge(dom, on="DOMAIN_ID", how="left").drop(columns="DOMAIN_ID")
    if mapping:
        for derive in mapping.derive:
            derive(df)
    df = _order_columns(df, mapping)

    if columns:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise NastranH5Error(
                f"Column(s) {missing} not in result {rel!r}. Available: {list(df.columns)}"
            )
        keys = [c for c in df.columns if c in _KEY_COLUMNS]
        df = df[keys + [c for c in columns if c not in keys]]

    system = unit_system
    units: dict[str, str] = {}
    if mapping and system is not None:
        units = {col: unit_label(kind, system) for col, kind in mapping.kinds.items()}
    set_units(df, system, units)
    df.attrs.update(
        {
            "source_file": str(path),
            "result": rel,
            "title": mapping.title if mapping else rel,
            "nastran_version": version,
        }
    )
    for attr, key in (("SOL", "nastran_solution"), ("INPUT", "nastran_input")):
        if attr in info_attrs:
            df.attrs[key] = _attr_text(info_attrs[attr])
    return df


#: ID, domain and row-label columns: placed first and always kept by a column selection.
_KEY_COLUMNS = (
    "GRID",
    "EID",
    "SUBCASE",
    "STEP",
    "MODE",
    "EIGENVALUE",
    "STATION",
    "DOF",
    "DIRECTION",
)


def describe_result(
    path: str | Path, result: str, unit_system: UnitSystem | None = None
) -> ResultDetail:
    """Describe one result dataset: columns, subcases, modes and IDs present (NAS-13).

    Only the ID column, the domain references and one row of data are read.

    Args:
        path: ``.h5`` file.
        result: Dataset path as for :func:`read_result`.
        unit_system: Unit system of the model, for the unit labels.

    Returns:
        The choices the dataset offers.

    Raises:
        NastranH5Error: If the dataset does not exist.
    """
    rel = normalise_result_path(result)
    mapping = RESULT_MAPPINGS.get(rel)
    sample = read_result(path, rel, unit_system=unit_system, max_rows=1)
    domains = read_domains(path)
    with _open(path) as h5:
        ds = h5[f"{RESULT_ROOT}/{rel}"]
        names = ds.dtype.names or ()
        rows = int(ds.shape[0]) if ds.shape else 0
        domain_ids: npt.NDArray[Any] = np.empty(0, dtype=np.int64)
        index = h5.get(f"{INDEX_ROOT}/{rel}")
        if isinstance(index, h5py.Dataset) and "DOMAIN_ID" in (index.dtype.names or ()):
            domain_ids = index.fields("DOMAIN_ID")[()]
        elif "DOMAIN_ID" in names and rows:
            domain_ids = np.unique(ds.fields("DOMAIN_ID")[()])
        id_field = _id_field(names, mapping)
        ids: npt.NDArray[Any] = np.empty(0, dtype=np.int64)
        if id_field is not None and id_field in names and rows:
            ids = np.unique(ds.fields(id_field)[()])

    used = domains[domains["ID"].isin(domain_ids)]
    subcase_list = sorted({int(s) for s in used["SUBCASE"] if s > 0}) if "SUBCASE" in used else []
    mode_list = sorted({int(m) for m in used["MODE"] if m > 0}) if "MODE" in used else []
    id_column = ""
    if id_field is not None:
        id_column = mapping.id_column if mapping else id_field
    units: Any = sample.attrs.get("units", {})
    return ResultDetail(
        path=rel,
        title=mapping.title if mapping else rel,
        rows=rows,
        columns=tuple(str(c) for c in sample.columns if c not in _KEY_COLUMNS),
        units=dict(units),
        subcases=tuple(subcase_list),
        modes=tuple(mode_list),
        id_column=id_column if ids.size else "",
        id_min=int(ids[0]) if ids.size else None,
        id_max=int(ids[-1]) if ids.size else None,
        id_count=int(ids.size),
    )


def _order_columns(df: pd.DataFrame, mapping: ResultMapping | None) -> pd.DataFrame:
    """Put ID and domain columns first, in a fixed order."""
    first = [mapping.id_column] if mapping else []
    first += [c for c in _KEY_COLUMNS if c not in first]
    lead = [c for c in first if c in df.columns]
    return df[lead + [c for c in df.columns if c not in lead]]


def read_eigenvalues(path: str | Path, unit_system: UnitSystem | None = None) -> pd.DataFrame:
    """Read the eigenvalue summary (mode, eigenvalue, frequency, generalised mass/stiffness).

    Args:
        path: ``.h5`` file.
        unit_system: Unit system of the model.

    Returns:
        One row per mode and modal subcase.
    """
    return read_result(path, "SUMMARY/EIGENVALUE", unit_system=unit_system)


def read_input_table(path: str | Path, dataset: str) -> pd.DataFrame:
    """Read a model (input) dataset raw, e.g. ``ELEMENT/CONM2``.

    Args:
        path: ``.h5`` file.
        dataset: Path relative to ``/NASTRAN/INPUT``.

    Returns:
        Table with all fields (array fields flattened).
    """
    full = f"{INPUT_ROOT}/{dataset.strip('/')}"
    with _open(path) as h5:
        if full not in h5:
            raise NastranH5Error(f"Model table {dataset!r} not found in {path}")
        return _structured_to_frame(h5[full][()])


def rbe2_connectivity(path: str | Path) -> pd.DataFrame:
    """Return the grids of every RBE2 element.

    Args:
        path: ``.h5`` file.

    Returns:
        Table ``EID, GRID, SIDE`` with ``SIDE`` = ``"independent"`` or ``"dependent"``.
    """
    rb = read_input_table(path, "ELEMENT/RBE2/RB")
    gm = read_input_table(path, "ELEMENT/RBE2/GM")["ID"].to_numpy()
    rows: list[dict[str, Any]] = []
    fields = [rb[c].to_numpy(dtype=np.int64) for c in ("EID", "GN", "GM_POS", "GM_LEN")]
    for eid_raw, gn, start, length in zip(*fields, strict=True):
        eid = int(eid_raw)
        rows.append({"EID": eid, "GRID": int(gn), "SIDE": "independent"})
        for g in gm[int(start) : int(start) + int(length)]:
            rows.append({"EID": eid, "GRID": int(g), "SIDE": "dependent"})
    return pd.DataFrame(rows, columns=["EID", "GRID", "SIDE"])


RbeSource = Literal["auto", "mpc_force", "gpforce"]


def read_rbe2_loads(
    path: str | Path,
    *,
    source: RbeSource = "auto",
    side: Literal["independent", "dependent"] = "dependent",
    eids: str = "",
    subcases: Iterable[int] | None = None,
    unit_system: UnitSystem | None = None,
) -> pd.DataFrame:
    """Read the forces carried by RBE2 elements (spec 02 §3.6).

    Args:
        path: ``.h5`` file.
        source: ``mpc_force`` (MPCFORCES output), ``gpforce`` (grid point force balance)
            or ``auto`` (MPC forces if present, else grid point forces).
        side: Report the forces at the independent or the dependent grids.
        eids: RBE2 element ID filter expression.
        subcases: Subcases to keep (``None`` = all).
        unit_system: Unit system of the model.

    Returns:
        Table ``EID, GRID, SUBCASE, FX, FY, FZ, MX, MY, MZ`` in the grids' output systems.
    """
    available = {r.path for r in list_results(path)}
    if source == "auto":
        source = "mpc_force" if "NODAL/MPC_FORCE" in available else "gpforce"
    needed = "NODAL/MPC_FORCE" if source == "mpc_force" else "NODAL/GRID_FORCE"
    if needed not in available:
        raise NastranH5Error(f"{path} has no {needed} result (needed for RBE2 source {source!r})")

    conn = rbe2_connectivity(path)
    conn = conn[(conn["SIDE"] == side) & _id_mask(conn["EID"].to_numpy(), parse_id_filter(eids))]
    grids = ",".join(str(g) for g in sorted(conn["GRID"].unique()))
    if not grids:
        raise NastranH5Error("No RBE2 elements match the filter")
    forces = read_result(path, needed, subcases=subcases, ids=grids, unit_system=unit_system)

    components = ["FX", "FY", "FZ", "MX", "MY", "MZ"]
    if source == "mpc_force":
        forces = forces.rename(
            columns=dict(zip(["X", "Y", "Z", "RX", "RY", "RZ"], components, strict=True))
        )
        out = conn[["EID", "GRID"]].merge(forces, on="GRID")
    else:
        forces = forces[forces["ELNAME"] == "RBE2"].rename(
            columns=dict(zip(["F1", "F2", "F3", "M1", "M2", "M3"], components, strict=True))
        )
        out = conn[["EID", "GRID"]].merge(forces.drop(columns="ELNAME"), on=["EID", "GRID"])
    keys = [c for c in ("EID", "GRID", "SUBCASE") if c in out.columns]
    out = out[keys + components].sort_values(keys).reset_index(drop=True)

    units: dict[str, str] = {}
    if unit_system is not None:
        units = {
            c: unit_label(
                QuantityKind.FORCE if c.startswith("F") else QuantityKind.MOMENT, unit_system
            )
            for c in components
        }
    set_units(out, unit_system, units)
    out.attrs.update(
        {
            "source_file": str(path),
            "result": f"RBE2 loads ({source})",
            "title": "RBE2 loads",
            "nastran_version": forces.attrs["nastran_version"],
        }
    )
    return out


def read_masses(path: str | Path, unit_system: UnitSystem | None = None) -> pd.DataFrame:
    """Read CONM2 point masses from the model data (NAS-06b).

    Args:
        path: ``.h5`` file.
        unit_system: Unit system of the model.

    Returns:
        Table ``EID, GRID, CID, MASS, X1, X2, X3, I11, I21, I22, I31, I32, I33``.
    """
    raw = read_input_table(path, "ELEMENT/CONM2")
    rename = {"G": "GRID", "M": "MASS", "X_1": "X1", "X_2": "X2", "X_3": "X3"}
    rename.update(
        {f"I_{i + 1}": n for i, n in enumerate(["I11", "I21", "I22", "I31", "I32", "I33"])}
    )
    df = raw.rename(columns=rename)
    df = df[
        [
            c
            for c in [
                "EID",
                "GRID",
                "CID",
                "MASS",
                "X1",
                "X2",
                "X3",
                "I11",
                "I21",
                "I22",
                "I31",
                "I32",
                "I33",
            ]
            if c in df.columns
        ]
    ]
    units: dict[str, str] = {}
    if unit_system is not None:
        units["MASS"] = unit_label(QuantityKind.MASS, unit_system)
        units.update({c: unit_label(QuantityKind.LENGTH, unit_system) for c in ("X1", "X2", "X3")})
        units.update(
            {
                c: unit_label(QuantityKind.MASS_INERTIA, unit_system)
                for c in ("I11", "I21", "I22", "I31", "I32", "I33")
            }
        )
    set_units(df, unit_system, units)
    df.attrs.update({"source_file": str(path), "title": "CONM2 point masses"})
    return df
