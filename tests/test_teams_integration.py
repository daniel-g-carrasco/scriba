"""Recording tags and session compatibility with invented named intervals."""
import json
import threading
import unittest
from pathlib import Path

import _setup  # noqa: F401
from scriba.app import recording
from scriba.app.config import Config
from scriba.app.engines import ffmpeg
from scriba.core import callend, spans
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
        recorder.teams_names = True
        at = [0.0]
        recorder.elapsed = lambda: at[0]
        recorder.teams_reading(Reading("ready", ("Ada Example",), 2))
        at[0] = 4.0
        recorder.teams_reading(Reading("minimized"))
        at[0] = 5.0
        recorder.teams_reading(Reading("ready", ("Ada Example",), 2))
        at[0], recorder._state = 7.0, "paused"
        recorder.teams_reading(Reading("ready", ("Ada Example",), 2))
        self.assertEqual(recorder._named.finish(9), (("Ada Example", 0, 4), ("Ada Example", 5, 7)))

    def test_old_settings_and_sessions_take_optional_defaults(self):
        self.assertTrue(Config.from_json({}).teams_names)
        self.assertEqual(Config.from_json({}).call_stop_minutes, 5)
        self.assertEqual(session.from_json({"kind": session.KIND}).named_spans, ())

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
