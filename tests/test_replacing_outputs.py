"""Names and listening balance replace complete files in the meeting."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _setup  # noqa: F401
from fake_engines import made_result
from test_recording import flac

from scriba.app import jobs, recording
from scriba.core.segments import Segment
from scriba.data import atomic, session


class OutputReplacementTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.folder / "recording.mka"
        self.source.write_bytes(b"original track")
        self.result = made_result(self.folder, self.source, [Segment(0, 2, "Hello.", "Speaker 1")])

    def contents(self):
        return {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file() and not p.name.startswith(".")}

    def test_names_replace_one_set_and_can_be_cleared_after_reopening(self):
        before = self.contents()
        jobs.save_names(self.result, {"Speaker 1": "Ada"})
        jobs.save_names(self.result, {"Speaker 1": "Mara"})
        after = self.contents()
        self.assertEqual(set(before), set(after))
        self.assertEqual(after[self.source.name], before[self.source.name])
        self.assertIn(b"Mara", after[self.result.stem + ".md"])
        self.assertNotIn(b"Ada", after[self.result.stem + ".md"])
        reopened, _request = jobs.open_session(self.result.session)
        self.assertEqual(reopened.names, {"Speaker 1": "Mara"})
        self.assertEqual(reopened.segments[0].speaker, "Speaker 1")
        self.assertEqual(session.load(reopened.session).extra["previous_names"], {"Speaker 1": "Ada"})
        jobs.save_names(reopened, {})
        self.assertIn(b"Speaker 1", (self.folder / f"{reopened.stem}.md").read_bytes())

    def test_first_save_removes_the_old_separately_named_files(self):
        saved = session.load(self.result.session)
        old = self.folder / f"{self.result.stem}_names.md"
        old.write_text("old names", encoding="utf-8")
        unrelated = self.folder / "another_names.md"
        unrelated.write_text("another meeting", encoding="utf-8")
        saved.outputs.append(session.Output("md-named", str(old)))
        session.save(saved, self.result.session)
        reopened, _request = jobs.open_session(self.result.session)
        self.assertTrue(old.is_file())
        jobs.save_names(reopened, {"Speaker 1": "Ada"})
        self.assertFalse(old.exists())
        self.assertTrue(unrelated.is_file())
        self.assertNotIn("md-named", {o.kind for o in session.load(reopened.session).outputs})

    def test_failed_second_replacement_keeps_all_old_bytes(self):
        before = self.contents()
        target = self.folder / f"{self.result.stem}.txt"
        replace = atomic.replace_patiently

        def fail(source, destination):
            if Path(destination) == target:
                raise PermissionError("open file")
            replace(source, destination)

        with patch.object(atomic, "replace_patiently", side_effect=fail), self.assertRaises(OSError):
            jobs.save_names(self.result, {"Speaker 1": "Ada"})
        self.assertEqual(self.contents(), before)
        self.assertEqual(self.result.names, {})

    def test_reopening_restores_previous_bytes_after_an_interrupted_swap(self):
        class Interrupted(BaseException):
            pass

        before = self.contents()
        target = self.folder / f"{self.result.stem}.txt"
        replace = atomic.replace_patiently

        def interrupted(source, destination):
            if Path(destination) == target:
                raise Interrupted()
            replace(source, destination)

        with patch.object(atomic, "replace_patiently", side_effect=interrupted), self.assertRaises(Interrupted):
            jobs.save_names(self.result, {"Speaker 1": "Ada"})
        reopened, _request = jobs.open_session(self.result.session)
        self.assertEqual(self.contents(), before)
        self.assertEqual(reopened.names, {})

    def test_balance_replaces_one_listening_copy_and_does_not_touch_the_recording(self):
        takes = [recording.Take("m", "me", "Microphone", flac(self.folder / "m.flac", 2, .15)),
                 recording.Take("o", "others", "Output", flac(self.folder / "o.flac", 2, .2, 660))]
        source = recording.join(takes, self.folder, "call")
        result = made_result(self.folder, source, [Segment(0, 2, "Hello.", "Speaker 1")])
        automatic = recording.listening_mix(source)
        result.listen = automatic
        original = source.read_bytes()
        session_path = result.session
        jobs.save_balance(result, 0.8)
        heard = hashlib.sha256(automatic.read_bytes()).hexdigest()
        jobs.save_balance(result, -0.6)
        self.assertNotEqual(hashlib.sha256(automatic.read_bytes()).hexdigest(), heard)
        self.assertEqual(list(self.folder.glob("*.m4a")), [automatic])
        self.assertEqual(result.session, session_path)
        self.assertEqual(source.read_bytes(), original)
        reopened, _request = jobs.open_session(result.session)
        self.assertEqual(reopened.listen, automatic)
        self.assertEqual(jobs.listening_balance(reopened), -0.6)
        kept, saved = automatic.read_bytes(), session_path.read_bytes()
        replace = atomic.replace_patiently

        def fail(source, destination):
            if Path(destination) == session_path:
                raise PermissionError("open meeting file")
            replace(source, destination)

        with patch.object(atomic, "replace_patiently", side_effect=fail), self.assertRaises(OSError):
            jobs.save_balance(reopened, 0.3)
        self.assertEqual(automatic.read_bytes(), kept)
        self.assertEqual(session_path.read_bytes(), saved)
        self.assertEqual(source.read_bytes(), original)
