"""A program run as a child process: its lines read as they come, and
stopped when the user cancels.

ffmpeg and whisper-cli are driven, not linked (the plan: engines guided as
Spola guides rclone). Both say how far they are on their own streams, a
line at a time (ffmpeg's `-progress pipe:1` on stdout, whisper's
`progress = 35%` on stderr), so each stream has a reader thread that hands
the lines on as they arrive; the caller's thread waits on the cancel event
in between. Cancelling terminates the child (on Windows TerminateProcess:
neither program has work to save, and what they wrote lives in a
temporary folder the caller throws away) and raises Cancelled.

Bytes, never text mode: Windows' text mode turns LF into CRLF, and the
console's code page would garble the Italian in a file name (the lines
are decoded as UTF-8, what does not decode replaced).
"""

from __future__ import annotations

import collections
import os
import sys
import threading
from collections.abc import Callable, Sequence

from scriba.app.task import Cancelled

KEEP_LINES = 200            # the tail of stderr kept for an error's detail
GRACE = 5.0                 # seconds a terminated child has to go before it is killed

Lines = Callable[[str], None]


class Run:
    """What a finished child left: its exit status and the last lines of
    its error stream."""

    def __init__(self, returncode: int, stderr_tail: Sequence[str]) -> None:
        self.returncode = returncode
        self.stderr_tail = list(stderr_tail)

    @property
    def detail(self) -> str:
        return "\n".join(self.stderr_tail[-20:])


def _reader(stream, handlers: list[Lines | None], keep: collections.deque | None) -> None:
    for raw in iter(stream.readline, b""):
        # a line may end with CRLF (Windows' text mode), and a progress line
        # with a lone CR (written for a terminal): empty pieces are no lines
        for line in raw.replace(b"\r", b"\n").decode("utf-8", "replace").splitlines():
            if not line:
                continue
            if keep is not None:
                keep.append(line)
            for handler in handlers:
                if handler is not None:
                    handler(line)
    stream.close()


def run(argv: Sequence[str], *, env: dict | None = None, cwd: str | os.PathLike | None = None,
        on_stdout: Lines | None = None, on_stderr: Lines | None = None,
        cancelled: threading.Event | None = None, poll: float = 0.1) -> Run:
    """`argv` run to its end, its lines given to the handlers (from the
    reader threads); Cancelled, after the child is gone, when `cancelled`
    is set. OSError when the program cannot be started."""
    import subprocess
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    child = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env, cwd=cwd, creationflags=flags)
    tail: collections.deque = collections.deque(maxlen=KEEP_LINES)
    readers = [threading.Thread(target=_reader, args=(child.stdout, [on_stdout], None), daemon=True),
               threading.Thread(target=_reader, args=(child.stderr, [on_stderr], tail), daemon=True)]
    for reader in readers:
        reader.start()
    stop = cancelled if cancelled is not None else threading.Event()
    try:
        while child.poll() is None:
            if stop.wait(poll):
                raise Cancelled()
    except BaseException:
        # cancelled, or the caller's thread interrupted (Ctrl+C): the child
        # does not outlive the work it was doing
        child.terminate()
        try:
            child.wait(GRACE)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        raise
    finally:
        for reader in readers:
            reader.join(GRACE)
    return Run(child.returncode, tail)
