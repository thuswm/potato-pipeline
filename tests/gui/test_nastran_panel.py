from __future__ import annotations

from apb.blocks.nastran import h5reader
from apb.gui.nastran_panel import group_results, id_summary, prune_selection
from tests.conftest import GLOBAL_H5, MODAL_H5


def test_group_results_and_search():
    infos = h5reader.list_results(MODAL_H5)
    groups = group_results(infos)
    assert list(groups) == ["ELEMENTAL", "MODAL", "NODAL", "SUMMARY"]
    assert [i.path for i in groups["SUMMARY"]] == ["SUMMARY/EIGENVALUE"]
    found = group_results(infos, "effective MASS")
    assert list(found) == ["MODAL"]
    assert {i.title for i in found["MODAL"]} == {
        "Effective mass matrix",
        "Modal effective mass",
        "Modal effective mass fractions",
        "Total effective mass fraction",
    }
    assert list(group_results(infos, "spc_force")) == ["NODAL"]  # path is searched too
    assert group_results(infos, "nothing like this") == {}


def test_prune_selection_drops_what_the_new_result_lacks():
    bush = h5reader.describe_result(GLOBAL_H5, "ELEMENT_FORCE/BUSH")
    params = {"subcases": [1, 7], "columns": ["FX", "T1"], "ids": "17-19"}
    assert prune_selection(params, bush) == {"subcases": [1], "columns": ["FX"]}

    mass = h5reader.describe_result(MODAL_H5, "MODAL/MODAL_EFFECTIVE_MASS")
    assert prune_selection(params, mass) == {"subcases": [1], "columns": ["T1"], "ids": ""}
    assert prune_selection({"subcases": [1], "columns": [], "ids": ""}, mass) == {}


def test_prune_selection_keeps_variables():
    mass = h5reader.describe_result(MODAL_H5, "MODAL/MODAL_EFFECTIVE_MASS")
    params = {"subcases": "${cases}", "ids": "${ids}", "columns": "${cols}"}
    assert prune_selection(params, mass) == {}


def test_id_summary():
    bush = h5reader.describe_result(GLOBAL_H5, "ELEMENT_FORCE/BUSH")
    assert id_summary(bush) == "EID present: 17 to 19 (3 IDs)"
    assert id_summary(h5reader.describe_result(MODAL_H5, "SUMMARY/EIGENVALUE")) == ""
