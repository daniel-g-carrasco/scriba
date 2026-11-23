import unittest

import _setup  # noqa: F401

from scriba.core.callend import CallEnd


class CallEndTest(unittest.TestCase):
    def test_interval_covers_multiple_calls_with_margins(self):
        state = CallEnd()
        state.teams("present", 130)
        state.teams("ended", 230)
        state.update(234, False, 230, False)
        self.assertEqual(state.interval(350), (120, 240))
        state.teams("minimized", 300)
        self.assertIsNone(state.interval(350))
        state.teams("ended", 400)
        state.update(404, False, 400, False)
        self.assertEqual(state.interval(410), (120, 410))

    def test_microphone_and_output_must_coincide_for_a_call_without_teams(self):
        state = CallEnd()
        state.update(50, True, None, False)
        state.update(80, False, None, True)
        self.assertIsNone(state.began)
        state.update(100, True, None, True)
        state.update(150, False, 150, True)
        state.update(153, False, 150, False)
        self.assertEqual(state.interval(220), (90, 160))

    def test_interval_needs_a_minute_outside_the_kept_audio(self):
        state = CallEnd()
        state.teams("present", 0)
        state.teams("ended", 50)
        state.update(54, False, 50, False)
        self.assertIsNone(state.interval(119))
        self.assertEqual(state.interval(120), (0, 60))

    def test_silence_in_person_never_stops_the_recording(self):
        state = CallEnd()
        state.teams("no-meeting", 0)
        state.update(600, False, 1, False)
        self.assertIsNone(state.ended)
        self.assertIsNone(state.interval(600))
        self.assertFalse(state.due(1000, 1))

    def test_needs_all_signals_and_blocks_a_late_webcam_switch(self):
        state = CallEnd()
        state.teams("ready", 95)
        state.teams("ended", 100)
        state.update(100, True, 97, False)
        self.assertIsNone(state.ended)
        self.assertTrue(state.blocks_follow)
        state.update(102, False, 97, False)
        self.assertEqual(state.ended, 97)
        state.update(108, True, 97, False)
        self.assertTrue(state.blocks_follow)
        self.assertFalse(state.due(401, 5))
        self.assertTrue(state.due(402, 5))

    def test_a_microphone_opened_right_after_the_meeting_does_not_hide_its_end(self):
        # Teams closes the meeting and opens the webcam's microphone a few
        # seconds later: the call program never lets go of a microphone.
        state = CallEnd()
        state.teams("ready", 100)
        state.teams("ended", 130)
        state.update(140, True, 110, False)
        self.assertIsNone(state.ended)
        self.assertTrue(state.blocks_follow)
        state.update(170, True, 110, False)
        self.assertEqual(state.ended, 110)
        self.assertTrue(state.prompt)

    def test_without_a_confirmed_end_an_open_microphone_keeps_the_call(self):
        state = CallEnd()
        state.teams("ready", 100)
        state.teams("no-meeting", 110)
        state.update(1000, True, 105, False)
        self.assertIsNone(state.ended)

    def test_continue_suppresses_polling_until_a_new_call(self):
        state = CallEnd()
        state.teams("ready", 5)
        state.teams("ended", 10)
        state.update(10, False, 8, False)
        state.continue_call()
        self.assertFalse(state.due(1000, 1))
        self.assertTrue(state.blocks_follow)
        state.update(1001, True, None, True)
        self.assertIsNone(state.ended)
        self.assertFalse(state.blocks_follow)
        state.teams("ready", 1002)
        state.teams("ended", 1009)
        state.update(1011, False, 1008, False)
        self.assertTrue(state.prompt)
        self.assertFalse(state.due(1100, 0))
