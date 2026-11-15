from __future__ import annotations

import pytest

from apb.gui.filedialog import make_folder


def test_make_folder(tmp_path):
    new = make_folder(tmp_path, "  results ")
    assert new == tmp_path / "results"
    assert new.is_dir()


@pytest.mark.parametrize("bad", ["", "  ", ".", "..", "a/b", "a\\b", "C:x"])
def test_make_folder_refuses_bad_names(tmp_path, bad):
    with pytest.raises(ValueError, match="folder name"):
        make_folder(tmp_path, bad)
    assert list(tmp_path.iterdir()) == []


def test_make_folder_refuses_existing_and_unwritable(tmp_path):
    (tmp_path / "data").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        make_folder(tmp_path, "data")
    with pytest.raises(ValueError, match="Cannot create"):
        make_folder(tmp_path / "missing", "x")
