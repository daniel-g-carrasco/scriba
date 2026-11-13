"""The levels while recording (core/levels.py), the loudness afterwards
(core/loudness.py) and a recording's names and room (core/recording.py)."""

import datetime
import math
import random
import unittest

import _setup  # noqa: F401

from scriba.core import levels, loudness, recording as rec


def feed(watch, key, seconds, rms, peak=None, start=0.0, step=0.1):
    """`rms` a number, or a function of the time."""
    t = start
    while t < start + seconds:
        r = rms(t) if callable(rms) else rms
        watch.feed(key, t, r, peak if peak is not None else r + 10)
        t += step
    return t


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.watch = levels.Watch()
        self.watch.track("mic", True)
        self.watch.track("out", False)

    def kinds(self, now):
        return [(a.kind, a.track) for a in self.watch.warnings(now)]

    def test_a_voice_heard_from_across_the_room_is_said_to_be_too_quiet(self):
        # the live call: speech at -44 to -53 dB over a room at -62
        pick = random.Random(3)
        voice = lambda t: -46 + pick.uniform(-4, 4) if int(t) % 3 else -62     # noqa: E731
        now = feed(self.watch, "mic", 12, voice)
        feed(self.watch, "out", 30, -20, start=0)
        self.assertEqual(self.kinds(now), [])           # not yet: faint speech for QUIET_FOR seconds first
        now = feed(self.watch, "mic", 10, voice, start=now)
        self.assertIn((levels.QUIET, "mic"), self.kinds(now))
        quiet = next(a for a in self.watch.warnings(now) if a.kind == levels.QUIET)
        self.assertLess(quiet.level, levels.QUIET_BELOW)
        self.assertGreaterEqual(quiet.level, levels.SPEECH_FROM)

    def test_a_quiet_room_where_nobody_speaks_is_not_a_quiet_voice(self):
        now = feed(self.watch, "mic", 12, -62)
        feed(self.watch, "out", 12, -20)
        self.assertEqual(self.kinds(now), [])

    def test_a_user_silent_in_the_call_is_never_told_the_microphone_hears_little(self):
        # the call of 13/11/2026: the right microphone at -73 dB, with the dips of the call's
        # own noise suppression to digital silence and a breath now and then
        pick = random.Random(5)
        now = feed(self.watch, "mic", 120,
                   lambda t: -700.0 if int(t * 10) % 7 == 0 else -73 + pick.uniform(-6, 6) if int(t) % 9 else -64)
        feed(self.watch, "out", 120, -20)
        self.assertNotIn(levels.QUIET, [k for k, _t in self.kinds(now)])

    def test_one_faint_sound_in_the_silence_is_not_faint_speech(self):
        now = feed(self.watch, "mic", 20, -73)
        now = feed(self.watch, "mic", 1.5, -48, start=now)          # a cough, a chair
        for _ in range(4):
            now = feed(self.watch, "mic", 5, -73, start=now)
            self.assertNotIn(levels.QUIET, [k for k, _t in self.kinds(now)])

    def test_faint_speech_that_stops_is_not_warned_about_any_more(self):
        voice = lambda t: -48 if int(t) % 3 else -66        # noqa: E731
        now = feed(self.watch, "mic", 30, voice)
        self.assertIn(levels.QUIET, [k for k, _t in self.kinds(now)])
        now = feed(self.watch, "mic", 15, -73, start=now)          # the user stopped speaking
        self.assertNotIn(levels.QUIET, [k for k, _t in self.kinds(now)])

    def test_a_voice_at_a_good_level_says_nothing_and_ends_the_warning(self):
        now = feed(self.watch, "mic", 12, lambda t: -46 if int(t) % 3 else -62)
        now = feed(self.watch, "mic", 12, lambda t: -22 if int(t) % 3 else -62, start=now)
        feed(self.watch, "out", now, -20)
        self.assertNotIn(levels.QUIET, [k for k, _t in self.kinds(now)])

    def test_a_microphone_that_reaches_zero_is_said_to_clip_for_a_while(self):
        feed(self.watch, "mic", 2, -12, peak=-0.0)
        feed(self.watch, "out", 2, -20)
        self.assertIn((levels.CLIPPING, "mic"), self.kinds(2.0))
        self.assertNotIn((levels.CLIPPING, "mic"), self.kinds(2.0 + levels.KEEP_WARNING + 1))

    def test_outputs_silent_for_fifteen_seconds_are_said_with_how_long(self):
        feed(self.watch, "mic", 20, -30)
        feed(self.watch, "out", 20, -700, peak=-350)
        self.assertEqual(self.kinds(10.0), [])
        silent = [a for a in self.watch.warnings(20.0) if a.kind == levels.SILENT]
        self.assertEqual(len(silent), 1)
        self.assertGreaterEqual(silent[0].seconds, levels.SILENT_AFTER)
        feed(self.watch, "out", 1, -30, start=20)       # someone speaks in the call
        self.assertEqual([a for a in self.watch.warnings(21.0) if a.kind == levels.SILENT], [])

    def test_a_track_that_ended_is_forgotten_and_silence_is_not_kept(self):
        feed(self.watch, "mic", 12, lambda t: -46 if int(t) % 3 else -62)
        self.watch.forget("mic")
        self.assertNotIn("mic", [a.track for a in self.watch.warnings(12)])
        self.assertTrue(levels.kept(-30.0))
        self.assertFalse(levels.kept(-350.0))
        self.assertFalse(levels.kept(-math.inf))

    def test_the_meter_reads_from_minus_ninety_to_zero(self):
        self.assertEqual(levels.fraction(-700.0), 0.0)
        self.assertEqual(levels.fraction(0.0), 1.0)
        self.assertEqual(levels.fraction(3.0), 1.0)
        self.assertAlmostEqual(levels.fraction(-45.0), 0.5)
        self.assertEqual(levels.fraction(float("nan")), 0.0)


class LoudnessTest(unittest.TestCase):
    # ebur128's summary as ffmpeg 8 prints it (the live call's microphone, 11/11/2026)
    SUMMARY = """[Parsed_ebur128_0 @ 000002c6] Summary:

  Integrated loudness:
    I:         -32.5 LUFS
    Threshold: -44.7 LUFS

  Loudness range:
    LRA:        19.6 LU
    Threshold: -55.0 LUFS
    LRA low:   -49.4 LUFS
    LRA high:  -29.8 LUFS

  True peak:
    Peak:        0.4 dBFS
size=N/A time=00:08:41.36 bitrate=N/A speed= 407x"""

    def test_the_measure_is_read_as_ffmpeg_prints_it(self):
        measure = loudness.parse_ebur128("t: 521.2 M: -60.8 I: -32.5 LUFS\n" + self.SUMMARY)
        self.assertEqual(measure, loudness.Loudness(-32.5, 0.4, 19.6, -44.7))
        silent = loudness.parse_ebur128(self.SUMMARY.replace("-32.5", "-70.0").replace("0.4 dBFS", "-inf dBFS"))
        self.assertTrue(loudness.silent(silent))
        self.assertEqual(silent.true_peak, -math.inf)
        with self.assertRaises(ValueError):
            loudness.parse_ebur128("no measure here")

    def test_the_gain_reaches_the_target_unless_the_peaks_would_cross_the_ceiling(self):
        # the hard file of 11/11/2026: -46 LUFS, peaks at -23.5 dBTP: +22.5 dB is allowed
        self.assertAlmostEqual(loudness.linear_gain(loudness.Loudness(-46.12, -23.45, 10.9, -56.7)), 22.45, places=2)
        # a slammed door: the ceiling decides
        self.assertAlmostEqual(loudness.linear_gain(loudness.Loudness(-40.0, -3.0, 10.0, -50.0)), 2.0)
        # too loud: turned down
        self.assertAlmostEqual(loudness.linear_gain(loudness.Loudness(-14.0, -1.5, 5.0, -24.0)), -9.0)
        # silence: nothing to raise, and never more than MOST
        self.assertEqual(loudness.linear_gain(loudness.Loudness(-math.inf, -math.inf, 0, -70)), 0.0)
        self.assertEqual(loudness.linear_gain(loudness.Loudness(-69.0, -60.0, 0, -79)), loudness.MOST)

    def test_the_mix_raises_a_track_that_once_touched_zero_and_leaves_the_peak_to_the_limiter(self):
        # the live call's microphone: -32.5 LUFS with a peak at +0.4 dBTP
        measure = loudness.Loudness(-32.5, 0.4, 19.6, -44.7)
        self.assertAlmostEqual(loudness.mix_gain(measure), 9.5)
        self.assertLess(loudness.linear_gain(measure), 0)          # held under the ceiling: turned down
        self.assertEqual(loudness.mix_gain(loudness.Loudness(-70.0, -math.inf, 0, -70)), 0.0)
        self.assertEqual(loudness.mix_gain(loudness.Loudness(-69.0, -60.0, 0, -79)), loudness.MOST)


class NamesAndRoomTest(unittest.TestCase):
    def test_the_name_says_when_and_sorts_in_order(self):
        when = datetime.datetime(2026, 11, 11, 10, 54, 8)
        self.assertEqual(rec.stem(when, "registrazione"), "2026-11-11 10-54-08 registrazione")

    def test_a_device_s_name_is_made_safe_for_a_file_s(self):
        self.assertEqual(rec.safe("Cuffie (Headset BT)"), "Cuffie (Headset BT)")
        self.assertEqual(rec.safe('Mic: "USB" <2>/3'), "Mic USB 2 3")
        self.assertEqual(rec.safe("Gruppo microfoni (Tecnologia Intel® Smart Sound per microfoni digitali)"),
                         "Gruppo microfoni (Tecnologia Intel® Smar")
        self.assertEqual(rec.safe("..."), "audio")

    def test_the_room_left_on_the_disk(self):
        hour = rec.BYTES_PER_SECOND * 3600
        self.assertAlmostEqual(rec.seconds_left(rec.MARGIN + hour * 3, 3), 3600)
        self.assertTrue(rec.may_start(rec.MARGIN + hour, 3))
        self.assertFalse(rec.may_start(rec.MARGIN + rec.BYTES_PER_SECOND * 60 * 5, 1))      # five minutes
        self.assertEqual(rec.room(rec.MARGIN + hour, 1), "ok")
        self.assertEqual(rec.room(rec.MARGIN + rec.BYTES_PER_SECOND * 60 * 20, 1), "low")
        self.assertEqual(rec.room(rec.MARGIN + rec.BYTES_PER_SECOND * 30, 1), "stop")
        self.assertEqual(rec.room(0, 2), "stop")
        self.assertEqual(rec.seconds_left(0, 0), float("inf"))


if __name__ == "__main__":
    unittest.main()
