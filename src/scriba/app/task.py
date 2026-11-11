"""What every long piece of work without a window shares: how it is
stopped, and how it says it failed.

A download, a decoding, a transcription run in a thread the window (or the
command line) starts. They are stopped with a threading.Event the caller
sets, looked at between one chunk of work and the next, never by killing
the thread: what was half done is then cleaned up by the code that did it.
A failure is a Failure, whose text is a sentence for the user (in their
language, through i18n) and whose `detail` is what went wrong in the
engine's own words, for the log: the window shows the first, the command
line prints the first and, when asked, the second.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

# done, total (bytes, samples, percent: the unit is the caller's); total 0 = unknown
Progress = Callable[[float, float], None]


class Failure(Exception):
    """The work could not be done. str() is the sentence to show; `detail`
    is the engine's own words, for the log."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class Cancelled(Exception):
    """The user asked to stop. Not a failure: nothing is said but that."""


def check(cancelled: threading.Event | None) -> None:
    """Cancelled if the user asked to stop."""
    if cancelled is not None and cancelled.is_set():
        raise Cancelled()


def tell(progress: Progress | None, done: float, total: float) -> None:
    if progress is not None:
        progress(done, total)
