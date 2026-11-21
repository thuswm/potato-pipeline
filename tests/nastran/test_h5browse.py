"""Tests of the HDF5 viewer library (spec 10): tree, details, raw and result views, export."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import pytest

from apb.blocks.nastran import h5browse as b
from apb.blocks.nastran import h5reader as r
from apb.units import UnitSystem
from tests.conftest import GLOBAL_H5, JOINT_H5, MODAL_H5, PLATE_H5

# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------


def test_paths():
    assert b.result_path("/NASTRAN/RESULT/NODAL/DISPLACEMENT") == "NODAL/DISPLACEMENT"
    assert b.result_path("NASTRAN/RESULT/NODAL/DISPLACEMENT/") == "NODAL/DISPLACEMENT"
    assert b.result_path("/NASTRAN/RESULT/DOMAINS") == ""
    assert b.result_path("/INDEX/NASTRAN/RESULT/NODAL/EIGENVECTOR") == ""
    assert b.copy_path("/NASTRAN/RESULT/NODAL/EIGENVECTOR") == "NODAL/EIGENVECTOR"
    assert b.copy_path("NASTRAN/INPUT/NODE/GRID") == "/NASTRAN/INPUT/NODE/GRID"


# --------------------------------------------------------------------------------------
# Tree and details (H5V-03, H5V-04, H5V-09)
# --------------------------------------------------------------------------------------


def test_tree_lists_whole_file():
    nodes = {n.path: n for n in b.list_tree(MODAL_H5)}
    for path in ("/INDEX", "/NASTRAN/INPUT/NODE/GRID", "/NASTRAN/RESULT/DOMAINS"):
        assert path in nodes
    grid = nodes["/NASTRAN/INPUT/NODE/GRID"]
    assert (grid.kind, grid.rows, grid.columns, grid.parent) == (
        "dataset",
        3008,
        9,
        "/NASTRAN/INPUT/NODE",
    )
    assert grid.size_label == "3008 x 9"
    assert nodes["/NASTRAN/RESULT/NODAL/EIGENVECTOR"].rows == 30080
    assert nodes["/NASTRAN"].kind == "group"
    assert nodes["/NASTRAN"].attributes == 7
    assert nodes["/NASTRAN"].size_label == "2 items"
    assert list(nodes) == sorted(nodes)


def test_details_of_group_and_dataset():
    group = b.node_details(MODAL_H5, "/NASTRAN")
    assert group.attributes["VERSION"] == "msc20233"
    assert group.attributes["SOL"] == "103"
    assert group.columns == ()

    ds = b.node_details(GLOBAL_H5, "/NASTRAN/RESULT/ELEMENTAL/ELEMENT_FORCE/BUSH", UnitSystem.SI)
    assert ds.result == "ELEMENTAL/ELEMENT_FORCE/BUSH"
    assert ds.title
    cols = {c.name: c for c in ds.columns}
    assert cols["FX"].unit == "N"
    assert cols["FX"].kind == "force"
    assert cols["EID"].unit == ""

    root = b.node_details(MODAL_H5, "/")
    assert root.node.children == 2


def test_details_array_fields():
    cols = {c.name: c for c in b.node_details(MODAL_H5, "/NASTRAN/INPUT/ELEMENT/CQUAD4").columns}
    assert cols["G"].shape == (4,)
    assert cols["EID"].dtype.startswith("int")


def test_details_missing_item():
    with pytest.raises(r.NastranH5Error, match="not found"):
        b.node_details(MODAL_H5, "/NO/SUCH")


def test_subcase_summary():
    assert [(s.subcase, s.modes) for s in b.subcase_summary(MODAL_H5)] == [(1, 10)]
    assert b.subcase_summary(MODAL_H5)[0].label == "1 (10 modes)"
    plate = {s.subcase: s for s in b.subcase_summary(PLATE_H5)}
    assert sorted(plate) == [1, 2, 3, 10]
    assert plate[2].label == "2"
    assert plate[10].modes == 6


def test_errors_for_bad_files(tmp_path: Path):
    with pytest.raises(r.NastranH5Error, match="not found"):
        b.list_tree(tmp_path / "missing.h5")
    bad = tmp_path / "bad.h5"
    bad.write_text("not hdf5")
    with pytest.raises(r.NastranH5Error, match="Cannot open"):
        b.list_tree(bad)


# --------------------------------------------------------------------------------------
# Raw view (H5V-05, H5V-06)
# --------------------------------------------------------------------------------------


def test_raw_rows_are_paged():
    df = b.read_rows(MODAL_H5, "/NASTRAN/INPUT/NODE/GRID", start=100, count=50)
    assert len(df) == 50
    assert df.index[0] == 100
    with h5py.File(MODAL_H5, "r") as h5:
        expected = h5["/NASTRAN/INPUT/NODE/GRID"]["ID"][100:150]
    assert df["ID"].tolist() == expected.tolist()
    tail = b.read_rows(MODAL_H5, "/NASTRAN/INPUT/NODE/GRID", start=3000, count=50)
    assert len(tail) == 8


def test_raw_flattens_array_fields():
    df = b.read_rows(MODAL_H5, "/NASTRAN/INPUT/ELEMENT/CQUAD4", 0, 2)
    assert {"G[0]", "G[1]", "G[2]", "G[3]"} <= set(df.columns)


def test_raw_frame_shapes():
    nested = np.zeros(2, dtype=[("A", "<i4"), ("B", [("X", "<f8"), ("Y", "<f8")])])
    assert list(b.raw_frame(nested).columns) == ["A", "B.X", "B.Y"]
    matrix = np.arange(12).reshape(3, 2, 2)
    assert list(b.raw_frame(matrix).columns) == ["[0,0]", "[0,1]", "[1,0]", "[1,1]"]
    assert list(b.raw_frame(np.arange(3)).columns) == ["value"]
    assert len(b.raw_frame(np.array(5.0))) == 1
    text = np.array([b"ab  ", b"c"], dtype="S4")
    assert b.raw_frame(text)["value"].tolist() == ["ab", "c"]


def test_raw_view_of_byte_strings():
    df = b.read_rows(MODAL_H5, "/NASTRAN/INPUT/PARAMETER/PVT/CHAR", 0, 3)
    assert df["NAME"].map(type).eq(str).all()


# --------------------------------------------------------------------------------------
# Result view (H5V-07, H5V-08)
# --------------------------------------------------------------------------------------


def test_result_view_matches_block_output():
    sel = b.select_rows(GLOBAL_H5, "ELEMENT_FORCE/BUSH")
    page = b.read_result_page(sel, 0, 100, UnitSystem.SI)
    block = r.read_result(GLOBAL_H5, "ELEMENT_FORCE/BUSH", unit_system=UnitSystem.SI)
    pd.testing.assert_frame_equal(page, block.reset_index(drop=True), check_like=False)
    assert page.attrs["units"]["FZ"] == "N"


def test_result_view_mode_and_id_filter():
    sel = b.select_rows(MODAL_H5, "NODAL/EIGENVECTOR", modes=[2, 3], ids="1-5")
    assert sel.total == 10
    page = b.read_page(sel, 0, 100)
    assert sorted(page["MODE"].unique()) == [2, 3]
    assert page["GRID"].between(1, 5).all()
    full = r.read_result(MODAL_H5, "NODAL/EIGENVECTOR", ids="1-5")
    expected = full[full["MODE"].isin([2, 3])].reset_index(drop=True)
    pd.testing.assert_frame_equal(page, expected)


def test_result_view_pages_together_give_everything():
    sel = b.select_rows(MODAL_H5, "NODAL/EIGENVECTOR", ids="10-20")
    pages = [b.read_page(sel, s, 25) for s in range(0, sel.total, 25)]
    joined = pd.concat(pages, ignore_index=True)
    pd.testing.assert_frame_equal(joined, r.read_result(MODAL_H5, "NODAL/EIGENVECTOR", ids="10-20"))
    assert {tuple(p.columns) for p in pages} == {tuple(joined.columns)}


def test_own_mode_column_is_filtered_on_the_field():
    sel = b.select_rows(MODAL_H5, "MODAL/MODAL_EFFECTIVE_MASS", modes=[2, 4])
    assert sel.total == 2
    assert b.read_page(sel)["MODE"].tolist() == [2, 4]


def test_subcase_filter_on_multi_subcase_file():
    sel = b.select_rows(PLATE_H5, "NODAL/DISPLACEMENT", subcases=[2, 3])
    page = b.read_page(sel, 0, 1000)
    assert sorted(page["SUBCASE"].unique()) == [2, 3]
    expected = r.read_result(PLATE_H5, "NODAL/DISPLACEMENT", subcases=[2, 3])
    pd.testing.assert_frame_equal(page, expected)


def test_raw_view_of_result_keeps_filter():
    sel = b.select_rows(JOINT_H5, "/NASTRAN/RESULT/ELEMENTAL/ELEMENT_FORCE/BUSH", subcases=[4])
    raw = b.read_page(sel, 0, 100, result_view=False)
    assert len(raw) == 4
    assert "DOMAIN_ID" in raw.columns
    assert raw.index.tolist() == [12, 13, 14, 15]


def test_index_and_domain_field_give_same_rows(tmp_path: Path):
    copy = tmp_path / "noindex.h5"
    shutil.copy(PLATE_H5, copy)
    with h5py.File(copy, "a") as h5:
        del h5["INDEX"]
    for kwargs in ({"subcases": [2]}, {"subcases": [1, 3], "ids": "3-20"}):
        with_index = b.select_rows(PLATE_H5, "ELEMENTAL/STRESS/QUAD4", **kwargs)
        without = b.select_rows(copy, "ELEMENTAL/STRESS/QUAD4", **kwargs)
        assert with_index.rows is not None and without.rows is not None
        assert with_index.rows.tolist() == without.rows.tolist()


def test_non_result_dataset_is_not_filtered():
    sel = b.select_rows(MODAL_H5, "/NASTRAN/INPUT/NODE/GRID", subcases=[5])
    assert (sel.rows, sel.total, sel.result) == (None, 3008, "")


def test_id_filter_needs_id_field():
    with pytest.raises(r.NastranH5Error, match="no ID field"):
        b.select_rows(MODAL_H5, "SUMMARY/EIGENVALUE", ids="1")
    with pytest.raises(r.NastranH5Error, match="Invalid ID"):
        b.select_rows(MODAL_H5, "NODAL/EIGENVECTOR", ids="a-b")


def test_page_beyond_end_is_empty():
    sel = b.select_rows(GLOBAL_H5, "ELEMENT_FORCE/BUSH")
    assert len(b.read_page(sel, 100, 10)) == 0


def test_file_is_not_kept_open(tmp_path: Path):
    copy = tmp_path / "copy.h5"
    shutil.copy(GLOBAL_H5, copy)
    sel = b.select_rows(copy, "ELEMENT_FORCE/BUSH")
    b.read_page(sel)
    b.list_tree(copy)
    with h5py.File(copy, "a") as h5:  # would fail if the viewer still held the file
        h5.attrs["TOUCHED"] = 1


# --------------------------------------------------------------------------------------
# CSV export (H5V-11)
# --------------------------------------------------------------------------------------


def test_export_result_view_with_units(tmp_path: Path):
    target = tmp_path / "out.csv"
    sel = b.select_rows(MODAL_H5, "NODAL/EIGENVECTOR", ids="1-3")
    n = b.export_csv(sel, target, unit_system=UnitSystem.SI)
    assert n == 30
    with target.open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0][:3] == ["GRID", "SUBCASE", "MODE"]
    assert len(rows) == 31


def test_export_in_chunks_is_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(b, "CHUNK_ROWS", 7)
    target = tmp_path / "raw.csv"
    sel = b.select_rows(MODAL_H5, "/NASTRAN/INPUT/NODE/GRID")
    pages = list(b.iter_pages(sel, result_view=False, chunk=1000))
    assert sum(len(p) for p in pages) == 3008
    assert b.export_csv(sel, target, result_view=False) == 3008
    back = pd.read_csv(target)
    with h5py.File(MODAL_H5, "r") as h5:
        assert back["ID"].tolist() == h5["/NASTRAN/INPUT/NODE/GRID"]["ID"].tolist()


def test_export_full_precision(tmp_path: Path):
    target = tmp_path / "eig.csv"
    sel = b.select_rows(MODAL_H5, "SUMMARY/EIGENVALUE")
    b.export_csv(sel, target)
    back = pd.read_csv(target)
    expected = r.read_result(MODAL_H5, "SUMMARY/EIGENVALUE")
    # Stored as float32: the text must give back exactly the same float32 values.
    assert (back["EIGENVALUE"].astype(np.float32) == expected["EIGENVALUE"]).all()
