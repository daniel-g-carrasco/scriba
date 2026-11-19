import datetime
import tempfile
import unittest
from pathlib import Path

import _setup  # noqa: F401
from scriba.app import jobs
from scriba.core.segments import Segment
from scriba.data import markdown, session


class MeetingTitleTest(unittest.TestCase):
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

    def test_editing_a_title_updates_the_session_and_names_new_exports_only(self):
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
            self.assertEqual(existing.read_text(encoding="utf-8"), "previous transcript")
            self.assertEqual(session.load(meeting).extra["meeting_title"], "Piano / portico")
            self.assertTrue(all(Path(o.path).name.startswith("2026-11-19 10-17-48 Piano portico")
                                for o in saved.outputs))
