"""Running a solver as a child process (spec 09 §3.4).

The command runs through the shell of the system, in a given folder, with its output
streamed line by line. *Stop* and a time limit end the shell **and everything it started**.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

#: Lines of output kept for error messages.
TAIL_LINES = 10

_GRACE_SECONDS = 3.0


@dataclass(frozen=True)
class ProcessResult:
    """Outcome of :func:`run_command`.

    Attributes:
        exit_code: Exit code of the command, ``None`` if it was stopped.
        duration: Wall time in seconds.
        tail: The last lines of output.
        timed_out: The time limit was reached and the command was ended.
        cancelled: The user stopped the command.
    """

    exit_code: int | None
    duration: float
    tail: tuple[str, ...]
    timed_out: bool = False
    cancelled: bool = False


def kill_tree(process: subprocess.Popen[str]) -> None:
    """End a process and everything it started (process group, or ``taskkill /T``)."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + _GRACE_SECONDS
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_command(
    command: str,
    *,
    cwd: Path,
    env: Mapping[str, str],
    on_line: Callable[[str], None],
    cancel: threading.Event,
    timeout: float | None = None,
    poll: float = 0.1,
) -> ProcessResult:
    """Run a shell command and wait for it, streaming its output.

    Args:
        command: The command line, run by the shell (``sh -c`` / ``cmd /c``).
        cwd: Working folder.
        env: Environment of the command.
        on_line: Called with every line of output (stdout and stderr together), in the
            calling thread.
        cancel: When set, the command is ended and ``cancelled`` is returned.
        timeout: Seconds after which the command is ended (``None`` = no limit).
        poll: How often cancel and timeout are checked.

    Returns:
        What happened. A command that cannot be started raises :class:`OSError`.
    """
    kwargs: dict[str, object] = {
        "shell": True,
        "cwd": str(cwd),
        "env": dict(env),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    start = time.monotonic()
    process = subprocess.Popen(command, **kwargs)  # type: ignore[call-overload]
    lines: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line.rstrip("\r\n"))
        lines.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    timed_out = cancelled = False
    finished_reading = False
    while not finished_reading:
        try:
            item = lines.get(timeout=poll)
        except queue.Empty:
            item = ""
            idle = True
        else:
            idle = False
        if item is None:
            finished_reading = True
        elif not idle:
            tail.append(item)
            on_line(item)
        if finished_reading:
            break
        if cancel.is_set():
            cancelled = True
        elif timeout is not None and time.monotonic() - start > timeout:
            timed_out = True
        if cancelled or timed_out:
            kill_tree(process)
            break
    try:
        code = process.wait(timeout=_GRACE_SECONDS * 3)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        code = process.wait()
    reader.join(timeout=2)
    while True:  # lines that arrived while the command was being ended
        try:
            item = lines.get_nowait()
        except queue.Empty:
            break
        if item is not None:
            tail.append(item)
            on_line(item)
    return ProcessResult(
        exit_code=None if (cancelled or timed_out) else code,
        duration=time.monotonic() - start,
        tail=tuple(tail),
        timed_out=timed_out,
        cancelled=cancelled,
    )
