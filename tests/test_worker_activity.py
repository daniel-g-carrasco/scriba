"""Worker completion includes its main-loop callback and any cleanup it starts."""

import queue
import unittest
from functools import partial
from unittest import mock

import _setup  # noqa: F401

try:
    from scriba.gui import ui
except (ImportError, ValueError) as error:
    raise unittest.SkipTest(str(error)) from error


class WorkerActivityTest(unittest.TestCase):
    def setUp(self):
        self.ready = queue.Queue()
        self.patch = mock.patch.object(ui.GLib, "idle_add", side_effect=lambda fn, *args: self.ready.put(partial(fn, *args)))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def callback(self):
        return self.ready.get(timeout=3)

    def test_truthy_completion_does_not_repeat_the_idle_callback(self):
        seen = []
        ui.run_in_thread(lambda: 42, lambda value, error: seen.append(value) or True)
        callback = self.callback()
        self.assertFalse(callback())
        self.assertFalse(callback())
        self.assertEqual(seen, [42])

    def test_activity_remains_until_completion_and_its_cleanup_return(self):
        from scriba.core.activity import Activities
        activities = Activities()
        observed = []
        activities.subscribe(lambda: observed.append(activities.active_count))

        def done(value, error):
            self.assertEqual(activities.active_count, 1)
            ui.run_in_thread(lambda: value + 1, lambda *_: None, activities=activities)

        ui.run_in_thread(lambda: 42, done, activities=activities)
        callback = self.callback()
        self.assertEqual(activities.active_count, 1)
        callback()
        self.assertEqual(activities.active_count, 1)
        self.callback()()
        self.assertEqual(observed, [1, 2, 1, 0])

    def test_raising_or_obsolete_completion_still_releases_activity(self):
        from scriba.core.activity import Activities
        activities = Activities()

        def failed(*_args):
            raise ValueError("completion failed")

        ui.run_in_thread(lambda: None, failed, activities=activities)
        callback = self.callback()
        with self.assertRaisesRegex(ValueError, "completion failed"):
            callback()
        self.assertEqual(activities.active_count, 0)
        self.assertFalse(callback())
        ui.run_in_thread(lambda: None, lambda *_: None, activities=activities)
        self.callback()()
        self.assertEqual(activities.active_count, 0)

    def test_worker_error_is_delivered_once_before_activity_ends(self):
        from scriba.core.activity import Activities
        activities = Activities()
        seen = []

        def failed():
            raise OSError("work failed")

        ui.run_in_thread(failed, lambda result, error: seen.append(
            (result, str(error), activities.active_count)), activities=activities)
        self.callback()()
        self.assertEqual(seen, [(None, "work failed", 1)])
        self.assertEqual(activities.active_count, 0)

    def test_worker_exit_is_reported_instead_of_stranding_shutdown(self):
        from scriba.core.activity import Activities
        activities = Activities()
        seen = []

        def exited():
            raise SystemExit("worker exited")

        ui.run_in_thread(exited, lambda _result, error: seen.append(error), activities=activities)
        self.callback()()
        self.assertIsInstance(seen[0], SystemExit)
        self.assertEqual(activities.active_count, 0)

    def test_thread_start_failure_does_not_leave_activity_pending(self):
        from scriba.core.activity import Activities
        activities = Activities()
        with mock.patch.object(ui.threading.Thread, "start", side_effect=RuntimeError("cannot start")):
            with self.assertRaisesRegex(RuntimeError, "cannot start"):
                ui.run_in_thread(lambda: None, lambda *_: None, activities=activities)
        self.assertEqual(activities.active_count, 0)
        self.assertTrue(self.ready.empty())

    def test_explicit_scope_does_not_change_the_process_registry(self):
        from scriba.core.activity import Activities
        activities = Activities()
        baseline = ui.activities.active_count
        ui.run_in_thread(lambda: None, lambda *_: None, activities=activities)
        self.assertEqual(activities.active_count, 1)
        self.assertEqual(ui.activities.active_count, baseline)
        self.callback()()
