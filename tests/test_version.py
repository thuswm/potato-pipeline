"""Version reporting (spec 12)."""

import tomllib
from pathlib import Path

from typer.testing import CliRunner

from apb import __version__
from apb.cli import app

ROOT = Path(__file__).resolve().parents[1]


def test_version_comes_from_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert __version__ == project["version"]


def test_version_flag_and_command():
    runner = CliRunner()
    for args in (["--version"], ["version"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert result.output.strip() == __version__


def test_help_still_lists_commands():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Potato Pipeline" in result.output
    assert "gui" in result.output


def test_python_dash_m_apb():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "apb", "--version"], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == __version__
