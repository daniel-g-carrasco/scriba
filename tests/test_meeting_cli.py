"""The assistant commands work with saved meetings without opening GTK."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _setup  # noqa: F401
from fake_engines import made_result

from scriba import cli
from scriba.app import config, jobs, meeting_folders, services
from scriba.app.paths import Paths
from scriba.core.segments import Segment
from scriba.data import session


class MeetingCommandsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.library = self.root / "library"
        self.library.mkdir()
        self.paths = Paths(self.root / "config", self.root / "state", self.root / "data")
        self.enterContext(patch.object(services, "app_paths", return_value=self.paths))
        self.alpha = self.meeting("2026-11-12 10-00-00", "Project Alpha", nested=False)
        self.beta = self.meeting("2026-11-14 13-00-00", "Project Beta", nested=True)
        config.save(config.Config(recordings_folder=str(self.library)), self.paths.config_file)

    def meeting(self, date, title, *, nested):
        folder = self.library / (date if nested else f"{date} {title}")
        folder.mkdir()
        source = folder / f"{date} recording.mka"
        source.write_bytes(b"original")
        output = folder / title if nested else folder
        made = made_result(output, source, [Segment(2, 8, "An estimate is ready.", "Speaker 1"),
                                            Segment(9, 12, "Review the estimate.", "Speaker 2")], duration=30)
        stored = session.load(made.session)
        stored.extra["meeting_title"] = title
        session.save(stored, made.session)
        return made

    def command(self, *args, code=0, as_json=True):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            actual = cli.main([*args, *(("--json",) if as_json else ())])
        self.assertEqual(actual, code, error.getvalue())
        return (json.loads(output.getvalue()) if as_json and code == 0 else output.getvalue()), error.getvalue()

    def test_list_dates_and_old_layout_have_a_stable_json_schema(self):
        expected = json.loads((Path(__file__).parent / "fixtures" / "meeting_cli_list.json").read_text(encoding="utf-8"))
        expected["meetings"][0]["path"] = jobs.full_path(self.alpha.folder)
        expected["meetings"][1]["path"] = jobs.full_path(self.beta.source.parent)
        result, _ = self.command("list")
        self.assertEqual(result, expected)
        filtered, _ = self.command("list", "--since", "2026-11-13", "--folder", str(self.library))
        self.assertEqual(filtered["meetings"], expected["meetings"][1:])

    def test_show_resolves_title_date_and_paths_and_says_which_are_ambiguous(self):
        for query in ("Alpha", "2026-11-12", str(self.alpha.session), str(self.alpha.source), str(self.alpha.folder)):
            shown, _ = self.command("show", query)
            self.assertEqual(shown["meeting"]["title"], "Project Alpha")
            self.assertEqual(shown["lines"][0]["text"], "An estimate is ready.")
        for kind in ("md", "txt", "srt"):
            shown, _ = self.command("show", "Alpha", "--format", kind, as_json=False)
            self.assertIn("An estimate is ready.", shown)
        _, error = self.command("show", "Project", code=1)
        self.assertIn("Project Alpha", error)
        self.assertIn("Project Beta", error)

    def test_search_and_voices_keep_the_line_times_and_labels(self):
        matches, _ = self.command("search", "ESTIMATE")
        self.assertEqual([line["start"] for line in matches["matches"]], [2, 9, 2, 9])
        limited, _ = self.command("search", "estimate", "--meeting", "Beta")
        self.assertTrue(all(line["meeting"]["title"] == "Project Beta" for line in limited["matches"]))
        found, _ = self.command("voices", "Alpha")
        self.assertEqual([voice["label"] for voice in found["voices"]], ["Speaker 1", "Speaker 2"])
        self.assertEqual(found["voices"][0]["minutes"], 0.1)
        self.assertEqual(found["voices"][0]["first"], 2)

    def test_names_updates_every_format_and_saves_my_default_name(self):
        original = self.alpha.source.read_bytes()
        result, _ = self.command("names", "Alpha", "--set", "Parlante 1=Ada", "--set", "Speaker 2=Kai",
                                 "--me", "Parlante 1")
        self.assertEqual(result["names"], {"Speaker 1": "Ada", "Speaker 2": "Kai"})
        self.assertEqual(config.load(self.paths.config_file).my_name, "Ada")
        self.assertEqual(self.alpha.source.read_bytes(), original)
        for output in self.alpha.outputs:
            self.assertIn("Ada", Path(output.path).read_text(encoding="utf-8"))
        cleared, _ = self.command("names", "Alpha", "--clear", "Speaker 2")
        self.assertEqual(cleared["names"], {"Speaker 1": "Ada"})

    def test_reading_does_not_retry_an_earlier_folder_rename(self):
        state = meeting_folders.MeetingFolder("2026-11-12 10-00-00", "New title", "2026-11-12 10-00-00 New title")
        state.save(self.alpha.folder)
        before = {p.relative_to(self.library): p.read_bytes() for p in self.library.rglob("*") if p.is_file()}
        shown, _ = self.command("show", str(self.alpha.session))
        self.assertEqual(shown["meeting"]["title"], "New title")
        self.command("list")
        self.assertEqual({p.relative_to(self.library): p.read_bytes() for p in self.library.rglob("*")
                          if p.is_file()}, before)
