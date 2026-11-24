import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import _setup  # noqa: F401

from scriba.app import finishing, recording, recovery
from scriba.app.engines import ffmpeg
from test_recording import flac


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.folder = self.root / "2000-01-02 03-04-05"
        self.folder.mkdir()
        self.stem = self.folder.name + " recording"
        self.path = self.folder / f"{self.stem}_microphone.flac"

    def test_live_folder_is_hidden_and_its_metadata_survives(self):
        self.path.write_bytes(b"fLaC")
        journal = recovery.Journal(self.folder, self.stem)
        take = recording.Take("me-1", "me", "Mic", self.path, began=1, ended=3, muted=((2, None),))
        with journal.lock():
            journal.save("recording", [take], 3)
            self.assertEqual(recovery.pending(self.root), [])
        item, = recovery.pending(self.root)
        self.assertEqual(item.takes()[0].muted, ((2, None),))
        self.assertEqual(item.takes()[0].began, 1)

    def test_orphan_is_proposed_and_can_be_completed(self):
        flac(self.path, 2, .3)
        item, = recovery.pending(self.root)
        self.assertTrue(item.orphan)
        result = recovery.resume(item)
        self.assertEqual(ffmpeg.probe(result.recording).roles[0].role, "me")
        self.assertEqual(recovery.pending(self.root), [])

    def test_stop_uses_the_teams_title_and_a_free_name(self):
        flac(self.path, 2, .3)
        journal = recovery.Journal(self.folder, self.stem)
        take = recording.Take("me-1", "me", "Mic", self.path, -10, meeting_title="Plan / example")
        occupied = self.root / f"{self.folder.name} Plan example"
        occupied.mkdir()
        result = recovery.finalize(journal, [take], elapsed=2)
        target = occupied.with_name(occupied.name + "_R01")
        self.assertEqual(result.recording, target / f"{target.name}.mka")
        self.assertEqual(result.together.parent, target)
        self.assertFalse(self.folder.exists())
        self.assertEqual(list(occupied.iterdir()), [])
        self.assertEqual(ffmpeg.probe(result.recording).meeting_title, take.meeting_title)

    def test_untitled_stop_keeps_the_dated_folder(self):
        flac(self.path, 2, .3)
        journal = recovery.Journal(self.folder, self.stem)
        result = recovery.finalize(journal, [recording.Take("me-1", "me", "Mic", self.path, -10)], elapsed=2)
        self.assertEqual(result.recording.parent, self.folder)

    def test_a_folder_locked_at_stop_still_keeps_the_recording_and_title(self):
        from scriba.app import meeting_folders

        flac(self.path, 2, .3)
        journal = recovery.Journal(self.folder, self.stem)
        take = recording.Take("me-1", "me", "Mic", self.path, -10, meeting_title="Plan")
        with patch.object(meeting_folders, "rename_directory", side_effect=PermissionError("open file")):
            result = recovery.finalize(journal, [take], elapsed=2)
        self.assertEqual(result.recording.parent, self.folder)
        self.assertEqual(ffmpeg.probe(result.recording).meeting_title, "Plan")
        self.assertIsNotNone(result.together)
        self.assertIn("Scriba will try again", result.notes[0])
        self.assertEqual(meeting_folders.read(self.folder).target, self.folder.name + " Plan")

    def test_restart_after_directory_move_reads_the_same_checkpoint_and_audio(self):
        from scriba.app import meeting_folders

        class Interrupted(BaseException):
            pass

        original = flac(self.path, 2, .3).read_bytes()
        journal = recovery.Journal(self.folder, self.stem)
        take = recording.Take("me-1", "me", "Mic", self.path, -10, meeting_title="Plan")
        journal.save("finishing:closing", [take], 2)
        rename = meeting_folders.rename_directory

        def interrupted(source, target):
            rename(source, target)
            raise Interrupted()

        with patch.object(meeting_folders, "rename_directory", side_effect=interrupted):
            with self.assertRaises(Interrupted):
                recovery.finalize(journal, [take], elapsed=2)
        item, = recovery.pending(self.root)
        self.assertEqual(item.folder.name, self.folder.name + " Plan")
        self.assertEqual(item.takes()[0].path.read_bytes(), original)
        finished = recovery.resume(item)
        self.assertEqual(finished.recording.parent, item.folder)
        self.assertEqual(ffmpeg.probe(finished.recording).meeting_title, "Plan")
        self.assertEqual(recovery.pending(self.root), [])

    def test_restart_at_mix_keeps_the_verified_join(self):
        flac(self.path, 2, .3)
        journal = recovery.Journal(self.folder, self.stem)
        take = recording.Take("me-1", "me", "Mic", self.path, -10)
        joined = recording.join([take], self.folder, self.stem)
        journal.save("finishing:mixing", [take], 2, joined=joined, mix_takes=[take])
        steps = []
        result = recovery.resume(recovery.pending(self.root)[0], progress=steps.append)
        self.assertEqual(steps, [recording.MIXING])
        self.assertEqual(result.recording, joined)
        self.assertEqual(list(self.folder.glob("*.mka")), [joined])
        self.assertIsNotNone(result.together)


    def test_a_crash_during_capture_can_salvage_the_complete_flac_frames(self):
        flac(self.path, 3, .3)
        with self.path.open("r+b") as handle:
            handle.truncate(self.path.stat().st_size - 256)
        take = recording.Take("me-1", "me", "Mic", self.path, -10)
        journal = recovery.Journal(self.folder, self.stem)
        journal.save("recording", [take], 1)
        result = recovery.resume(recovery.pending(self.root)[0])
        seconds = ffmpeg.decoded_seconds(result.recording)
        self.assertGreater(seconds, 2.5)
        self.assertLessEqual(seconds, 3)

    def test_crash_after_publishing_the_join_does_not_create_a_second_mka(self):
        flac(self.path, 2, .3)
        take = recording.Take("me-1", "me", "Mic", self.path, -10)
        journal = recovery.Journal(self.folder, self.stem)
        join_id = "f40a5ba364124e788cf19c00f0c2bf23"
        journal.save("finishing:joining", [take], 2, join_id=join_id)
        joined = recording.join([take], self.folder, self.stem, join_id=join_id)
        result = recovery.resume(recovery.pending(self.root)[0])
        self.assertEqual(result.recording, joined)
        self.assertEqual(list(self.folder.glob("*.mka")), [joined])

    def test_stop_offers_the_choice_after_a_verified_join_and_keeps_the_whole_recording(self):
        journal, take = self.call_recording()

        def choose(interval, cancelled, duration, call):
            self.assertAlmostEqual(duration, 90, delta=.2)
            self.assertEqual(call, interval)
            self.assertEqual(interval, (12, 32))
            state = journal.read()
            self.assertEqual(state["phase"], "finishing:waiting")
            self.assertAlmostEqual(ffmpeg.probe(journal.verified_join(state)).duration, 90, delta=.2)
            return False

        result = recovery.finalize(journal, [take], elapsed=90, choose=choose)
        self.assertAlmostEqual(ffmpeg.probe(result.recording).duration, 90, delta=.2)
        self.assertIsNotNone(result.together)
        self.assertTrue(journal.read()["choice_done"])

    def test_resume_while_waiting_can_keep_only_the_call_without_the_flac(self):
        journal, take = self.call_recording()
        from scriba.core.teams import ReadingSummary

        summary = ReadingSummary()
        summary.observe(5, "ready", 8, True, "print-window", True, (0, .91, .90, .92))
        journal.teams_minutes = summary.minutes
        joined = recording.join([take], self.folder, self.stem, call_range=(12, 32))
        original = joined.read_bytes()
        journal.save("finishing:waiting", [take], 90, joined=joined, call_range=(12, 32))
        take.path.unlink()
        item, = recovery.pending(self.root)

        def choose(interval, cancelled, duration, call):
            self.assertEqual(interval, (12, 32))
            self.assertEqual(journal.verified_join(journal.read()), joined)
            return True

        result = recovery.resume(item, choose=choose)
        media = ffmpeg.probe(result.recording)
        self.assertAlmostEqual(media.duration, 20, delta=.2)
        self.assertEqual(media.teams_people, 8)
        self.assertEqual(journal.read()["teams_people"], 8)
        self.assertEqual(journal.read()["teams_minutes"], summary.minutes)
        self.assertAlmostEqual(ffmpeg.probe(result.together).duration, 20, delta=.2)
        self.assertFalse(joined.exists())
        archived, = self.folder.glob("*/*.mka")
        self.assertEqual(archived.read_bytes(), original)
        self.assertEqual(recovery.pending(self.root), [])

    def test_retries_an_excerpt_published_before_its_checkpoint(self):
        journal, take = self.call_recording()
        joined = recording.join([take], self.folder, self.stem)
        journal.save("finishing:trimming", [take], 90, joined=joined, excerpt=(12, 32), join_id="excerpt")
        excerpt = recording.copy_excerpt(joined, 12, 32, join_id="excerpt")
        with patch.object(recording, "copy_excerpt", side_effect=AssertionError("excerpt already published")):
            result = recovery.resume(journal)
        self.assertEqual(result.recording, excerpt)
        self.assertEqual(list(self.folder.glob("*.mka")), [excerpt])

    def test_timeout_trims_both_tracks_before_cleaning_and_mixing(self):
        journal, take = self.call_recording()
        out = recording.Take("out", "others", "Out", flac(self.folder / "out.flac", 90, .3, 660), -10)
        steps = []

        def timeout(interval, cancelled, duration, call):
            now = [0]
            choice = finishing.CallChoice(interval, duration=duration, call=call, timer=lambda: now[0])
            self.assertTrue(choice.select((13, 31)))
            now[0] += 120
            return choice.interval if choice.wait(cancelled) else None

        result = recovery.finalize(journal, [take, out], threading.Event(), steps.append, elapsed=90, choose=timeout)
        self.assertEqual(steps, [recording.JOINING, recording.WAITING, recording.TRIMMING,
                                 recording.CLEANING, recording.MIXING])
        self.assertEqual([track.role for track in ffmpeg.probe(result.recording).roles], ["me", "others"])
        self.assertAlmostEqual(ffmpeg.probe(result.together).duration, 18, delta=.2)
        self.assertEqual(journal.read()["excerpt"], [13, 31])
        self.assertFalse(recovery.pending(self.root))

    def call_recording(self):
        flac(self.path, 90, .3)
        take = recording.Take("me-1", "me", "Mic", self.path, -10, teams_people=8)
        journal = recovery.Journal(self.folder, self.stem)
        journal.save("finishing:closing", [take], 90, call_range=(12, 32))
        return journal, take
