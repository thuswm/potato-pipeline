"""Unit tests of the HDF5 viewer page helpers and its entry points (spec 10)."""

from __future__ import annotations

import urllib.parse
from pathlib import Path

import pytest
from typer.testing import CliRunner

from apb import cli
from apb.blocks.nastran import h5browse
from apb.gui import h5viewer
from apb.gui.nastran_panel import Hdf5ResultsPanel
from apb.units import UnitSystem
from tests.conftest import GLOBAL_H5, MODAL_H5, PLATE_H5

runner = CliRunner()


def _query(url: str) -> dict[str, str]:
    path, _, query = url.partition("?")
    assert path == h5viewer.ROUTE
    return dict(urllib.parse.parse_qsl(query))


def test_viewer_url_carries_file_and_filters(tmp_path: Path):
    url = h5viewer.viewer_url(
        MODAL_H5, dataset="NODAL/EIGENVECTOR", subcases=[1, 2], ids="1-5", units="SI"
    )
    assert _query(url) == {
        "file": str(MODAL_H5.resolve()),
        "dataset": "NODAL/EIGENVECTOR",
        "subcases": "1,2",
        "ids": "1-5",
        "units": "SI",
    }
    assert _query(h5viewer.viewer_url(tmp_path / "a b.h5")) == {
        "file": str((tmp_path / "a b.h5").resolve())
    }


def test_parse_int_list():
    assert h5viewer.parse_int_list("1, 2,3") == [1, 2, 3]
    assert h5viewer.parse_int_list("") == []
    assert h5viewer.parse_int_list("1;x;4") == [1, 4]


def test_size_label():
    assert h5viewer.size_label(512) == "512 B"
    assert h5viewer.size_label(2_400_000) == "2.4 MB"
    assert h5viewer.size_label(3_000_000_000) == "3.0 GB"


def test_build_tree_nests_groups():
    tree = h5viewer.build_tree(h5browse.list_tree(MODAL_H5))
    assert [n["label"] for n in tree] == ["INDEX", "NASTRAN"]
    nastran = tree[1]
    assert [c["label"] for c in nastran["children"]] == ["INPUT", "RESULT"]
    result = nastran["children"][1]
    domains = next(c for c in result["children"] if c["label"] == "DOMAINS")
    assert domains["id"] == "/NASTRAN/RESULT/DOMAINS"
    assert domains["size"] == "22 x 16"
    assert "children" not in domains


def test_file_summary():
    text = h5viewer.file_summary(MODAL_H5)
    assert "MSC Nastran 2023.3 · SOL 103 · modal.bdf" in text
    assert "Subcases: 1 (10 modes)" in text
    assert "Subcases: 1, 2, 3, 10 (6 modes)" in h5viewer.file_summary(PLATE_H5)


def test_default_units():
    assert h5viewer.default_units(MODAL_H5, "SI_mm") == "SI_mm"
    assert h5viewer.default_units(MODAL_H5, "") == ""  # real files carry no unit system
    assert h5viewer.default_units(PLATE_H5, "") == "SI_mm"  # synthetic files do
    assert h5viewer.default_units(PLATE_H5, "nonsense") == "SI_mm"


def _panel(params: dict[str, object]) -> Hdf5ResultsPanel:
    return Hdf5ResultsPanel(
        params,
        on_change=lambda name, value: params.__setitem__(name, value),
        pick_file=None,
        resolve=lambda value: value,
        base_dir=GLOBAL_H5.parent,
        default_units=UnitSystem.SI_MM,
        show_peek=lambda table, error: None,
        log=lambda text: None,
    )


def test_panel_viewer_url_has_result_and_filters():
    params = {
        "file": "global_loads.h5",
        "result": "ELEMENT_FORCE/BUSH",
        "subcases": [1],
        "ids": "17-18",
    }
    query = _query(_panel(params).viewer_url(GLOBAL_H5))
    assert query["dataset"] == "ELEMENT_FORCE/BUSH"
    assert query["subcases"] == "1"
    assert query["ids"] == "17-18"
    assert query["units"] == "SI_mm"  # the pipeline default


def test_panel_viewer_url_without_valid_filters():
    params = {"file": "global_loads.h5", "result": "", "subcases": "${cases}", "ids": ""}
    query = _query(_panel(params).viewer_url(GLOBAL_H5))
    assert "dataset" not in query and "subcases" not in query


# --------------------------------------------------------------------------------------
# apb h5view
# --------------------------------------------------------------------------------------


def test_h5view_refuses_missing_files(tmp_path: Path):
    result = runner.invoke(cli.app, ["h5view", str(tmp_path / "nope.h5")])
    assert result.exit_code == 2
    assert "File not found" in result.output


def test_h5view_refuses_unknown_units():
    result = runner.invoke(cli.app, ["h5view", str(MODAL_H5), "--units", "imperial"])
    assert result.exit_code == 2
    assert "Unknown unit system" in result.output


def test_h5view_starts_server_with_one_tab_per_file(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_server_answers", lambda port: False)
    monkeypatch.setattr(
        "apb.gui.app.run_gui",
        lambda pipeline, **kwargs: calls.append({"pipeline": pipeline, **kwargs}),
    )
    result = runner.invoke(cli.app, ["h5view", str(MODAL_H5), str(GLOBAL_H5), "--port", "9001"])
    assert result.exit_code == 0, result.output
    (call,) = calls
    assert call["pipeline"] is None and call["port"] == 9001 and call["show"] is True
    paths = call["open_paths"]
    assert isinstance(paths, list) and len(paths) == 2
    assert [_query(p)["file"] for p in paths] == [str(MODAL_H5.resolve()), str(GLOBAL_H5.resolve())]


def test_h5view_reuses_a_running_server(monkeypatch: pytest.MonkeyPatch):
    opened: list[str] = []
    monkeypatch.setattr(cli, "_server_answers", lambda port: True)
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    result = runner.invoke(cli.app, ["h5view", str(MODAL_H5), "--units", "SI"])
    assert result.exit_code == 0, result.output
    assert "already running" in result.output
    (url,) = opened
    assert url.startswith("http://127.0.0.1:8765/h5?")
    assert "units=SI" in url


def test_server_answers_is_false_without_server():
    assert cli._server_answers(1) is False
