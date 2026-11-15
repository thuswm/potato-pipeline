"""A fake MSC Nastran for the tests (spec 09 §3.11): behaves like the program, needs no licence.

Called as ``nastran job.bdf [key=value ...]`` through the launcher the tests create. It writes
``job.f06``, ``job.h5``, ``job.f04`` and ``job.log`` next to the deck and prints progress
lines. The environment variable ``FAKE_NASTRAN`` selects the behaviour; ``FAKE_NASTRAN_LOG``
names a file to which every call is appended (``<cwd>|<arguments>``); ``FAKE_NASTRAN_H5`` names
the ``.h5`` file it delivers (default: the ``global_loads.h5`` of the example files):

    ok (default)  clean run
    warning       a warning (9058) in the .f06
    fatal         a fatal message and exit code 1
    fatal_exit0   a fatal message but exit code 0
    no_end        the .f06 has no END OF JOB (exit code 0)
    no_h5         no .h5 file
    exit3         clean .f06 but exit code 3
    hang          prints a line, then waits a minute
    background    returns at once without writing anything
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

TEMPLATE_H5 = Path(
    os.environ.get("FAKE_NASTRAN_H5")
    or Path(__file__).resolve().parents[1] / "examples" / "fem" / "global_loads.h5"
)

HEADER = """1                 Welcome to MSC Nastran 2023.3 (fake)
 *** USER INFORMATION MESSAGE 4109 (OUTPX2)
 *** USER INFORMATION MESSAGE 3 (crdb::nastran::Processor::writeInput)
     WRITE DATA BLOCK I4 PVT
"""
WARNING = """ *** USER WARNING MESSAGE 9058 (SUBDMAP SESTATIC)
     THE SOLUTION FOR THE RESIDUAL STRUCTURE AND THE APPLIED LOADS FOR THE CURRENT SUBCASE ARE ZERO.
"""
FATAL = """ *** USER FATAL MESSAGE 2 (FAKE)
     THIS IS A FATAL MESSAGE OF THE FAKE NASTRAN.
"""
END = "1                                        * * * END OF JOB * * *\n"


def main(argv: list[str]) -> int:
    mode = os.environ.get("FAKE_NASTRAN", "ok")
    log = os.environ.get("FAKE_NASTRAN_LOG")
    if log:
        with Path(log).open("a", encoding="utf-8") as handle:
            handle.write(f"{Path.cwd()}|{' '.join(argv)}\n")
    decks = [a for a in argv if "=" not in a]
    if not decks:
        print("fake nastran: no input file")
        return 2
    deck = Path(decks[0])
    job = deck.stem
    print(f"fake nastran: job {job}", flush=True)
    if mode == "background":
        return 0
    if mode == "hang":
        print("fake nastran: working ...", flush=True)
        time.sleep(60)
        return 0
    time.sleep(0.05)
    text = HEADER
    if mode == "warning":
        text += WARNING
    if mode in ("fatal", "fatal_exit0"):
        text += FATAL
    if mode != "no_end":
        text += END
    Path(f"{job}.f06").write_text(text, encoding="utf-8")
    Path(f"{job}.f04").write_text("f04\n", encoding="utf-8")
    Path(f"{job}.log").write_text("log\n", encoding="utf-8")
    if mode != "no_h5":
        shutil.copyfile(TEMPLATE_H5, f"{job}.h5")
    print("fake nastran: finished", flush=True)
    return {"fatal": 1, "exit3": 3}.get(mode, 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
