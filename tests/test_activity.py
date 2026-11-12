"""A worker remains owned until its completion callback releases its ticket."""

import unittest

import _setup  # noqa: F401

from scriba.core.activity import Activities


class ActivitiesTest(unittest.TestCase):
    def test_tickets_are_independent_and_finish_only_once(self):
        activities = Activities()
        seen = []
        unsubscribe = activities.subscribe(lambda: seen.append(activities.active_count))
        first, second = activities.acquire(), activities.acquire()
        self.assertTrue(activities.finish(first))
        self.assertFalse(activities.finish(first))
        self.assertEqual(activities.active_count, 1)
        self.assertTrue(activities.finish(second))
        self.assertEqual(seen, [1, 2, 1, 0])
        unsubscribe()
        unsubscribe()
        activities.finish(activities.acquire())
        self.assertEqual(seen, [1, 2, 1, 0])

    def test_observer_can_start_cleanup_before_the_first_work_finishes(self):
        activities = Activities()
        cleanup = []

        def changed():
            if activities.active_count == 1 and not cleanup:
                cleanup.append(None)
                cleanup[0] = activities.acquire()

        activities.subscribe(changed)
        first = activities.acquire()
        activities.finish(first)
        self.assertEqual(activities.active_count, 1)
        activities.finish(cleanup[0])
        self.assertEqual(activities.active_count, 0)

    def test_broken_observer_does_not_lose_tickets_or_other_notifications(self):
        activities = Activities()
        seen = []

        def failed():
            raise ValueError("observer failed")

        activities.subscribe(failed)
        activities.subscribe(lambda: seen.append(activities.active_count))
        with self.assertLogs("scriba.core.activity", level="ERROR"):
            activities.finish(activities.acquire())
        self.assertEqual(seen, [1, 0])
