"""Recently opened pipelines of this computer (spec 06 §3.8, GUI-38)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apb.gui import recent


@pytest.fixture
def listfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("APB_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    return tmp_path / "cfg" / "recent.json"


def test_list_sits_next_to_the_config_file(listfile: Path):
    assert recent.recent_path() == listfile
    assert recent.recent_files() == []


def test_newest_first_no_duplicates_at_most_ten(listfile: Path, tmp_path: Path):
    files = [tmp_path / f"p{i}.apb.json" for i in range(12)]
    for f in files:
        recent.add_recent(f)
    assert recent.recent_files() == [f.resolve() for f in reversed(files[2:])]
    recent.add_recent(files[5])  # already listed: moves to the top
    listed = recent.recent_files()
    assert listed[0] == files[5].resolve() and len(listed) == 10
    assert listed.count(files[5].resolve()) == 1
    recent.clear_recent()
    assert recent.recent_files() == []
    assert listfile.is_file()


def test_broken_file_counts_as_empty_and_is_rewritten(listfile: Path, tmp_path: Path):
    listfile.parent.mkdir(parents=True)
    for text in ("not json", '["a"]', '{"files": "x"}', '{"files": [1, ""]}'):
        listfile.write_text(text, encoding="utf-8")
        assert recent.recent_files() == []
    recent.add_recent(tmp_path / "a.apb.json")
    assert recent.recent_files() == [(tmp_path / "a.apb.json").resolve()]


def test_a_list_that_cannot_be_written_is_ignored(tmp_path: Path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("APB_CONFIG", str(blocker / "config.toml"))  # folder is a file
    assert recent.add_recent(tmp_path / "a.apb.json") == [(tmp_path / "a.apb.json").resolve()]
