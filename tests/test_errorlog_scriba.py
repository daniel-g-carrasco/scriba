#!/usr/bin/env python3
"""Scriba's own side of the errors file: a traceback of a GLib callback
(the way a button's callback fails) reaches errors.log and the loop goes
on, and the report of a problem, made from Scriba's own settings, names
the file. The rest is the shared module's test (test_errorlog.py, a copy
of the model's, which does not load the toolkit)."""

import re
import sys
import tempfile
import unittest
from pathlib import Path

import _setup  # noqa: F401

from scriba.app import diagnostics, errorlog
from scriba.app.config import Config
from scriba.app.paths import Paths

STAMP = re.compile(r"^--- \d{4}-\d\d-\d\d \d\d:\d\d:\d\d ---$", re.M)


class ErrorLogTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = errorlog.log_file(Path(tmp.name) / "state")
        before = sys.stderr

        def put_back():
            if isinstance(sys.stderr, errorlog.ErrorLog):
                sys.stderr.close()
            sys.stderr = before
        self.addCleanup(put_back)

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def test_an_exception_in_a_glib_callback_lands_in_the_file_and_the_loop_goes_on(self):
        try:
            import gi
            from gi.repository import GLib
        except ImportError:
            self.skipTest("PyGObject")
        del gi
        sys.stderr = None
        errorlog.install(self.path)
        after = []

        def clicked():
            raise TypeError("'tuple' object is not callable")
        context = GLib.MainContext.default()
        GLib.idle_add(clicked)
        GLib.idle_add(lambda: after.append(True) and False)
        for _ in range(20):
            context.iteration(False)
            if after:
                break
        self.assertEqual([True], after)
        text = self.read()
        self.assertRegex(text, STAMP)
        self.assertIn("TypeError: 'tuple' object is not callable", text)
        self.assertIn("in clicked", text)

    def test_the_report_of_a_problem_names_the_file(self):
        state = self.path.parent
        paths = Paths(state.parent / "config", state, state.parent / "data")
        line = [x for x in diagnostics.summary("Scriba", "0", Config(), paths, "GTK").splitlines()
                if x.startswith("errors:")]
        self.assertEqual([f"errors: {self.path} (none)"], line)


if __name__ == "__main__":
    unittest.main()
