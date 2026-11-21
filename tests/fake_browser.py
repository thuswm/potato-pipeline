"""A fake Chrome/Edge for the app window tests (spec 06 §3.6).

Appends its command line to ``$FAKE_BROWSER_LOG`` (one JSON list per line), then stays
"open" for ``$FAKE_BROWSER_SECONDS`` seconds (default: one hour) and exits with code 0 -
as if the user closed the window. When terminated (File > Quit) it appends
``["terminated"]`` first. ``FAKE_BROWSER_SECONDS=0`` behaves like a browser that hands the
window to an already running browser process and returns at once.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from types import FrameType


def _log(entry: list[str]) -> None:
    with Path(os.environ["FAKE_BROWSER_LOG"]).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _terminated(signum: int, frame: FrameType | None) -> None:
    _log(["terminated"])
    sys.exit(0)


def main() -> int:
    """Log the arguments and wait."""
    signal.signal(signal.SIGTERM, _terminated)
    _log(sys.argv[1:])
    time.sleep(float(os.environ.get("FAKE_BROWSER_SECONDS", "3600")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
