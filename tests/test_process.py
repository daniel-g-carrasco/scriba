# SPDX-License-Identifier: GPL-3.0-or-later
"""A program run as a child process (app/process.py): its lines read as
they come from both streams, its exit status, and the child stopped when
the work is cancelled. The child is this Python itself."""

import os
import sys
import tempfile
import threading
import time
import unittest

import _setup

process = _setup.shared("app.process")
task = _setup.shared("app.task")


class ProcessTest(unittest.TestCase):
    def test_the_lines_arrive_from_both_streams_and_the_exit_status_is_kept(self):
        out, err = [], []
        code = "import sys; print('one'); print('two'); sys.stderr.write('uno\\r\\ndue\\n'); sys.exit(3)"
        done = process.run([sys.executable, "-c", code], on_stdout=out.append, on_stderr=err.append)
        self.assertEqual((out, err, done.returncode), (["one", "two"], ["uno", "due"], 3))
        self.assertEqual(done.stderr_tail, ["uno", "due"])

    def test_a_progress_line_written_for_a_terminal_is_a_line_and_the_text_is_utf8(self):
        out = []
        code = ("import sys; sys.stdout.buffer.write('10%\\r20%\\rcaffè \U0001f600\\n'.encode('utf-8')); "
                "sys.stdout.buffer.write(b'bad \\xff byte\\n')")
        process.run([sys.executable, "-c", code], on_stdout=out.append)
        self.assertEqual(out, ["10%", "20%", "caffè \U0001f600", "bad � byte"])

    def test_the_detail_is_the_last_lines_of_the_error_stream(self):
        code = f"import sys\nfor n in range({process.KEEP_LINES + 50}): print('line', n, file=sys.stderr)"
        done = process.run([sys.executable, "-c", code])
        self.assertEqual(len(done.stderr_tail), process.KEEP_LINES)
        self.assertEqual(done.stderr_tail[-1], f"line {process.KEEP_LINES + 49}")
        self.assertEqual(done.detail.splitlines(), done.stderr_tail[-20:])

    def test_the_folder_and_the_environment_are_the_child_s(self):
        out = []
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, MY_APP_TEST_VALUE="given")
            code = "import os; print(os.getcwd()); print(os.environ['MY_APP_TEST_VALUE'])"
            process.run([sys.executable, "-c", code], env=env, cwd=folder, on_stdout=out.append)
            self.assertEqual(os.path.normcase(os.path.realpath(out[0])),
                             os.path.normcase(os.path.realpath(folder)))
        self.assertEqual(out[1], "given")

    def test_a_cancelled_child_is_stopped_at_once(self):
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        began = time.monotonic()
        with self.assertRaises(task.Cancelled):
            process.run([sys.executable, "-c", "import time; time.sleep(60)"], cancelled=stop)
        self.assertLess(time.monotonic() - began, 10)

    def test_a_program_that_is_not_there_is_an_os_error(self):
        with self.assertRaises(OSError):
            process.run([os.path.join(tempfile.gettempdir(), "no-such-program-here.exe")])


if __name__ == "__main__":
    unittest.main()
