"""Saved recording information is distinct from incomplete work."""

import unittest

import _setup  # noqa: F401

from scriba.app import jobs
from scriba import TEXTDOMAIN, i18n
from pathlib import Path


class ResultNotesTest(unittest.TestCase):
    def test_saved_italian_notes_remain_information_when_the_interface_is_english(self):
        i18n.setup(TEXTDOMAIN, Path(__file__).resolve().parents[1], "en")
        translate = i18n.translation_for("it").gettext
        self.assertTrue(jobs.recording_information(translate(jobs.MUTE_INFORMATION).format(time="05:05")))
        self.assertTrue(jobs.recording_information(
            translate(jobs.ECHO_INFORMATION).format(share="9 %", time="05:05")))
        repair = translate(jobs.TIMING_INFORMATION)
        self.assertEqual(repair, "Alla riapertura sono state corrette le battute con orari incoerenti.")
        self.assertTrue(jobs.recording_information(repair))
        self.assertEqual(translate("inconsistent times"), "orari incoerenti")

    def test_recording_information_is_recognised_without_hiding_failures(self):
        for note in (
            "The microphone heard the computer's sound (loudspeakers) in 9 % of the recording "
            "(05:05): there it was taken out of your track before transcribing, so the others' words "
            "come from the computer's track only.",
            "05:05 of speech on your microphone fell where you were muted in the call: it was "
            "not transcribed, and its sound stays in the recording.",
        ):
            self.assertTrue(jobs.recording_information(note), note)
        for note in (
            "A file of terms was left out: missing.txt is not there.",
            "The computer's sound heard by the microphone could not be taken out: failed.",
            "An unknown future warning",
        ):
            self.assertFalse(jobs.recording_information(note), note)
