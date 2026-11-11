"""Unexpected failures said out loud. The app has no log of its own: what
goes wrong in a thread or in the main loop must at least reach stderr (the
journal, when it runs as a service; the console, from the sources), with
its traceback, instead of vanishing with the thread or the idle source that
raised it. Never `except Exception: pass`: a failure nobody hears of is a
bug nobody can fix.

The module knows no app: setup() is told the name the lines start with."""

from __future__ import annotations

import sys
import traceback

_name = "app"


def setup(name: str) -> None:
    """At start: the name every line starts with (the app's slug)."""
    global _name
    _name = name


def complain(where: str, error: BaseException) -> None:
    print(f"{_name}: {where}: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
    traceback.print_exception(error, file=sys.stderr)
