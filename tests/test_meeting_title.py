import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _setup  # noqa: F401
from scriba.app import jobs, meeting_folders
from scriba.core.segments import Segment
from scriba.data import markdown, session


class MeetingTitleTest(unittest.TestCase):
    def test_a_locked_folder_keeps_every_file_and_reopening_retries_the_move(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        folder = root / "2026-11-19 10-17-48"
        folder.mkdir()
        source = folder / f"{folder.name} recording.mka"
        source.write_bytes(b"original recording")
        subtitles = folder / f"{source.stem}_transcript.srt"
        subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
        saved = folder / f"{subtitles.stem}.json"
        session.save(session.Session(source=str(source), outputs=[session.Output("srt", str(subtitles))]), saved)
        meeting_folders.MeetingFolder(folder.name).save(folder)
        before = {p.name: p.read_bytes() for p in (source, subtitles, saved)}
        result, _request = jobs.open_session(saved)
        with patch.object(meeting_folders, "rename_directory", side_effect=PermissionError("open file")):
            self.assertEqual(jobs.save_meeting_title(result, "Plan"), "Plan")
        self.assertEqual(result.folder, folder)
        self.assertIn("Scriba will try again", result.notes[-1])
        self.assertEqual({name: (folder / name).read_bytes() for name in before}, before)
        opened, request = jobs.open_session(saved)
        target = root / f"{folder.name} Plan"
        self.assertEqual(opened.folder, target)
        self.assertEqual(opened.meeting_title, "Plan")
        self.assertEqual(request.source, target / source.name)
        self.assertEqual(Path(opened.outputs[0].path), target / subtitles.name)
        self.assertEqual(opened.segments[0].text, "Hello")
        self.assertEqual({name: (target / name).read_bytes() for name in before}, before)
        self.assertFalse(folder.exists())
        jobs.save_meeting_title(opened, "PLAN")
        self.assertEqual(opened.folder.name, f"{folder.name} PLAN")
        self.assertEqual([p.name for p in root.iterdir()], [opened.folder.name])
        self.assertEqual(jobs.open_session(opened.session)[0].meeting_title, "PLAN")

    def test_teams_title_is_preferred_and_safe_for_an_export(self):
        lines = [Segment(0, 8, "Dobbiamo approvare le modifiche al piano di montaggio.")]
        title = jobs.suggest_meeting_title("Piano / montaggio", lines, ())
        self.assertEqual(title, "Piano / montaggio")
        self.assertEqual(jobs.titled_stem(title, Path("2026-11-19 10-17-48 registrazione.mka"),
                                         datetime.datetime(2026, 11, 20)), "2026-11-19 10-17-48 Piano montaggio")

    def test_terms_must_be_heard_and_doubtful_lines_do_not_supply_titles(self):
        lines = [Segment(0, 5, "Riassunto inventato dall'audio", doubts=("low",)),
                 Segment(6, 15, "Parliamo del piano consegna per la settimana prossima.")]
        self.assertEqual(jobs.suggest_meeting_title("", lines, ("inventato", "piano consegna")), "piano consegna")
        self.assertEqual(jobs.suggest_meeting_title("", lines[:1], ()), "")

    def test_editing_a_title_updates_the_session_and_the_transcript_keeps_its_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "2026-11-19 10-17-48 registrazione.mka"
            meeting = folder / "before.json"
            session.save(session.Session(source=str(source)), meeting)
            existing = folder / "before.md"
            existing.write_text("previous transcript", encoding="utf-8")
            result = jobs.Result([], meeting, [Segment(0, 5, "hello", "Speaker 1")], 1, 5, "cpu", {},
                                 source=source, folder=folder, stem="before",
                                 header=markdown.Header(source=str(source), duration=5,
                                                        when=datetime.datetime(2026, 11, 20)))
            self.assertEqual(jobs.save_meeting_title(result, " Piano / portico "), "Piano / portico")
            saved = jobs.save_names(result, {"Speaker 1": "Ada"})
            self.assertIn("Ada", existing.read_text(encoding="utf-8"))
            self.assertEqual(session.load(meeting).extra["meeting_title"], "Piano / portico")
            self.assertTrue(all(Path(o.path).stem == "before" for o in saved.outputs))
