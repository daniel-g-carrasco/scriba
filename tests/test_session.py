#!/usr/bin/env python3
"""A meeting's own file: round trip, what this version does not know kept,
a damaged file or a newer one refused with a sentence to show."""

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup  # noqa: F401

from scriba.data import session
from scriba.data.session import Output, Session, SessionError


def sample() -> Session:
    return Session(source="C:/Riunioni/call con Jürgen.mkv", duration=3727.4, created="2026-11-11T14:05:00",
                   options={"language": "auto", "model": "large-v3-turbo", "voices": 3, "video": True,
                            "terms": ["QG-01", "CRAH"]},
                   outputs=[Output("md", "C:/Riunioni/call_transcript.md"), Output("srt", "C:/Riunioni/call.srt")],
                   names={"Speaker 1": "Zoë", "Speaker 2": "François"},
                   timings={"decode": 12.5, "transcribe": 931.0, "speakers": 240})


class SessionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "call.json"

    def test_what_is_saved_is_read_back(self):
        session.save(sample(), self.path)
        self.assertEqual(session.load(self.path), sample())
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((data["kind"], data["version"]), (session.KIND, session.FORMAT_VERSION))
        self.assertIn("Jürgen", self.path.read_text(encoding="utf-8"))

    def test_keys_this_version_does_not_know_are_kept(self):
        data = sample().to_json()
        data["future"] = {"x": 1}
        data["outputs"][0]["checksum"] = "abc"
        data["options"]["new_option"] = [1, 2]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        loaded = session.load(self.path)
        self.assertEqual(loaded.extra, {"future": {"x": 1}})
        session.save(loaded, self.dir / "again.json")
        again = json.loads((self.dir / "again.json").read_text(encoding="utf-8"))
        self.assertEqual(again["future"], {"x": 1})
        self.assertEqual(again["outputs"][0]["checksum"], "abc")
        self.assertEqual(again["options"]["new_option"], [1, 2])

    def test_a_damaged_file_is_refused_with_a_sentence_to_show(self):
        good = sample().to_json()
        cases = {
            "not json": b"{ not json",
            "not utf-8": json.dumps(good, ensure_ascii=False).encode("latin-1"),
            "a list": b"[]",
            "another app's json": json.dumps({"version": 1, "theme": "dark"}).encode(),
            "no version": json.dumps({k: v for k, v in good.items() if k != "version"}).encode(),
            "a word for a number": json.dumps({**good, "duration": "long"}).encode(),
            "names that are not texts": json.dumps({**good, "names": {"Speaker 1": 3}}).encode(),
            "an output without a path": json.dumps({**good, "outputs": [{"kind": "md"}]}).encode(),
            "outputs not a list": json.dumps({**good, "outputs": {"kind": "md"}}).encode(),
            "a switch for a time": json.dumps({**good, "timings": {"transcribe": True}}).encode(),
        }
        for name, raw in cases.items():
            with self.subTest(name):
                self.path.write_bytes(raw)
                with self.assertRaises(SessionError) as caught:
                    session.load(self.path)
                self.assertEqual(str(caught.exception), "The meeting file is damaged and cannot be opened: call.json")
                self.assertTrue(caught.exception.detail)

    def test_a_newer_format_is_refused_and_says_so(self):
        self.path.write_text(json.dumps({**sample().to_json(), "version": session.FORMAT_VERSION + 1}),
                             encoding="utf-8")
        with self.assertRaises(SessionError) as caught:
            session.load(self.path)
        self.assertIn("saved by a newer version of the app", str(caught.exception))
        self.assertIn(f"format {session.FORMAT_VERSION + 1}", str(caught.exception))

    def test_an_older_format_is_brought_up_to_this_one(self):
        def step(data):
            data["names"] = data.pop("voices")
            return data
        old = {**sample().to_json(), "version": 1}
        old["voices"] = old.pop("names")
        self.path.write_text(json.dumps({**old, "version": 1}), encoding="utf-8")
        with mock.patch.object(session, "FORMAT_VERSION", 2), mock.patch.dict(session.MIGRATIONS, {1: step}):
            self.assertEqual(session.load(self.path).names, sample().names)

    def test_a_file_that_cannot_be_read_is_refused_with_a_sentence(self):
        with self.assertRaises(SessionError) as caught:
            session.load(self.dir / "gone.json")
        self.assertIn("could not be read", str(caught.exception))

    def test_a_time_that_is_not_a_number_fails_at_the_save_and_the_old_file_stays(self):
        session.save(sample(), self.path)
        before = self.path.read_bytes()
        bad = sample()
        bad.timings["transcribe"] = math.nan
        with self.assertRaises(ValueError):
            session.save(bad, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["call.json"])

    def test_the_save_replaces_the_file_whole(self):
        session.save(sample(), self.path)
        changed = sample()
        changed.names["Speaker 3"] = "Anna"
        with mock.patch("os.replace", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                session.save(changed, self.path)
        self.assertEqual(session.load(self.path), sample())
        self.assertEqual([p.name for p in self.dir.iterdir()], ["call.json"])      # no temporary file left behind
        session.save(changed, self.path)
        self.assertEqual(session.load(self.path).names["Speaker 3"], "Anna")

    def test_a_file_held_for_an_instant_by_another_program_is_still_saved(self):
        session.save(sample(), self.path)
        changed = sample()
        changed.names["Speaker 3"] = "Anna"
        real, refusals = os.replace, [PermissionError(13, "held"), PermissionError(13, "held")]

        def held_at_first(src, dst):
            if refusals:
                raise refusals.pop()
            return real(src, dst)

        with mock.patch("os.replace", side_effect=held_at_first), mock.patch("time.sleep"):
            session.save(changed, self.path)
        self.assertEqual(session.load(self.path).names["Speaker 3"], "Anna")
        self.assertEqual([p.name for p in self.dir.iterdir()], ["call.json"])

    def test_an_empty_session_is_a_session(self):
        session.save(Session(), self.path)
        self.assertEqual(session.load(self.path), Session())


if __name__ == "__main__":
    unittest.main()
