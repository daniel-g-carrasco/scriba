"""Recording tags and session compatibility with invented named intervals."""
import json
import threading
import unittest
from pathlib import Path

import _setup  # noqa: F401
from scriba.app import recording
from scriba.app.config import Config
from scriba.app.engines import ffmpeg
from scriba.core import callend, spans, teams
from scriba.data import session


class PersistenceTest(unittest.TestCase):
    def test_recording_clock_and_unavailable_samples_bound_the_intervals(self):
        from test_recorder import gstreamer
        from scriba.app.teams import Reading
        from scriba.core.spans import Builder
        recorder = gstreamer().Recorder.__new__(gstreamer().Recorder)
        recorder._lock = threading.Lock()
        recorder._named, recorder._state, recorder._off = Builder(), "recording", set()
        recorder._call_end = callend.CallEnd()
        recorder._teams_people = 0
        recorder._teams_summary = teams.ReadingSummary()
        recorder.teams_names = True
        at = [0.0]
        recorder.elapsed = lambda: at[0]
        recorder.teams_reading(Reading("ready", ("Ada Example",), 8))
        at[0] = 4.0
        recorder.teams_reading(Reading("minimized"))
        at[0] = 5.0
        recorder.teams_reading(Reading("ready", ("Ada Example",), 2))
        at[0], recorder._state = 7.0, "paused"
        recorder.teams_reading(Reading("ready", ("Ada Example",), 2))
        self.assertEqual(recorder._named.finish(9), (("Ada Example", 0, 4), ("Ada Example", 5, 7)))
        self.assertEqual(recorder._teams_people, 8)

    def test_old_settings_and_sessions_take_optional_defaults(self):
        self.assertTrue(Config.from_json({}).teams_names)
        self.assertEqual(Config.from_json({}).call_stop_minutes, 5)
        self.assertEqual(session.from_json({"kind": session.KIND}).named_spans, ())

    def test_minutes_count_only_the_requested_fields_and_reset_after_an_active_border(self):
        summary = teams.ReadingSummary()
        summary.observe(0, "ready", 8, False, "screen", False, (.12, .18, 0, .11))
        summary.observe(0.5, "ready", 6, True, "print-window", True, (.08, .94, .81, .95))
        summary.observe(1, "minimized", 0, False, "", None)
        summary.observe(2, "unavailable", 3, False, "", True)
        summary.observe(3, "error", 0, False, "", None)
        summary.observe(4, "no-meeting", 0, False, "", None)
        self.assertEqual(summary.minutes, [{
            "minute": 0, "states": {"ready": 2, "minimized": 1, "unavailable": 1, "error": 1, "no-meeting": 1},
            "max_tiles": 8, "with_border": 1, "sources": {"screen": 1, "print-window": 1},
            "covered": 2, "uncovered": 1,
            "border_side_max": [.12, .94, .81, .95],
        }])
        for at in range(60, 360):
            summary.observe(at, "ready", 8, False, "screen", False)
        self.assertEqual(summary.minutes[-1]["minute"], 5)
        self.assertEqual(summary.without_border, 4)
        summary.observe(360, "ready", 8, False, "screen", False)
        self.assertEqual(summary.without_border, 5)
        summary.observe(361, "ready", 8, True, "screen", False)
        self.assertEqual(summary.without_border, 0)

    def test_named_intervals_travel_from_recording_tag_to_session(self):
        named = (("Ada Example", 2.0, 8.0),)
        take = recording.Take("out", "others", "Headset", Path("invented.flac"), named_spans=named)
        args = recording.join_arguments("ffmpeg", [take], "invented.mka")
        self.assertIn(ffmpeg.NAMED_TAG + "=" + spans.write_spans(named), args)
        media = ffmpeg.parse_probe(json.dumps({"streams": [{"codec_type": "audio", "tags": {
            ffmpeg.ROLE_TAG: "others", ffmpeg.NAMED_TAG: spans.write_spans(named)}}]}))
        self.assertEqual(media.tracks[0].named_spans, named)
        saved = session.Session(named_spans=named)
        self.assertEqual(session.from_json(json.loads(session.dumps(saved))).named_spans, named)
