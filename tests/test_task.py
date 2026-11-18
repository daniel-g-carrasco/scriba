# SPDX-License-Identifier: GPL-3.0-or-later
"""How a piece of work without a window is stopped and says it failed
(app/task.py)."""

import threading
import unittest

import _setup

task = _setup.shared("app.task")


class TaskTest(unittest.TestCase):
    def test_a_failure_has_a_sentence_for_the_user_and_a_detail_for_the_log(self):
        failure = task.Failure("The file could not be read.", "ffmpeg: Invalid data found")
        self.assertEqual(str(failure), "The file could not be read.")
        self.assertEqual(failure.detail, "ffmpeg: Invalid data found")
        self.assertEqual(task.Failure("No detail.").detail, "")

    def test_a_cancellation_is_not_a_failure(self):
        self.assertFalse(issubclass(task.Cancelled, task.Failure))
        self.assertFalse(issubclass(task.Failure, task.Cancelled))

    def test_check_stops_only_when_asked(self):
        stop = threading.Event()
        task.check(None)
        task.check(stop)
        stop.set()
        with self.assertRaises(task.Cancelled):
            task.check(stop)

    def test_the_progress_is_told_when_someone_listens(self):
        heard = []
        task.tell(lambda done, total: heard.append((done, total)), 3, 10)
        task.tell(None, 4, 10)
        self.assertEqual(heard, [(3, 10)])


if __name__ == "__main__":
    unittest.main()
