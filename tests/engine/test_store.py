"""The output store: encoding of values, entries on disk, clearing (spec 08)."""

from __future__ import annotations

import io
import json
import math
import os

import matplotlib
import numpy as np
import pandas as pd
import pytest
from PIL import Image

import apb.engine.store as store_module
from apb.engine.store import (
    FIGURE_DPI,
    OutputStore,
    StoredFigure,
    StoredOutputs,
    StoreError,
    StoreUnavailableError,
    decode_value,
    encode_value,
    format_size,
)
from apb.units import Quantity

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def roundtrip(value, tmp_path):
    desc = encode_value(value, tmp_path, "v")
    return decode_value(json.loads(json.dumps(desc)), tmp_path)  # through real JSON


# ---- tables -----------------------------------------------------------------------------


def frames() -> dict[str, pd.DataFrame]:
    with_attrs = pd.DataFrame({"X": [1.0, 2.0]})
    with_attrs.attrs = {"units": {"X": "N·m"}, "title": "Ünïcode", "n": {"a": [1, 2.5, None]}}
    return {
        "plain": pd.DataFrame({"A": [1, 2, 3], "B": [1.5, np.nan, 3.0], "C": ["x", "y", None]}),
        "two_level_header": pd.DataFrame(
            [[1, 2, 3, 4]],
            columns=pd.MultiIndex.from_tuples(
                [("T", "LC1"), ("T", "LC2"), ("S", "LC1"), ("S", "LC2")], names=["QTY", "CASE"]
            ),
        ),
        "duplicate_names": pd.DataFrame([[1, 2]], columns=["a", "a"]),
        "numeric_names": pd.DataFrame([[1, 2]], columns=[10, 20]),
        "range_columns": pd.DataFrame(np.arange(6).reshape(2, 3)),
        "odd_names": pd.DataFrame([[1, 2, 3]], columns=["a b", "ä/ö", "x.y"]),
        "filtered_index": pd.DataFrame({"A": [1, 2]}, index=pd.Index([5, 9], name="eid")),
        "shifted_range": pd.DataFrame({"A": [1, 2, 3]}).iloc[1:],
        "multi_index": pd.DataFrame(
            {"A": [1, 2]}, index=pd.MultiIndex.from_tuples([(1, "a"), (2, "b")], names=["x", "y"])
        ),
        "nullable_int": pd.DataFrame({"A": pd.array([1, None, 3], dtype="Int64")}),
        "categorical": pd.DataFrame(
            {"A": pd.Categorical(["u", "v", "u"], categories=["v", "u", "w"], ordered=True)}
        ),
        "dates": pd.DataFrame({"t": pd.to_datetime(["2026-01-01", "2026-02-01"])}),
        "bool": pd.DataFrame({"f": [True, False]}),
        "no_rows": pd.DataFrame({"A": pd.Series([], dtype="float64")}),
        "no_columns": pd.DataFrame(index=range(3)),
        "empty": pd.DataFrame(),
        "text_object_column": pd.DataFrame({"A": pd.Series(["x", "y"], dtype=object)}),
        "object_with_none": pd.DataFrame({"A": pd.Series(["x", None, "z"], dtype=object)}),
        "object_with_nan": pd.DataFrame({"A": pd.Series(["x", np.nan], dtype=object)}),
        "object_all_missing": pd.DataFrame({"A": pd.Series([None, None], dtype=object)}),
        "attrs": with_attrs,
        "float_precision": pd.DataFrame({"A": [0.1 + 0.2, 1 / 3, 1e-300, 1e300, -0.0]}),
    }


@pytest.mark.parametrize("name", list(frames()))
def test_table_roundtrip_is_exact(name, tmp_path):
    original = frames()[name]
    back = roundtrip(original, tmp_path)
    pd.testing.assert_frame_equal(
        back, original, check_exact=True, check_column_type=True, check_index_type=True
    )
    assert back.attrs == original.attrs
    assert [str(t) for t in back.dtypes] == [str(t) for t in original.dtypes]


def test_table_data_is_in_parquet_under_neutral_names(tmp_path):
    desc = encode_value(frames()["duplicate_names"], tmp_path, "t")
    assert desc["file"] == "t.parquet" and desc["bytes"] == (tmp_path / "t.parquet").stat().st_size
    assert list(pd.read_parquet(tmp_path / "t.parquet").columns) == ["c0", "c1"]
    assert desc["columns"] == ["a", "a"]


def test_tables_that_cannot_be_stored_leave_nothing_behind(tmp_path):
    # Parquet would store dicts as a struct and return them changed: refused up front.
    for column in ([{"x": 1}, {"y": 2}], [[1, 2], [3]], [1, "a"], [b"a", b"b"]):
        with pytest.raises(StoreError, match="only text is stored"):
            encode_value(pd.DataFrame({"A": pd.Series(column, dtype=object)}), tmp_path, "t")
    mixed_missing = pd.DataFrame({"A": pd.Series(["x", None, np.nan], dtype=object)})
    with pytest.raises(StoreError, match="mixes None and NaN"):
        encode_value(mixed_missing, tmp_path, "t")
    assert list(tmp_path.iterdir()) == []
    nan_attrs = pd.DataFrame({"A": [1]})
    nan_attrs.attrs = {"x": float("nan")}
    with pytest.raises(StoreError, match="attrs"):
        encode_value(nan_attrs, tmp_path, "t")
    tuple_key = pd.DataFrame({"A": [1]})
    tuple_key.attrs = {1: "int key becomes text in JSON"}
    with pytest.raises(StoreError, match="attrs"):
        encode_value(tuple_key, tmp_path, "t")
    odd_label = pd.DataFrame([[1]], columns=[pd.Timestamp("2026-01-01")])
    with pytest.raises(StoreError, match="label"):
        encode_value(odd_label, tmp_path, "t")


def test_a_damaged_data_file_is_reported_not_returned(tmp_path):
    desc = encode_value(pd.DataFrame({"A": [1, 2, 3]}), tmp_path, "t")
    (tmp_path / "t.parquet").write_bytes(b"not parquet")
    with pytest.raises(StoreError, match="cannot be read"):
        decode_value(desc, tmp_path)
    desc["rows"] = 5  # description that does not fit the data
    (tmp_path / "t.parquet").unlink()
    encode_value(pd.DataFrame({"A": [1, 2, 3]}), tmp_path, "t")
    with pytest.raises(StoreError, match="expected 5"):
        decode_value(desc, tmp_path)


def test_the_table_file_can_be_deleted_after_reading(tmp_path):
    # Windows cannot delete files that are still open: reading must close them (STO-08).
    encode_value(pd.DataFrame({"A": [1]}), tmp_path, "t")
    decode_value(
        {"kind": "table", **encode_value(pd.DataFrame({"A": [1]}), tmp_path, "t")}, tmp_path
    )
    (tmp_path / "t.parquet").unlink()


# ---- scalars, texts, JSON -----------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [0, 1, -7, 2**70, 0.0, 1.5, -0.0, 1e-300, 0.1 + 0.2, float("inf"), float("-inf")],
)
def test_numbers_roundtrip(value, tmp_path):
    back = roundtrip(value, tmp_path)
    assert back == value and type(back) is type(value)
    assert math.copysign(1, back) == math.copysign(1, value)


def test_nan_and_numpy_numbers(tmp_path):
    assert math.isnan(roundtrip(float("nan"), tmp_path))
    back = roundtrip(np.float64(2.5), tmp_path)
    assert back == 2.5 and type(back) is float
    back = roundtrip(np.int64(7), tmp_path)
    assert back == 7 and type(back) is int


def test_quantities_texts_json(tmp_path):
    assert roundtrip(Quantity(25.7119, "Hz"), tmp_path) == Quantity(25.7119, "Hz")
    nan = roundtrip(Quantity(float("nan"), "N"), tmp_path)
    assert math.isnan(nan.value) and nan.unit == "N"
    assert roundtrip("Grüße ✓ \n tab\t", tmp_path) == "Grüße ✓ \n tab\t"
    assert roundtrip("", tmp_path) == ""
    for value in (None, True, False, [1, "a", None, [2.5]], {"a": {"b": [1, 2]}}):
        assert roundtrip(value, tmp_path) == value
    for bad in (object(), {1: "int key"}, (1, 2), {"x": float("nan")}, {"s": {1, 2}}):
        with pytest.raises(StoreError, match="cannot be stored"):
            encode_value(bad, tmp_path, "v")


# ---- figures ------------------------------------------------------------------------------


def make_figure():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1, 2], [0, 1, 4])
    return fig


def png_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        return image.size


def test_figures_come_back_as_images(tmp_path):
    figure = make_figure()
    back = roundtrip(figure, tmp_path)
    assert isinstance(back, StoredFigure) and hasattr(back, "savefig")
    live = io.BytesIO()
    figure.savefig(live, format="png", dpi=FIGURE_DPI, bbox_inches="tight")
    stored = io.BytesIO()
    back.savefig(stored, format="png", dpi=FIGURE_DPI, bbox_inches="tight")
    assert stored.getvalue() == live.getvalue()
    plt.close(figure)


def test_stored_figure_scales_to_the_requested_dpi(tmp_path):
    back = roundtrip(make_figure(), tmp_path)
    full = io.BytesIO()
    back.savefig(full, format="png")
    half = io.BytesIO()
    back.savefig(half, format="png", dpi=FIGURE_DPI // 2, bbox_inches="tight")
    width, height = png_size(full.getvalue())
    assert png_size(half.getvalue()) == (round(width / 2), round(height / 2))
    target = tmp_path / "out.png"
    back.savefig(target, format="png")
    assert target.read_bytes() == full.getvalue()
    with pytest.raises(ValueError, match="only be written as PNG"):
        back.savefig(io.BytesIO(), format="pdf")


# ---- entries ------------------------------------------------------------------------------


def save(store: OutputStore, block_id: str = "b1", key: str = "key1", **outputs):
    return store.save(
        block_id,
        type_id="test.block",
        type_version=1,
        cache_key=key,
        outputs=outputs or {"table": pd.DataFrame({"A": [1, 2]}), "value": 2.5},
        log=["a line", "ünï"],
        duration=0.25,
        written_files=["output/r.docx"],
    )


def test_entry_roundtrip_and_layout(tmp_path):
    store = OutputStore(tmp_path / ".apb" / "p")
    entry = save(store)
    assert (entry.block_id, entry.type_id, entry.cache_key) == ("b1", "test.block", "key1")
    assert entry.log == ("a line", "ünï") and entry.duration == 0.25
    assert entry.written_files == ("output/r.docx",)
    assert entry.usable and entry.size > 0
    assert sorted(p.name for p in (store.folder / "b1").iterdir()) == ["meta.json", "table.parquet"]
    assert (tmp_path / ".apb" / ".gitignore").read_text() == "*\n"
    assert entry.finished[:2] == "20" and "T" in entry.finished
    assert store.read_entry("b1") == entry
    outputs = StoredOutputs(entry)
    assert list(outputs) == ["table", "value"] and len(outputs) == 2
    assert outputs.get("value") == 2.5 and "table" in outputs
    pd.testing.assert_frame_equal(outputs["table"], pd.DataFrame({"A": [1, 2]}))
    with pytest.raises(KeyError):
        outputs["nope"]


def test_outputs_are_read_when_first_used(tmp_path):
    store = OutputStore(tmp_path / "s")
    outputs = StoredOutputs(save(store))
    (store.folder / "b1" / "table.parquet").write_bytes(
        b"x" * 10
    )  # damaged after the entry was read
    assert outputs["value"] == 2.5  # another port is unaffected
    with pytest.raises(
        StoreError, match=r"Stored output 'table' of block 'b1' cannot be read.*Clear"
    ):
        outputs["table"]


def test_saving_again_replaces_the_entry(tmp_path):
    store = OutputStore(tmp_path / "s")
    save(store, key="old", table=pd.DataFrame({"A": [1]}))
    save(store, key="new", value=1.0)
    entry = store.read_entry("b1")
    assert entry.cache_key == "new" and list(entry.outputs) == ["value"]
    assert [p.name for p in (store.folder / "b1").iterdir()] == ["meta.json"]  # no old table file
    assert not list(store.folder.glob(".tmp-*"))


def test_a_failed_write_keeps_the_old_entry_and_leaves_no_debris(tmp_path):
    store = OutputStore(tmp_path / "s")
    save(store, key="old")
    with pytest.raises(StoreError, match=r"output 'bad'.*cannot be stored"):
        save(store, key="new", good=1.0, bad=object())
    assert store.read_entry("b1").cache_key == "old"
    assert not list(store.folder.glob(".tmp-*"))
    assert [p.name for p in store.folder.iterdir()] == ["b1"]


def test_unsafe_block_ids_are_refused(tmp_path):
    store = OutputStore(tmp_path / "s")
    for bad in ("../x", "a/b", ".hidden", "", "a b"):
        with pytest.raises(StoreError, match="cannot be used as a file name"):
            save(store, block_id=bad)
    assert not (tmp_path / "x").exists()


def test_damaged_and_foreign_entries(tmp_path, monkeypatch):
    store = OutputStore(tmp_path / "s")
    save(store, "good")
    save(store, "no_meta")
    (store.folder / "no_meta" / "meta.json").unlink()
    save(store, "bad_json")
    (store.folder / "bad_json" / "meta.json").write_text("{ not json", encoding="utf-8")
    save(store, "no_data")
    (store.folder / "no_data" / "table.parquet").unlink()
    save(store, "short_data")
    (store.folder / "short_data" / "table.parquet").write_bytes(b"x")
    save(store, "missing_key")
    meta = json.loads((store.folder / "missing_key" / "meta.json").read_text())
    del meta["cache_key"]
    (store.folder / "missing_key" / "meta.json").write_text(json.dumps(meta))
    (store.folder / "stray_file.txt").write_text("not an entry")
    (store.folder / ".tmp-leftover").mkdir()
    entries, problems = store.entries()
    assert list(entries) == ["good"]
    assert set(problems) == {"no_meta", "bad_json", "no_data", "short_data", "missing_key"}
    assert (
        "incomplete" in problems["no_meta"] and "meta.json cannot be read" in problems["bad_json"]
    )
    assert (
        "missing or damaged" in problems["no_data"]
        and "missing or damaged" in problems["short_data"]
    )
    assert "not valid" in problems["missing_key"]


def test_other_layout_or_tool_version_is_known_but_not_usable(tmp_path, monkeypatch):
    store = OutputStore(tmp_path / "s")
    save(store, "b1")
    meta_file = store.folder / "b1" / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["store_schema"] = 99
    meta["outputs"] = {"x": {"kind": "from the future"}}
    meta_file.write_text(json.dumps(meta))
    entry = store.read_entry("b1")
    assert not entry.usable and entry.schema == 99 and entry.outputs == {}
    save(store, "b2")
    monkeypatch.setattr(store_module, "__version__", "9.9")
    save(store, "b3")
    monkeypatch.undo()
    assert not store.read_entry("b3").usable and store.read_entry("b2").usable


def test_clear_orphans_size_and_gitignore(tmp_path):
    store = OutputStore(tmp_path / ".apb" / "p")
    for block_id in ("a", "b", "c"):
        save(store, block_id)
    assert store.orphans({"a", "c"}) == ["b"]
    assert store.size() > 0
    assert store.clear(["b", "nothing"]) == ["b"]
    assert sorted(store.entries()[0]) == ["a", "c"]
    assert sorted(store.clear()) == ["a", "c"]
    assert not store.folder.exists()
    assert not (tmp_path / ".apb").exists()  # nothing but our own .gitignore was left
    assert store.clear() == [] and store.clear(["a"]) == []


def test_clear_keeps_the_hidden_folder_when_another_store_is_in_it(tmp_path):
    first, second = OutputStore(tmp_path / ".apb" / "one"), OutputStore(tmp_path / ".apb" / "two")
    save(first)
    save(second)
    first.clear()
    assert second.entries()[0].keys() == {"b1"} and (tmp_path / ".apb" / ".gitignore").exists()


def test_store_of_a_pipeline_file(tmp_path):
    assert OutputStore.for_pipeline(tmp_path / "test_case.apb.json").folder == (
        tmp_path.resolve() / ".apb" / "test_case"
    )
    assert OutputStore.for_pipeline(tmp_path / "plain.json").folder.name == "plain"
    assert OutputStore.for_pipeline(tmp_path / "noext").folder.name == "noext"


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_a_read_only_folder_gives_store_unavailable(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(StoreUnavailableError, match="cannot be written"):
            save(OutputStore(locked / ".apb" / "p"))
    finally:
        locked.chmod(0o700)


def test_old_temporary_folders_are_removed_on_the_next_write(tmp_path):
    store = OutputStore(tmp_path / "s")
    save(store)
    old, fresh = store.folder / ".tmp-old", store.folder / ".tmp-fresh"
    old.mkdir()
    fresh.mkdir()
    long_ago = 1_000_000_000
    os.utime(old, (long_ago, long_ago))
    save(store, "b2")
    assert not old.exists() and fresh.exists()  # a fresh one may belong to a running write


def test_format_size():
    assert [format_size(n) for n in (0, 999, 1000, 12_345, 3_400_000, 2_000_000_000)] == [
        "0 B",
        "999 B",
        "1.0 kB",
        "12 kB",
        "3.4 MB",
        "2.0 GB",
    ]


def test_stored_figure_keeps_its_report_width(tmp_path):
    from apb.figures import new_figure

    fig = new_figure(12, 6, dpi=100)
    fig.subplots().plot([0, 1], [1, 0])
    back = roundtrip(fig, tmp_path)
    assert back.apb_width_cm == 12.0
    (tmp_path / "b").mkdir()
    assert not hasattr(roundtrip(make_figure(), tmp_path / "b"), "apb_width_cm")
