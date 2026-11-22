"""Allow ``python -m apb …`` when the ``apb`` command is not on the PATH."""

from apb.cli import app

if __name__ == "__main__":
    app(prog_name="apb")
