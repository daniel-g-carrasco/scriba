"""The windowed app's errors kept in a file (app/errorlog.py): what stderr
receives lands in errors.log with the time, a console still sees the same
text, the null device of the Windows build counts as nowhere, the file
stays under its limit, and a traceback of a GLib callback (the way a
button's callback fails) reaches it."""

import io
import os
import re
import sys
import tempfile
import threading
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

    def stream(self, **options) -> errorlog.ErrorLog:
        stream = errorlog.ErrorLog(self.path, **options)
        self.addCleanup(stream.close)
        return stream

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def test_a_run_without_errors_leaves_no_file(self):
        errorlog.install(self.path)
        self.assertFalse(self.path.exists())

    def test_what_stderr_receives_lands_in_the_file_with_the_time(self):
        null = open(os.devnull, "w")            # as the Windows build without a console
        self.addCleanup(null.close)
        sys.stderr = null
        stream = errorlog.install(self.path)
        self.assertIsNone(stream.echo)
        print("scriba: something: ValueError: wrong", file=sys.stderr)
        text = self.read()
        self.assertRegex(text, STAMP)
        self.assertIn("scriba: something: ValueError: wrong", text)

    def test_a_console_sees_the_same_text_and_no_more(self):
        console = io.StringIO()
        sys.stderr = console
        errorlog.install(self.path)
        print("first line", file=sys.stderr)
        self.assertEqual("first line\n", console.getvalue())
        self.assertIn("first line", self.read())

    def test_a_burst_gets_the_time_once_and_a_later_one_again(self):
        now = [1000.0]
        stream = self.stream(clock=lambda: now[0])
        stream.write("Traceback (most recent call last):\n")
        stream.write("  line\n")
        now[0] += errorlog.QUIET + 1
        stream.write("again\n")
        self.assertEqual(2, len(STAMP.findall(self.read())))

    def test_the_file_stays_under_its_limit_and_keeps_the_one_before(self):
        stream = self.stream(max_bytes=1000)
        for n in range(200):
            stream.write(f"line {n:03d} of an error that repeats\n")
        older = self.path.with_name("errors.log.1")
        self.assertTrue(older.is_file())
        self.assertLess(self.path.stat().st_size, 1100)
        self.assertLess(older.stat().st_size, 1100)
        self.assertIn("line 199", self.read())

    def test_a_console_s_colours_stay_out_of_the_file(self):
        stream = self.stream()
        stream.write("\x1b[1;35mValueError\x1b[0m: wrong\n")
        self.assertIn("ValueError: wrong", self.read())
        self.assertNotIn("\x1b", self.read())

    def test_an_exception_in_a_thread_lands_in_the_file(self):
        sys.stderr = None
        errorlog.install(self.path)

        def fails():
            raise RuntimeError("a thread that broke")
        thread = threading.Thread(target=fails)
        thread.start()
        thread.join()
        text = self.read()
        self.assertRegex(text, STAMP)
        self.assertIn("RuntimeError: a thread that broke", text)

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
