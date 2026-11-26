"""Stored outputs: block results on disk next to the pipeline (spec 08).

Every finished block gets one *entry*, a folder ``<store>/<block id>/`` holding
``meta.json`` (what the entry is) and the data files: tables as Parquet, figures as PNG;
numbers and texts live in ``meta.json``. There is **no pickle**: stored outputs are data.

This module knows nothing about pipelines or the executor. It offers

* :func:`encode_value` / :func:`decode_value` - one value to and from files (pure),
* :class:`OutputStore` - the entries of one pipeline: save, read, clear,
* :class:`StoredOutputs` - the outputs of an entry, read from disk when first used,
* :class:`StoredFigure` - a figure restored as an image.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import time
import uuid
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import pandas as pd

from apb import __version__
from apb.units import Quantity

#: Version of the layout described in spec 08 §3.2.
SCHEMA = 1

#: Hidden folder next to the pipeline file that holds the stores.
STORE_DIR = ".apb"

#: Resolution of stored figures; reports and previews scale from it.
FIGURE_DPI = 300

#: Temporary folders older than this (seconds) are leftovers of a crash and are removed.
_STALE_TMP_SECONDS = 3600

_SAFE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


class StoreError(Exception):
    """A value or entry cannot be stored or read; the message is for the user."""


class StoreUnavailableError(StoreError):
    """The store folder cannot be created or written (e.g. read-only)."""


# --------------------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------------------


class StoredFigure:
    """A figure restored from disk: an image with the ``savefig`` method of a figure.

    It is *not* a live matplotlib figure: it can be written again (reports and previews do
    this) but nothing can be drawn into it (spec 08 §3.3).

    Args:
        png: The stored PNG data.
        dpi: Resolution the PNG was rendered at.
        width_cm: Width the figure was made for (``apb_width_cm``), if known.
    """

    def __init__(self, png: bytes, dpi: int = FIGURE_DPI, width_cm: float | None = None) -> None:
        self._png = png
        self._dpi = dpi
        if width_cm is not None:
            self.apb_width_cm = float(width_cm)

    def savefig(
        self, fname: str | os.PathLike[str] | BinaryIO, *, format: str = "png", **kwargs: Any
    ) -> None:
        """Write the image as PNG, scaled to ``dpi`` if one is given.

        Args:
            fname: File name or binary file object.
            format: Must be ``png``.
            **kwargs: ``dpi`` rescales the image; other ``Figure.savefig`` options (such as
                ``bbox_inches``) are accepted and ignored, the image is already cropped.

        Raises:
            ValueError: For formats other than PNG.
        """
        if format.lower() != "png":
            raise ValueError(f"A restored figure can only be written as PNG, not {format!r}")
        dpi = kwargs.get("dpi")
        data = self._png if not dpi or int(dpi) == self._dpi else self._scaled(int(dpi))
        if hasattr(fname, "write"):
            fname.write(data)
        else:
            Path(os.fspath(fname)).write_bytes(data)

    def _scaled(self, dpi: int) -> bytes:
        from PIL import Image

        with Image.open(io.BytesIO(self._png)) as image:
            factor = dpi / self._dpi
            size = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
            out = io.BytesIO()
            image.resize(size, Image.Resampling.LANCZOS).save(out, format="PNG", dpi=(dpi, dpi))
            return out.getvalue()


# --------------------------------------------------------------------------------------
# Encoding single values
# --------------------------------------------------------------------------------------

type Description = dict[str, Any]


def _json_exact(value: Any) -> bool:
    """Return whether JSON stores ``value`` without changing it (types and numbers)."""
    if value is None or isinstance(value, bool | str):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_exact(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _json_exact(v) for k, v in value.items())
    return False


def _label(label: Any) -> Any:
    """Return a column label in a JSON-exact form (tuples become lists)."""
    if isinstance(label, tuple):
        return [_label(part) for part in label]
    if isinstance(label, np.integer):
        return int(label)
    if isinstance(label, np.floating):
        return float(label)
    if not _json_exact(label) or isinstance(label, list | dict):
        raise StoreError(f"column label {label!r} cannot be stored")
    return label


def _number(value: Any) -> Description:
    """Describe an int or float (NaN and infinities become strings)."""
    if isinstance(value, int | np.integer):
        return {"type": "int", "value": int(value)}
    number = float(value)
    if math.isnan(number):
        return {"type": "float", "value": "nan"}
    if math.isinf(number):
        return {"type": "float", "value": "inf" if number > 0 else "-inf"}
    return {"type": "float", "value": number}


def _unnumber(desc: Mapping[str, Any]) -> int | float:
    value = desc["value"]
    if desc["type"] == "int":
        return int(value)
    return float(value)  # also "nan", "inf" and "-inf"


def encode_value(value: Any, folder: Path, stem: str) -> Description:
    """Write one value's data to ``folder`` and return its description for ``meta.json``.

    Args:
        value: A table, number, number with unit, text, figure, or JSON-like structure.
        folder: Existing folder for data files.
        stem: File name stem for values that need a file.

    Returns:
        The description (``kind`` and what is needed to read the value back).

    Raises:
        StoreError: If the value cannot be stored exactly.
    """
    if isinstance(value, pd.DataFrame):
        return _encode_table(value, folder, stem)
    if isinstance(value, Quantity):
        return {"kind": "scalar", **_number(value.value), "unit": value.unit}
    if isinstance(value, bool):
        return {"kind": "json", "value": value}
    if isinstance(value, int | float | np.integer | np.floating):
        return {"kind": "scalar", **_number(value), "unit": ""}
    if isinstance(value, str):
        return {"kind": "text", "value": value}
    if hasattr(value, "savefig"):
        return _encode_figure(value, folder, stem)
    if value is None or _json_exact(value):
        return {"kind": "json", "value": value}
    raise StoreError(f"a value of type {type(value).__name__} cannot be stored")


def decode_value(desc: Mapping[str, Any], folder: Path) -> Any:
    """Read a value back from its description and the data files in ``folder``.

    Raises:
        StoreError: If the description is unknown or a data file is missing or unreadable.
    """
    kind = desc.get("kind")
    try:
        if kind == "table":
            return _decode_table(desc, folder)
        if kind == "scalar":
            number = _unnumber(desc)
            return Quantity(float(number), desc["unit"]) if desc.get("unit") else number
        if kind == "text":
            return str(desc["value"])
        if kind == "json":
            return desc["value"]
        if kind == "figure":
            width = desc.get("width_cm")
            return StoredFigure(
                (folder / desc["file"]).read_bytes(),
                int(desc["dpi"]),
                float(width) if width is not None else None,
            )
    except StoreError:
        raise
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise StoreError(f"stored {kind} cannot be read: {exc}") from exc
    raise StoreError(f"unknown kind of stored value: {kind!r}")


def _encode_figure(figure: Any, folder: Path, stem: str) -> Description:
    buffer = io.BytesIO()
    try:
        figure.savefig(buffer, format="png", dpi=FIGURE_DPI, bbox_inches="tight")
    except Exception as exc:
        raise StoreError(f"the figure cannot be rendered: {exc}") from exc
    data = buffer.getvalue()
    (folder / f"{stem}.png").write_bytes(data)
    desc: Description = {"kind": "figure", "file": f"{stem}.png", "dpi": FIGURE_DPI}
    width = getattr(figure, "apb_width_cm", None)
    if isinstance(width, int | float) and width > 0:
        desc["width_cm"] = float(width)  # the report's default width (spec 13 §3.4)
    desc["bytes"] = len(data)
    return desc


# ---- tables ---------------------------------------------------------------------------


def _encode_table(df: pd.DataFrame, folder: Path, stem: str) -> Description:
    """Store a table as Parquet under neutral column names, plus a JSON description.

    Parquet needs unique text column names and has no two-level headers, so the data goes in
    as ``c0 … cN`` and the real labels, the index, the dtypes and ``attrs`` are described in
    the entry (spec 08 §3.3).
    """
    rows, ncols = df.shape
    labels = [_label(c) for c in df.columns]
    attrs = dict(df.attrs)
    if not _json_exact(attrs) or json.loads(json.dumps(attrs)) != attrs:
        raise StoreError("the table's metadata (attrs) is not JSON-exact")
    object_missing = _object_columns(df)
    desc: Description = {
        "kind": "table",
        "rows": rows,
        "columns": labels,
        "multi_columns": isinstance(df.columns, pd.MultiIndex),
        "column_names": [
            n if isinstance(n, str) or n is None else str(n) for n in df.columns.names
        ],
        "columns_dtype": str(df.columns.dtype),
        "columns_range": (
            [df.columns.start, df.columns.stop, df.columns.step]
            if isinstance(df.columns, pd.RangeIndex)
            else None
        ),
        "dtypes": [str(t) for t in df.dtypes],
        "object_missing": object_missing,
        "attrs": attrs,
    }
    index = df.index
    if isinstance(index, pd.RangeIndex):
        desc["index"] = {
            "kind": "range",
            "start": index.start,
            "stop": index.stop,
            "step": index.step,
            "name": index.name,
        }
        index_frame = pd.DataFrame(index=range(rows))
    else:
        index_frame = index.to_frame(index=False)
        names = [str(n) if n is not None else None for n in index.names]
        desc["index"] = {
            "kind": "columns",
            "names": names,
            "dtypes": [str(t) for t in index_frame.dtypes],
            "multi": isinstance(index, pd.MultiIndex),
        }
        index_frame.columns = [f"i{k}" for k in range(index_frame.shape[1])]
    flat = df.copy(deep=False)
    flat.columns = [f"c{i}" for i in range(ncols)]
    flat = pd.concat([index_frame.reset_index(drop=True), flat.reset_index(drop=True)], axis=1)
    if flat.shape[1] == 0:
        return desc  # no data columns and a default index: nothing to write
    path = folder / f"{stem}.parquet"
    try:
        flat.to_parquet(path, engine="pyarrow", index=False, compression="snappy")
    except Exception as exc:  # pyarrow raises several types for unsupported data
        path.unlink(missing_ok=True)
        raise StoreError(f"the table cannot be stored as Parquet: {exc}") from exc
    desc["file"] = f"{stem}.parquet"
    desc["bytes"] = path.stat().st_size
    return desc


def _object_columns(df: pd.DataFrame) -> dict[str, str]:
    """Check the columns of dtype ``object`` and note how their missing values are written.

    Parquet would store a column of dicts as a "struct" and return it changed, so only
    text columns are accepted. Missing values come back as NaN, so ``None`` is recorded.

    Returns:
        Column position -> ``"none"`` or ``"nan"``, for columns that have missing values.

    Raises:
        StoreError: For an object column that is not text, or that mixes ``None`` and NaN.
    """
    markers: dict[str, str] = {}
    for position, dtype in enumerate(df.dtypes):
        if str(dtype) != "object":
            continue
        column = df.iloc[:, position]
        kind = pd.api.types.infer_dtype(column, skipna=True)
        if kind not in ("string", "empty"):
            raise StoreError(
                f"column {df.columns[position]!r} holds {kind} values; only text is stored "
                "in a column of mixed or unsupported type"
            )
        missing = column[column.isna()]
        if len(missing):
            nones = sum(v is None for v in missing)
            if nones not in (0, len(missing)):
                raise StoreError(f"column {df.columns[position]!r} mixes None and NaN")
            markers[str(position)] = "none" if nones else "nan"
    return markers


def _restore_columns(flat: pd.DataFrame, desc: Mapping[str, Any]) -> pd.DataFrame:
    """Return the data columns ``c0 … cN`` with the stored dtypes (and ``None`` markers)."""
    markers: Mapping[str, str] = desc.get("object_missing", {})
    series: dict[str, pd.Series] = {}
    for i, dtype in enumerate(desc["dtypes"]):
        column = flat[f"c{i}"]
        if str(column.dtype) != dtype:
            column = column.astype(dtype)
        if markers.get(str(i)) == "none":
            values = column.to_numpy(dtype=object, copy=True)
            values[column.isna().to_numpy()] = None
            column = pd.Series(values, index=column.index, dtype=object)
        if str(column.dtype) != dtype:
            raise StoreError(f"column {i} came back as {column.dtype}, not {dtype}")
        series[f"c{i}"] = column
    return pd.DataFrame(series, index=flat.index)


def _decode_table(desc: Mapping[str, Any], folder: Path) -> pd.DataFrame:
    rows = int(desc["rows"])
    if "file" in desc:
        with (folder / desc["file"]).open("rb") as handle:  # closed again: deletable on Windows
            flat = pd.read_parquet(handle, engine="pyarrow")
    else:
        flat = pd.DataFrame(index=range(rows))
    if len(flat) != rows:
        raise StoreError(f"the stored table has {len(flat)} rows, expected {rows}")

    data = _restore_columns(flat, desc)

    if desc.get("columns_range"):
        start, stop, step = desc["columns_range"]
        columns: pd.Index = pd.RangeIndex(start, stop, step, name=desc["column_names"][0])
    elif desc["multi_columns"]:
        columns = pd.MultiIndex.from_tuples(
            [tuple(label) for label in desc["columns"]], names=desc["column_names"]
        )
    else:
        columns = pd.Index(
            desc["columns"], dtype=desc["columns_dtype"], name=desc["column_names"][0]
        )
    data.columns = columns

    spec = desc["index"]
    if spec["kind"] == "range":
        data.index = pd.RangeIndex(spec["start"], spec["stop"], spec["step"], name=spec["name"])
    else:
        arrays = []
        for k, dtype in enumerate(spec["dtypes"]):
            level = flat[f"i{k}"]
            arrays.append(level if str(level.dtype) == dtype else level.astype(dtype))
        if spec["multi"]:
            data.index = pd.MultiIndex.from_arrays(arrays, names=spec["names"])
        else:
            data.index = pd.Index(arrays[0], name=spec["names"][0])
    data.attrs = dict(desc["attrs"])
    return data


# --------------------------------------------------------------------------------------
# Entries
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredEntry:
    """The description of one stored block result (``meta.json``).

    Attributes:
        block_id: The block.
        type_id: Block type it was made by.
        type_version: Version of that block type.
        cache_key: The cache key of the run (spec 01 §3.4).
        finished: When the block finished, ISO 8601 with offset.
        duration: Run time in seconds.
        log: The block's log lines.
        written_files: Files the block wrote (reports, workbooks), as recorded.
        outputs: Output port -> description of the stored value.
        tool_version: Version of the tool that wrote the entry.
        schema: Store layout version.
        size: Bytes on disk.
        folder: The entry's folder.
    """

    block_id: str
    type_id: str
    type_version: int
    cache_key: str
    finished: str
    duration: float
    log: tuple[str, ...]
    written_files: tuple[str, ...]
    outputs: dict[str, Description] = field(repr=False)
    tool_version: str
    schema: int
    size: int
    folder: Path

    @property
    def usable(self) -> bool:
        """``True`` if this tool may reuse the entry (same layout, same tool version)."""
        return self.schema == SCHEMA and self.tool_version == __version__


class StoredOutputs(Mapping[str, Any]):
    """The outputs of an entry; a port's data is read from disk the first time it is used.

    Args:
        entry: The entry.
    """

    def __init__(self, entry: StoredEntry) -> None:
        self._entry = entry
        self._loaded: dict[str, Any] = {}

    def __getitem__(self, port: str) -> Any:
        if port not in self._entry.outputs:
            raise KeyError(port)
        if port not in self._loaded:
            try:
                self._loaded[port] = decode_value(self._entry.outputs[port], self._entry.folder)
            except StoreError as exc:
                raise StoreError(
                    f"Stored output '{port}' of block '{self._entry.block_id}' cannot be read "
                    f"({exc}). Clear the stored output of that block and run again."
                ) from exc
        return self._loaded[port]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entry.outputs)

    def __len__(self) -> int:
        return len(self._entry.outputs)


def _safe_name(name: str, what: str) -> str:
    if not _SAFE_NAME.fullmatch(name):
        raise StoreError(f"{what} {name!r} cannot be used as a file name")
    return name


def now_text() -> str:
    """Return the current local time as ``2026-11-15T17:08:12+01:00``."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


class OutputStore:
    """The stored outputs of one pipeline: a folder with one entry per block.

    Args:
        folder: The store folder, e.g. ``examples/.apb/test_case``.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    @classmethod
    def for_pipeline(cls, pipeline_file: Path) -> OutputStore:
        """Return the store of a pipeline file: ``<folder>/.apb/<name without .apb.json>``."""
        path = pipeline_file.resolve()
        stem = path.name
        for suffix in (".apb.json", ".json"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        return cls(path.parent / STORE_DIR / stem)

    # ---- reading ----------------------------------------------------------------------

    def read_entry(self, block_id: str) -> StoredEntry:
        """Read the description of one entry (no data files).

        Raises:
            StoreError: If the entry is missing, incomplete or damaged.
        """
        folder = self.folder / _safe_name(block_id, "block id")
        meta_path = folder / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise StoreError("the entry is incomplete (no meta.json)") from None
        except (OSError, ValueError) as exc:
            raise StoreError(f"meta.json cannot be read: {exc}") from exc
        try:
            schema = int(meta["store_schema"])
            if schema != SCHEMA:  # another layout: known to exist, never reused
                return StoredEntry(
                    block_id, str(meta.get("type_id", "")), 0, "", str(meta.get("finished", "")),
                    0.0, (), (), {}, str(meta.get("tool_version", "")), schema,
                    _tree_size(folder), folder,
                )  # fmt: skip
            entry = StoredEntry(
                block_id=block_id,
                type_id=str(meta["type_id"]),
                type_version=int(meta["type_version"]),
                cache_key=str(meta["cache_key"]),
                finished=str(meta["finished"]),
                duration=float(meta["duration"]),
                log=tuple(str(line) for line in meta["log"]),
                written_files=tuple(str(p) for p in meta["written_files"]),
                outputs=dict(meta["outputs"]),
                tool_version=str(meta["tool_version"]),
                schema=schema,
                size=_tree_size(folder),
                folder=folder,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError(f"meta.json is not valid: {exc!r}") from exc
        for port, desc in entry.outputs.items():
            name = desc.get("file")
            if name and (
                not (folder / name).is_file() or (folder / name).stat().st_size != desc.get("bytes")
            ):
                raise StoreError(f"the data file of output '{port}' is missing or damaged")
        return entry

    def entries(self) -> tuple[dict[str, StoredEntry], dict[str, str]]:
        """Read all entries.

        Returns:
            ``(entries, problems)``: the readable entries by block ID, and a message for each
            entry that is incomplete or damaged.
        """
        entries: dict[str, StoredEntry] = {}
        problems: dict[str, str] = {}
        if not self.folder.is_dir():
            return entries, problems
        for child in sorted(self.folder.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            try:
                entries[child.name] = self.read_entry(child.name)
            except StoreError as exc:
                problems[child.name] = str(exc)
        return entries, problems

    def size(self) -> int:
        """Return the bytes the store occupies on disk."""
        return _tree_size(self.folder)

    def orphans(self, known: Collection[str]) -> list[str]:
        """Return the entries whose block is not in ``known`` (deleted or renamed blocks)."""
        if not self.folder.is_dir():
            return []
        return sorted(
            c.name
            for c in self.folder.iterdir()
            if c.is_dir() and not c.name.startswith(".") and c.name not in known
        )

    # ---- writing ----------------------------------------------------------------------

    def save(
        self,
        block_id: str,
        *,
        type_id: str,
        type_version: int,
        cache_key: str,
        outputs: Mapping[str, Any],
        log: Sequence[str],
        duration: float,
        written_files: Sequence[str] = (),
        finished: str = "",
    ) -> StoredEntry:
        """Write the entry of a block, replacing an earlier one (atomically, STO-09).

        Args:
            block_id: The block.
            type_id: Its block type.
            type_version: Version of that type.
            cache_key: The cache key of the run.
            outputs: Output port -> value.
            log: The block's log lines.
            duration: Run time in seconds.
            written_files: Files the block wrote.
            finished: Time the block finished (default: now).

        Returns:
            The new entry.

        Raises:
            StoreUnavailableError: If the store folder cannot be created or written.
            StoreError: If a value cannot be stored; nothing is left behind.
        """
        _safe_name(block_id, "block id")
        self._prepare()
        self._remove_old_temporaries()
        final = self.folder / block_id
        tmp = self.folder / f".tmp-{block_id}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        try:
            tmp.mkdir()
            described: dict[str, Description] = {}
            for index, (port, value) in enumerate(outputs.items()):
                stem = port if _SAFE_NAME.fullmatch(port) else f"out{index}"
                try:
                    described[port] = encode_value(value, tmp, stem)
                except StoreError as exc:
                    raise StoreError(f"output '{port}': {exc}") from exc
            meta = {
                "store_schema": SCHEMA,
                "tool_version": __version__,
                "block_id": block_id,
                "type_id": type_id,
                "type_version": type_version,
                "cache_key": cache_key,
                "finished": finished or now_text(),
                "duration": duration,
                "log": list(log),
                "written_files": list(written_files),
                "outputs": described,
            }
            (tmp / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            if final.exists():
                shutil.rmtree(final)
            tmp.replace(final)
        except OSError as exc:
            raise StoreError(f"cannot write the entry of block '{block_id}': {exc}") from exc
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return self.read_entry(block_id)

    def clear(self, block_ids: Iterable[str] | None = None) -> list[str]:
        """Delete entries: the given blocks', or everything (the whole store folder).

        Args:
            block_ids: Blocks whose entries are removed; ``None`` removes the store.

        Returns:
            The block IDs whose entries were removed.

        Raises:
            StoreError: If something cannot be deleted.
        """
        if not self.folder.is_dir():
            return []
        try:
            if block_ids is None:
                removed = [c.name for c in self.folder.iterdir() if c.is_dir() and c.name[0] != "."]
                shutil.rmtree(self.folder)
                parent = self.folder.parent
                if parent.name == STORE_DIR and [c.name for c in parent.iterdir()] == [
                    ".gitignore"
                ]:
                    shutil.rmtree(parent)  # nothing but our own .gitignore is left
                return removed
            removed = []
            for block_id in block_ids:
                target = self.folder / _safe_name(block_id, "block id")
                if target.is_dir():
                    shutil.rmtree(target)
                    removed.append(block_id)
            return removed
        except OSError as exc:
            raise StoreError(f"Stored outputs cannot be removed: {exc}") from exc

    # ---- internals --------------------------------------------------------------------

    def _prepare(self) -> None:
        """Create the store folder; the ``.apb`` folder ignores itself in Git."""
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            parent = self.folder.parent
            marker = parent / ".gitignore"
            if parent.name == STORE_DIR and not marker.exists():
                marker.write_text("*\n", encoding="utf-8")
            probe = self.folder / ".write-test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            raise StoreUnavailableError(
                f"{self.folder} cannot be written: {exc.strerror or exc}"
            ) from exc

    def _remove_old_temporaries(self) -> None:
        limit = time.time() - _STALE_TMP_SECONDS
        for child in self.folder.glob(".tmp-*"):
            try:
                if child.stat().st_mtime < limit:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue


def _tree_size(folder: Path) -> int:
    total = 0
    if not folder.is_dir():
        return 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def format_size(size: int) -> str:
    """Return a byte count as ``812 B`` / ``12 kB`` / ``3.4 MB`` for display."""
    value = float(size)
    for unit in ("B", "kB", "MB"):
        if value < 1000:
            return f"{value:.0f} {unit}" if unit == "B" or value >= 10 else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} GB"
