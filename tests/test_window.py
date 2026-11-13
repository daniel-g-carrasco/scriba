#!/usr/bin/env python3
"""The window, where there is a display: the models' page when one is
missing, the Transcribe page, a made-up job that reaches the Result page,
Cancel, the names that write new files, the preferences; and what the
template's window had (the remembered size, the theme, About, the update
banner and dialog, the font and the title bars on Windows, the style
sheets). One test runs the real engines from the window."""

import contextlib
import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _setup

Gtk, Adw = _setup.gtk_or_skip()

import fake_engines  # noqa: E402
from test_engines import AUDIO, MODELS, real  # noqa: E402

from scriba import APP_NAME  # noqa: E402
from scriba.app import backend, jobs, models, updates  # noqa: E402
from scriba.app.config import Config  # noqa: E402
from scriba.app.engines import ffmpeg  # noqa: E402
from scriba.app.paths import Paths  # noqa: E402
from scriba.app.task import Failure  # noqa: E402
from scriba.core import speakers  # noqa: E402
from scriba.gui import application as app_mod  # noqa: E402
from scriba.gui.preferences import PreferencesDialog, about_dialog  # noqa: E402
from scriba.gui.update_dialog import UpdateDialog  # noqa: E402
from scriba.gui.window import MainWindow  # noqa: E402

VIDEO = ffmpeg.Media(3725.0, 1, True, 0)


class TestApp(app_mod.App):
    """The application, never run: its folders in a temporary place, its
    saves counted, the engines made up, so that the window has the
    application a GTK window needs without a main loop of its own."""

    def __init__(self, tmp: str, engines=None):
        super().__init__()
        self.paths = Paths(Path(tmp) / "config", Path(tmp) / "state", Path(tmp) / "data")
        # the recordings in the temporary place too: never in the user's Music
        self.cfg = Config(recordings_folder=str(Path(tmp) / "recordings"))
        self.backend = engines or fake_engines.FakeBackend(Path(tmp))
        self.saved = 0

    def save_config(self):
        self.saved += 1


class WindowTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.engines = fake_engines.FakeBackend(self.tmp)
        self.app = TestApp(tmp.name, self.engines)
        self.recording = self.tmp / "call.m4a"
        self.recording.write_bytes(b"the user's recording")
        self.out = self.tmp / "out"
        # the cards tested already: a window would test them at once otherwise
        # (the tests of that test say untested()), and its thread would still
        # be writing its speeds while the temporary folder goes
        self.app.cfg.measured_device = fake_engines.CARD.name

    def untested(self):
        self.app.cfg.measured_device = ""

    def window(self) -> MainWindow:
        window = MainWindow(self.app)
        window.result.muted = True
        self.addCleanup(window.destroy)
        return window

    def opened(self, media=None) -> MainWindow:
        """A window with the recording open and read."""
        if media is not None:
            self.engines.media = media
        self.app.cfg.output_folder = str(self.out)
        window = self.window()
        window.present()
        window.open_path(str(self.recording))
        page = window.transcribe
        self.assertTrue(_setup.spin(lambda: page.media is not None))
        return window

    def state(self, window) -> str:
        return window.transcribe.stack.get_visible_child_name()

    def test_the_main_menu_opens_the_error_log(self):
        window = self.window()
        model = window.menu_button().get_menu_model()
        actions = [model.get_item_link(s, "section").get_item_attribute_value(i, "action", None).unpack()
                   for s in range(model.get_n_items())
                   for i in range(model.get_item_link(s, "section").get_n_items())]
        self.assertIn("app.error-log", actions)
        log = self.app.paths.state / "errors.log"
        with mock.patch("scriba.gui.window.ui_open_path") as opened, mock.patch.object(window, "toast") as toast:
            window.show_error_log()
            opened.assert_not_called()
            toast.assert_called_once()
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("--- 2026-11-13 12:00:00 ---\n", encoding="utf-8")
            window.show_error_log()
            opened.assert_called_once_with(window, str(log))

    def test_both_downloaded_models_remain_selectable(self):
        self.engines.precise = True
        window = self.opened()
        page = window.transcribe
        preferences = PreferencesDialog(window, self.app)
        for index, quality in ((1, "large"), (0, "turbo")):
            with self.subTest(quality=quality):
                preferences.quality_row.set_selected(index)
                self.assertEqual(self.app.cfg.quality, quality)
                page.quality_row.set_selected(index)
                self.assertEqual(page.cfg.quality, quality)
                self.assertTrue(page.quality_row.get_visible())

    def test_folder_buttons_reach_the_picker_with_a_translated_title(self):
        window = self.window()
        preferences = PreferencesDialog(window, self.app)

        def buttons(widget):
            if isinstance(widget, Gtk.Button):
                yield widget
            child = widget.get_first_child()
            while child is not None:
                yield from buttons(child)
                child = child.get_next_sibling()

        targets = [("record", window.record.choose_button, "scriba.gui.record")]
        for name, row, module in (
            ("transcription", window.transcribe.folder_row, "scriba.gui.transcribe"),
            ("usual folder", preferences.folder_row, "scriba.gui.preferences"),
            ("recordings preference", preferences.recordings_row, "scriba.gui.preferences"),
        ):
            button = next(b for b in buttons(row) if b.get_icon_name() == "folder-open-symbolic")
            targets.append((name, button, module))
        for name, button, module in targets:
            with self.subTest(button=name), mock.patch(module + ".choose_folder") as picker:
                button.emit("clicked")
                picker.assert_called_once()
                self.assertIs(picker.call_args.args[0], window)
                self.assertIsInstance(picker.call_args.args[1], str)
                self.assertTrue(picker.call_args.args[1])

    # -- the first start ---------------------------------------------------------------

    def test_with_models_missing_record_stays_available_and_the_models_page_downloads(self):
        self.engines._missing = list(backend.needed_models())
        window = self.window()
        self.assertEqual(self.state(window), "empty")
        self.assertEqual(window.views.get_visible_child_name(), "record")
        window.show_models()
        page = window.models
        self.assertEqual(page.rows[models.WESPEAKER.key].get_title(), models.purpose(models.WESPEAKER))
        self.assertIn("MB", page.rows[models.WESPEAKER.key].get_subtitle())
        page.download()
        self.assertTrue(_setup.spin(lambda: not self.engines.missing()))
        self.assertEqual(self.engines.missing(), [])

    def test_a_failed_download_is_said_in_words_and_can_be_tried_again(self):
        self.engines._missing = [models.WHISPER_TURBO]
        window = self.window()
        page = window.models
        with mock.patch.object(self.engines, "download",
                               side_effect=models.ModelError("The download of x stopped at 3 of 5 MB.", "cut")), \
                contextlib.redirect_stderr(io.StringIO()) as said:
            page.download()
            self.assertTrue(_setup.spin(lambda: page.error.get_visible()))
        self.assertIn("stopped at 3 of 5 MB", page.error.get_label())
        self.assertIn("cut", said.getvalue())
        self.assertTrue(page.download_button.get_visible())
        self.assertEqual(self.state(window), "empty")

    # -- Transcribe --------------------------------------------------------------------

    def test_with_the_models_there_the_window_asks_for_a_recording_and_shows_the_choices(self):
        window = self.window()
        self.assertEqual(self.state(window), "empty")
        self.assertFalse(window.open_button.get_visible())
        window = self.opened()
        page = window.transcribe
        self.assertEqual(self.state(window), "setup")
        self.assertTrue(window.open_button.get_visible())
        self.assertEqual(page.file_row.get_title(), "call.m4a")
        self.assertIn("10:00", page.file_row.get_subtitle())
        self.assertFalse(page.video_row.get_visible())          # an audio file: no video to make
        self.assertTrue(page.quality_row.get_visible())         # Precise remains discoverable before download
        self.assertTrue(page.start_button.get_sensitive())
        self.assertIn("10:00", page.estimate_row.get_subtitle())
        self.assertEqual(page.folder_row.get_subtitle(), jobs.full_path(self.out))
        self.assertEqual(self.app.cfg.last_folder, str(self.tmp))

    def test_a_file_kept_elsewhere_goes_to_a_folder_of_its_own_in_scriba_s_unless_the_preference_says_beside(self):
        window = self.window()
        window.present()
        window.open_path(str(self.recording))
        page = window.transcribe
        self.assertTrue(_setup.spin(lambda: page.media is not None))
        recordings = self.tmp / "recordings"
        self.assertEqual(page.folder_row.get_subtitle(), jobs.full_path(recordings / "call"))
        request = page.request()
        self.assertEqual((request.out, request.home), (None, recordings))
        PreferencesDialog(window, self.app).elsewhere_row.set_selected(1)
        self.assertEqual(self.app.cfg.elsewhere, "beside")
        request = page.request()
        self.assertEqual((request.out, request.home), (self.tmp, None))

    # -- the test of the graphics cards ---------------------------------------------

    def test_with_the_models_there_the_cards_are_tested_at_once_on_the_sample(self):
        self.untested()
        window = self.window()
        self.assertTrue(_setup.spin(lambda: self.app.cfg.measured_device == fake_engines.CARD.name))
        self.assertEqual(self.engines.tests, 1)                 # no recording needed
        window.present()
        window.open_path(str(self.recording))
        page = window.transcribe
        self.assertTrue(_setup.spin(lambda: "an hour of audio in about" in page.device_row.get_subtitle()))
        self.assertIn(fake_engines.CARD.name, page.device_row.get_subtitle())
        _setup.spin(lambda: False, 0.2)
        self.assertEqual(self.engines.tests, 1)                 # tested once: not again

    def test_the_cards_are_tested_right_after_the_models_arrive(self):
        self.untested()
        self.engines._missing = list(backend.needed_models())
        window = self.window()
        _setup.spin(lambda: False, 0.2)
        self.assertEqual(self.engines.tests, 0)                 # nothing to test with yet
        window.models.download()
        self.assertTrue(_setup.spin(lambda: self.app.cfg.measured_device == fake_engines.CARD.name))
        self.assertEqual(self.engines.tests, 1)

    def test_the_row_says_the_test_is_going_and_a_transcription_stops_it_until_its_end(self):
        self.untested()
        hold = self.engines.hold_test = threading.Event()
        self.addCleanup(hold.set)
        self.engines.runner = fake_engines.runner(
            lambda r: fake_engines.made_result(self.out, self.recording, fake_engines.lines()))
        window = self.opened()
        page = window.transcribe
        self.assertTrue(_setup.spin(lambda: "Testing the graphics cards" in page.device_row.get_subtitle()))
        self.assertTrue(page.device_spinner.get_visible())
        page.start()                                            # the work wants the card
        self.assertIsNotNone(window.cards.running)
        self.assertTrue(window.cards.running.is_set())          # requested, not yet acknowledged
        hold.set()
        self.assertTrue(_setup.spin(lambda: window.nav.get_visible_page() is window.result))
        # the test the work stopped runs again once the work is over
        self.assertTrue(_setup.spin(lambda: self.app.cfg.measured_device == fake_engines.CARD.name))
        self.assertEqual(self.engines.tests, 2)

    def test_a_faster_card_that_writes_the_sample_wrong_is_left_out_and_that_is_said(self):
        self.untested()
        iris, card = fake_engines.IRIS, fake_engines.CARD
        self.engines.choice = jobs.Choice(card.name, card.label, 17.5, 300.0, (
            jobs.Tried(iris.name, iris.label, 30.0, 5.0, 0.31), jobs.Tried(card.name, card.label, 17.5, 2.0, 0.02),
            jobs.Tried("cpu", "cpu", 1.5, 18.0, 0.02)))
        window = self.opened()
        page = window.transcribe
        self.assertTrue(_setup.spin(lambda: "31 %" in page.device_row.get_subtitle()))
        self.assertIn(iris.name, page.device_row.get_subtitle())
        self.assertIn(f"We will use the {card.name}", page.device_row.get_subtitle())
        # and at the next start, from what the test kept
        again = self.window()
        self.assertTrue(_setup.spin(lambda: again.cards.notes == window.cards.notes))

    def test_a_failed_test_is_said_and_the_preferences_run_it_again(self):
        self.untested()
        self.engines.test_fails = Failure("whisper did not work on any graphics card nor on the processor.", "exit 3")
        with contextlib.redirect_stderr(io.StringIO()) as said:
            window = self.opened()
            page = window.transcribe
            self.assertTrue(_setup.spin(lambda: "failed" in page.device_row.get_subtitle()))
        self.assertIn("exit 3", said.getvalue())
        self.assertTrue(page.device_row.has_css_class("warning"))
        dialog = PreferencesDialog(window, self.app)
        self.assertIn("did not work", dialog.test_row.get_subtitle())
        self.assertTrue(dialog.test_row.has_css_class("warning"))
        self.engines.test_fails = None
        dialog.test_button.emit("clicked")
        self.assertTrue(_setup.spin(lambda: self.app.cfg.measured_device == fake_engines.CARD.name))
        self.assertIn(fake_engines.CARD.name, dialog.test_row.get_subtitle())
        self.assertFalse(dialog.test_row.has_css_class("warning"))
        self.assertTrue(_setup.spin(lambda: "an hour of audio in about" in page.device_row.get_subtitle()))
        self.assertEqual(self.engines.tests, 2)

    def test_test_again_measures_once_more_and_keeps_the_new_choice(self):
        window = self.window()
        dialog = PreferencesDialog(window, self.app)
        self.assertIn(fake_engines.CARD.name, dialog.test_row.get_subtitle())
        self.engines.choice = jobs.Choice("cpu", "cpu", 1.2, 6000.0, (jobs.Tried("cpu", "cpu", 1.2, 25.0, 0.0),))
        dialog.test_button.emit("clicked")
        self.assertFalse(dialog.test_button.get_sensitive())   # one test at a time
        self.assertTrue(_setup.spin(lambda: self.app.cfg.measured_device == "cpu"))
        self.assertIn("the processor", dialog.test_row.get_subtitle())
        self.assertIn("the processor", dialog.device_row.get_subtitle())
        self.assertTrue(dialog.test_button.get_sensitive())

    def test_a_preference_changed_keeps_the_terms_typed_and_shows_the_precise_model(self):
        window = self.opened()
        page = window.transcribe
        page.terms_row.set_text("QG&UPS, UPS")
        self.assertTrue(page.quality_row.get_visible())
        self.engines.precise = True                             # downloaded from the shared models page
        window.settings_changed()
        self.assertEqual(page.terms_row.get_text(), "QG&UPS, UPS")
        self.assertTrue(page.quality_row.get_visible())

    def test_the_expected_time_says_when_it_is_only_a_first_estimate(self):
        self.engines.precise = True
        window = self.opened()
        page = window.transcribe
        page.quality_row.set_selected(0)
        request = page.request()
        jobs.remember_speeds(self.engines.speeds_file, {
            jobs.speed_key("turbo", self.engines.device_label(request)): 8.0, jobs.speed_key("speakers", "cpu"): 30.0})
        page.refresh_estimate()
        self.assertNotIn("first estimate", page.estimate_row.get_subtitle())
        page.quality_row.set_selected(1)                        # Precise, never run on this card
        self.assertIn("first estimate", page.estimate_row.get_subtitle())

    def test_the_people_speaking_reach_the_engine_counted_or_automatic(self):
        window = self.opened()
        page = window.transcribe
        page.voices_row.set_active(True)
        self.assertTrue(page.automatic_row.get_active())        # automatic unless told
        self.assertFalse(page.count_row.get_sensitive())
        self.assertIsNone(page.request().speaker_count)
        page.automatic_row.set_active(False)
        self.assertTrue(page.count_row.get_sensitive())
        page.count_row.set_value(1)                             # one person heard is a count too
        self.assertEqual(page.request().speaker_count, 1)
        page.count_row.set_value(4)
        self.assertEqual(page.request().speaker_count, 4)
        page.automatic_row.set_active(True)
        self.assertIsNone(page.request().speaker_count)
        page.voices_row.set_active(False)                       # no voices told apart: nothing to count
        self.assertFalse(page.automatic_row.get_sensitive())
        self.assertFalse(page.count_row.get_sensitive())

    def test_a_video_offers_its_copy_with_the_subtitles(self):
        window = self.opened(VIDEO)
        page = window.transcribe
        self.assertTrue(page.video_row.get_visible())
        self.assertIn("1:02:05", page.file_row.get_subtitle())
        page.video_row.set_active(True)
        self.assertTrue(page.request().video)

    def test_a_recording_that_cannot_be_read_is_said_and_not_started(self):
        self.app.cfg.output_folder = str(self.out)
        window = self.window()
        with mock.patch.object(self.engines, "inspect", side_effect=Failure("call.m4a cannot be read.", "probe")), \
                contextlib.redirect_stderr(io.StringIO()):
            window.open_path(str(self.recording))
            self.assertTrue(_setup.spin(lambda: "cannot be read" in window.transcribe.file_row.get_subtitle()))
        self.assertFalse(window.transcribe.start_button.get_sensitive())
        window.open_path(str(self.tmp / "gone.mp3"))           # not there: said, nothing opened
        self.assertEqual(window.transcribe.source, self.recording)

    def test_a_made_up_job_goes_through_its_steps_to_the_result_page(self):
        made = []

        def make(request):
            made.append(request)
            return fake_engines.made_result(self.out, self.recording, fake_engines.lines(3, 30, 4))

        self.engines.runner = fake_engines.runner(make, step=0.01)
        window = self.opened()
        page = window.transcribe
        page.terms_row.set_text("Cantiere Esempio, cavedi")
        page.start_button.emit("clicked")
        self.assertEqual(self.state(window), "working")
        self.assertFalse(window.open_button.get_visible())
        self.assertTrue(_setup.spin(lambda: window.nav.get_visible_page() is window.result))
        self.assertEqual(made[0].terms, ("Cantiere Esempio", "cavedi"))
        self.assertEqual(self.app.cfg.terms, "Cantiere Esempio, cavedi")             # remembered for the next time
        self.assertEqual(made[0].out, self.out)
        result = window.result
        self.assertIn("3 voices", result.outcome.get_label())
        self.assertIn("4 doubtful lines", result.outcome.get_label())
        self.assertEqual(sorted(result.entries), ["Speaker 1", "Speaker 2", "Speaker 3"])
        self.assertEqual(self.state(window), "setup")                       # behind: the same recording
        # back: the choices for the same recording; New Transcription: the empty page
        window.nav.pop()
        self.assertTrue(_setup.spin(lambda: window.nav.get_visible_page() is window.nav.find_page("main")))
        window.show_result(result.result, made[0])
        window.new_transcription()
        self.assertTrue(_setup.spin(lambda: self.state(window) == "empty"))

    def test_cancel_goes_back_with_no_file_written(self):
        hold = threading.Event()
        self.addCleanup(hold.set)
        self.engines.runner = fake_engines.runner(
            lambda r: fake_engines.made_result(self.out, self.recording, fake_engines.lines()), hold=hold)
        window = self.opened()
        page = window.transcribe
        page.start()
        self.assertTrue(_setup.spin(lambda: page.job is not None and page.job[0].stage == "transcribe"))
        self.assertTrue(_setup.spin(lambda: "%" in (page.work_bar.get_text() or "")))
        page.cancel_button.emit("clicked")
        self.assertTrue(_setup.spin(lambda: self.state(window) == "setup"))
        self.assertIsNone(page.job)
        self.assertFalse(self.out.exists())
        self.assertIsNot(window.nav.get_visible_page(), window.result)

    def test_a_failure_is_a_page_that_says_why(self):
        def broken(request, progress, cancelled):
            raise Failure("whisper-cli is missing: Scriba needs it to turn speech into text.", "no whisper-cli")
        self.engines.runner = broken
        window = self.opened()
        with contextlib.redirect_stderr(io.StringIO()):
            window.transcribe.start()
            self.assertTrue(_setup.spin(lambda: self.state(window) == "failed"))
        self.assertIn("whisper-cli is missing", window.transcribe.failed_page.get_description())

    # -- the Result --------------------------------------------------------------------

    def result_window(self):
        self.engines.runner = fake_engines.runner(
            lambda r: fake_engines.made_result(self.out, self.recording, fake_engines.lines(3, 30, 2)))
        window = self.opened()
        window.transcribe.start()
        self.assertTrue(_setup.spin(lambda: window.nav.get_visible_page() is window.result))
        return window

    def test_the_names_of_the_voices_write_new_files(self):
        window = self.result_window()
        result = window.result
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        self.assertFalse(result.save_button.get_sensitive())
        result.entries["Speaker 2"].set_text("Anna Rossi")
        self.assertTrue(result.save_button.get_sensitive())
        self.assertEqual(result.headings["Speaker 2"][0].get_label(), "Anna Rossi")     # the text follows the name
        result.save_button.emit("clicked")
        named = self.out / "call_transcript_names.md"
        self.assertTrue(_setup.spin(lambda: named.is_file()))
        self.assertIn("**Anna Rossi**", named.read_text(encoding="utf-8"))
        for name, content in before.items():
            if not name.endswith(".json"):
                self.assertEqual((self.out / name).read_bytes(), content, name)
        self.assertTrue(_setup.spin(lambda: "md-named" in {o.kind for o in result.result.outputs}))

    def test_listen_plays_the_voice_and_the_same_button_stops_it(self):
        import numpy as np
        from scriba.app.engines import audio
        window = self.result_window()
        result = window.result
        wav = self.tmp / "listening.wav"
        voice = next(v for v in speakers.voices(result.result.segments) if v.label == "Speaker 1")
        # long enough for the voice's stretch, wherever in the meeting it is
        audio.write(wav, np.zeros(int(voice.listen[1] + 1) * audio.RATE, np.int16))
        result.result.listen = wav
        result.listen(voice)
        self.assertTrue(_setup.spin(lambda: result.media is not None and result.media.is_prepared()))
        self.assertEqual(result.listen_buttons["Speaker 1"].get_icon_name(), "media-playback-stop-symbolic")
        path = Path(result.media.get_file().get_path())
        self.assertEqual(path.suffix, ".wav")
        self.assertLess(result.media.get_timestamp(), 1_000_000)
        result.listen(voice)
        self.assertIsNone(result.media)
        self.assertEqual(result.listen_buttons["Speaker 1"].get_icon_name(), "media-playback-start-symbolic")
        self.assertTrue(_setup.spin(lambda: not path.parent.exists()))

    def test_a_session_opens_with_models_missing_and_names_can_be_corrected(self):
        from scriba.app import jobs
        made = fake_engines.made_result(self.out, self.recording, fake_engines.lines(3, 12, 2))
        jobs.save_names(made, {"Speaker 2": "Anna"})
        self.engines._missing = list(backend.needed_models())
        window = self.window()
        window.open_path(str(made.session))
        self.assertTrue(_setup.spin(lambda: window.nav.get_visible_page() is window.result))
        self.assertEqual(window.result.entries["Speaker 2"].get_text(), "Anna")
        self.assertTrue(window.lookup_action("open").get_enabled())
        self.app.cfg.my_name = "Daniel"
        window.result.my_voice.set_selected(1)
        self.assertEqual(window.result.entries["Speaker 1"].get_text(), "Daniel")
        window.result.entries["Speaker 2"].set_text("An")
        self.assertEqual(window.result.suggestion_names("Speaker 2"), ["Anna"])
        window.result._use_suggestion("Speaker 2", "Anna")
        previous = window.result.result.session
        window.result.save_names()
        self.assertTrue(_setup.spin(lambda: "Anna" in self.app.cfg.speaker_names))
        self.assertEqual(window.result.result.session, previous)        # names: no new meeting file
        self.assertIn("Anna", self.app.cfg.speaker_names)
        # the files with the names, saved twice, are listed once
        named = [o.path for o in window.result.result.outputs if o.kind == "md-named"]
        self.assertEqual(len(named), 1)
        self.assertEqual(len(window.result._shown_outputs & set(named)), 1)

    def test_a_saved_anonymous_microphone_stays_anonymous_when_reopened(self):
        from scriba.app import jobs
        made = fake_engines.made_result(self.out, self.recording, fake_engines.lines(2, 4, 0))
        made.me, made.names = "Speaker 1", {"Speaker 1": "Io"}
        jobs.save_names(made, {})
        opened, request = jobs.open_session(made.session)
        window = self.window()
        window.show_result(opened, request)
        self.assertEqual(window.result.entries["Speaker 1"].get_text(), "")

    def test_editing_names_during_a_save_cannot_start_a_second_save(self):
        from scriba.app import jobs
        made = fake_engines.made_result(self.out, self.recording, fake_engines.lines(2, 4, 0))
        window = self.window()
        window.show_result(made, jobs.Request(self.recording))
        page = window.result
        page.entries["Speaker 1"].set_text("Anna")
        hold = threading.Event()
        saved = self.engines.save_names

        def delayed(result, names):
            hold.wait(3)
            return saved(result, names)

        with mock.patch.object(self.engines, "save_names", delayed):
            page.save_names()
            page.entries["Speaker 1"].set_text("Bob")
            sensitive = page.save_button.get_sensitive()
            page.save_names()
            hold.set()
            self.assertTrue(_setup.spin(lambda: bool(made.names)))
            _setup.spin(lambda: False, 0.2)
        self.assertFalse(sensitive)
        self.assertEqual(len([o for o in made.outputs if o.kind == "md-named"]), 1)

    def test_the_listening_balance_saves_in_a_thread_and_listen_uses_the_new_copy(self):
        from scriba.app import recording
        from test_recording import flac
        takes = [recording.Take("mic", "me", "Microphone", flac(self.tmp / "mic.flac", 2, 0.1)),
                 recording.Take("out", "others", "Headphones", flac(self.tmp / "out.flac", 2, 0.2, 660))]
        source = recording.join(takes, self.tmp, "meeting")
        from scriba.core.segments import Segment
        made = fake_engines.made_result(self.out, source, [Segment(0, 1, "First", "Speaker 1"),
                                                         Segment(1, 2, "Second", "Speaker 2")])
        self.engines.balanceable = True
        window = self.window()
        window.show_result(made, jobs.Request(source))
        page = window.result
        self.assertTrue(_setup.spin(lambda: page.balance_group.get_visible()))
        page.balance_scale.set_value(0.5)
        hold = threading.Event()
        saved = self.engines.save_balance

        def delayed(result, value):
            hold.wait(3)
            return saved(result, value)

        with mock.patch.object(self.engines, "save_balance", delayed):
            page.save_balance()
            self.assertFalse(page.balance_button.get_sensitive())
            self.assertFalse(page.balance_scale.get_sensitive())
            self.assertFalse(page.save_button.get_sensitive())
            hold.set()
            self.assertTrue(_setup.spin(lambda: made.listen is not None and page.balance_button.get_sensitive()))
        self.assertTrue(made.listen.is_file())
        self.assertIsNone(page.media)
        heard = []
        prepare = self.engines.preview

        def preview(source, start, end, cancelled):
            heard.append(Path(source))
            return prepare(source, start, end, cancelled)

        with mock.patch.object(self.engines, "preview", preview):
            page.listen(speakers.voices(made.segments)[0])
            self.assertTrue(_setup.spin(lambda: page.media is not None))
        self.assertEqual(heard, [made.listen])
        clip = Path(page.media.get_file().get_path())
        self.assertNotEqual(clip, made.listen)
        page.stop()
        self.assertTrue(_setup.spin(lambda: not clip.parent.exists()))
        self.engines.balanceable = False
        source.unlink()
        reopened, request = jobs.open_session(made.session)
        page.show(reopened, request)
        _setup.spin(lambda: False, 0.1)
        self.assertFalse(page.balance_group.get_visible())
        self.assertEqual(page.balance_scale.get_value(), 0.5)

    def test_a_balance_failure_is_said_and_the_controls_are_available_again(self):
        made = fake_engines.made_result(self.out, self.recording, fake_engines.lines(2, 4, 0))
        self.engines.balanceable = True
        window = self.window()
        window.show_result(made, jobs.Request(self.recording))
        page = window.result
        self.assertTrue(_setup.spin(lambda: page.balance_group.get_visible()))
        self.recording.unlink()
        said = []
        with mock.patch.object(window, "toast", said.append), contextlib.redirect_stderr(io.StringIO()):
            page.save_balance()
            self.assertTrue(_setup.spin(lambda: bool(said)))
        self.assertIn("original recording is missing", said[0])
        self.assertTrue(page.balance_button.get_sensitive())

    def test_the_balance_preview_plays_the_sliders_balance_and_the_same_button_stops_it(self):
        import numpy as np
        from scriba.app.engines import audio
        from scriba.app.preview import Clip
        from scriba.i18n import _
        made = fake_engines.made_result(self.out, self.recording, fake_engines.lines(2, 4, 0))
        self.engines.balanceable = True
        window = self.window()
        window.show_result(made, jobs.Request(self.recording))
        page = window.result
        self.assertTrue(_setup.spin(lambda: page.balance_group.get_visible()))
        before = sorted(p.name for p in self.out.iterdir())
        asked = []

        def balance_preview(result, balance, cancelled):        # the player's file, never the user's
            asked.append((result, balance))
            folder = Path(tempfile.mkdtemp(prefix="scriba-preview-", dir=self.tmp))
            audio.write(folder / "preview.wav", np.zeros(audio.RATE, np.int16))
            return Clip(folder / "preview.wav", 1.0)

        def playing():
            return page.media is not None and page.media.is_prepared()

        with mock.patch.object(self.engines, "balance_preview", balance_preview):
            page.balance_scale.set_value(-0.5)
            page.preview_button.emit("clicked")
            self.assertTrue(_setup.spin(playing))
            self.assertEqual(asked, [(made, -0.5)])
            self.assertEqual(page.preview_content.get_label(), _("Stop the Preview"))
            clip = Path(page.media.get_file().get_path())
            page.preview_button.emit("clicked")                 # the same button stops it
            self.assertIsNone(page.media)
            self.assertEqual(page.preview_content.get_label(), _("Listen to a Preview"))
            self.assertTrue(_setup.spin(lambda: not clip.parent.exists()))
            page.preview_button.emit("clicked")
            self.assertTrue(_setup.spin(playing))
            page.balance_scale.set_value(0.25)                  # a new balance: the old preview stops
            self.assertIsNone(page.media)
            page.preview_button.emit("clicked")                 # and the next one is made with it
            self.assertTrue(_setup.spin(playing))
            self.assertEqual(asked[-1], (made, 0.25))
            page.stop()
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), before)

    def test_my_name_preference_reaches_new_transcriptions(self):
        window = self.opened()
        dialog = PreferencesDialog(window, self.app)
        dialog.present(window)
        self.addCleanup(dialog.close)
        dialog.my_name.set_text("Daniel")
        request = self.engines.request(self.app.cfg, self.recording, self.out, False)
        self.assertEqual(request.my_name, "Daniel")
        self.assertGreater(self.app.saved, 0)

    # -- the preferences ---------------------------------------------------------------

    def test_the_preferences_save_the_card_chosen(self):
        window = self.window()
        dialog = PreferencesDialog(window, self.app)
        self.assertTrue(_setup.spin(lambda: fake_engines.CARD.name in dialog._devices))
        dialog.device_row.set_selected(dialog._devices.index(fake_engines.CARD.name))
        self.assertEqual(self.app.cfg.device, fake_engines.CARD.name)
        dialog.device_row.set_selected(dialog._devices.index("cpu"))
        self.assertEqual(self.app.cfg.device, "cpu")
        self.assertEqual(self.app.saved, 2)
        self.assertTrue(backend.request_for(self.app.cfg, self.recording, None, False).cpu)

    def test_preferences_link_to_the_shared_models_page_and_keep_the_usual_folder(self):
        window = self.window()
        dialog = PreferencesDialog(window, self.app)
        self.assertFalse(hasattr(dialog, "precise_button"))
        dialog._set_folder(str(self.out))
        self.assertEqual(self.app.cfg.output_folder, str(self.out))
        self.assertTrue(dialog.folder_reset.get_visible())
        dialog.present(window)
        dialog._open_models()
        self.assertIs(window.nav.get_visible_page(), window.models)
        window.models.precise_button.emit("clicked")
        self.assertTrue(_setup.spin(lambda: self.engines.has(models.WHISPER_LARGE) and not self.app.downloads.busy))
        self.assertTrue(_setup.spin(lambda: not window.models.precise_button.get_visible()))

    def test_the_size_is_remembered(self):
        window = self.window()
        window.set_default_size(640, 700)
        window.save_state()
        self.assertTrue(self.app.paths.window_state_file.exists())
        again = self.window()
        self.assertEqual(again.get_default_size(), (640, 700))

    def test_the_preferences_apply_the_theme_and_save(self):
        window = self.window()
        dialog = PreferencesDialog(window, self.app)
        style = Adw.StyleManager.get_default()
        self.addCleanup(style.set_color_scheme, Adw.ColorScheme.DEFAULT)
        dialog._set_theme("dark")
        self.assertEqual(style.get_color_scheme(), Adw.ColorScheme.FORCE_DARK)
        self.assertEqual((self.app.cfg.theme, self.app.saved), ("dark", 1))

    def test_the_about_window_has_the_troubleshooting_text(self):
        dialog = about_dialog(self.window(), self.app.cfg, self.app.paths, present=False)
        self.assertIn(APP_NAME, dialog.get_debug_info())
        self.assertTrue(_setup.spin(lambda: "python:" in dialog.get_debug_info()))
        self.assertNotIn("password", dialog.get_debug_info().lower())

    def test_a_newer_version_is_a_banner_in_the_window(self):
        window = self.window()
        self.assertFalse(window.update_banner.get_revealed())
        asked = []
        window.show_update("99.0.0", lambda: asked.append("dialog"))
        self.assertTrue(window.update_banner.get_revealed())
        self.assertIn("99.0.0", window.update_banner.get_title())
        self.assertTrue(window.update_banner.get_button_label())
        window.update_banner.emit("button-clicked")
        self.assertEqual(asked, ["dialog"])

    def test_the_check_shows_the_banner_and_opens_the_dialog_only_when_asked(self):
        release = updates.Release("99.0.0", "https://example/release", "Notes", [])
        self.app.window = self.window()
        shown, notes = [], []
        with mock.patch.object(app_mod, "run_in_thread", lambda work, done: done(release, None)), \
                mock.patch.object(type(self.app), "_show_update", lambda _app, r: shown.append(r.version)), \
                mock.patch.object(type(self.app), "notify", lambda _app, *a, **k: notes.append(a[0])):
            self.app.check_updates(manual=False)
            self.assertTrue(self.app.window.update_banner.get_revealed())
            self.assertEqual(shown, [])                 # found by itself: the banner, no dialog in the way
            self.assertEqual(len(notes), 1)             # and a notification: the window is not in front
            self.app.window.update_banner.emit("button-clicked")
            self.assertEqual(shown, ["99.0.0"])         # the banner's button opens it
            self.app.check_updates(manual=True)
            self.assertEqual(shown, ["99.0.0", "99.0.0"])   # asked for: the dialog at once
            self.assertEqual(len(notes), 1)

    def test_the_update_dialog(self):
        dialog = UpdateDialog(updates.Release("99.0.0", "https://example/release", "Notes\n\n- one line", []))
        self.assertIs(dialog.get_focus(), dialog.action)
        self.assertIs(dialog.get_default_widget(), dialog.action)
        dialog._cancel.set()            # "Later" (the dialog closed) once the last chunk was in
        with mock.patch("scriba.gui.update_dialog.subprocess.Popen") as popen:
            dialog._downloaded(Path("setup.exe"))
        popen.assert_not_called()


class RecordPageTest(unittest.TestCase):
    """The Record page with a made-up recorder (tests/fake_engines.py): the
    devices of the live call of 11/11/2026, no device touched."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.engines = fake_engines.FakeBackend(self.tmp)
        self.app = TestApp(tmp.name, self.engines)
        # the cards tested already: a recording opened in Transcribe starts no
        # test of its own, whose thread would still be writing its speeds
        # while the temporary folder goes (WinError 32, one run in ten)
        self.app.cfg.measured_device = fake_engines.CARD.name

    def window(self) -> MainWindow:
        window = MainWindow(self.app)
        window.result.muted = True
        self.addCleanup(window.destroy)
        window.present()
        self.assertTrue(_setup.spin(lambda: window.get_mapped(), 5))
        page = window.record
        self.assertTrue(_setup.spin(lambda: "Headset BT" in (page.input_side.row.get_subtitle() or ""), 5))
        return window

    def recording(self, window):
        page = window.record
        page.record_button.emit("clicked")
        self.assertTrue(_setup.spin(lambda: page.recorder is not None, 5))
        return page, page.recorder

    def test_the_record_view_comes_first_and_its_meters_run_only_while_it_is_on_screen(self):
        window = self.window()
        self.assertEqual(window.views.get_visible_child_name(), "record")
        self.assertEqual(len(self.engines.monitors), 1)
        self.assertEqual(self.engines.monitors[0].started, 1)
        window.views.set_visible_child_name("transcribe")
        self.assertTrue(_setup.spin(lambda: self.engines.monitors[0].stopped == 1, 5))
        self.assertIsNone(window.record.monitor)
        window.views.set_visible_child_name("record")
        self.assertEqual(len(self.engines.monitors), 2)

    def test_the_automatic_choice_says_which_devices_and_why(self):
        page = self.window().record
        microphone = page.input_side.row.get_subtitle()
        self.assertIn("Automatic · Cuffia auricolare (Headset BT)", microphone)
        self.assertIn("in use by Teams", page.input_side.auto.get_subtitle())
        sound = page.output_side.row.get_subtitle()
        self.assertIn("All outputs · Cuffie (Headset BT)", sound)
        self.assertIn("the silent ones are left out", page.output_side.auto.get_subtitle())
        # every device has its row and its meter, moving
        self.assertEqual(len(page.input_side.rows), 3)
        self.assertEqual(len(page.output_side.rows), 2)
        self.assertGreater(page.input_side.rows["in-headset"][2].get_value(), 0.8)
        self.assertEqual(page.output_side.rows["out-speakers"][2].get_value(), 0.0)
        self.assertTrue(page.input_side.auto_check.get_active())

    def test_a_device_chosen_by_hand_is_kept_and_recorded(self):
        window = self.window()
        page = window.record
        page.input_side.rows["in-webcam"][1].set_active(True)
        self.assertEqual(self.app.cfg.input_device, "in-webcam")
        page.refresh()
        self.assertIn("Chosen · ", page.input_side.row.get_subtitle())
        _page, recorder = self.recording(window)
        self.assertEqual(recorder.plan.input.id, "in-webcam")
        self.assertEqual([d.id for d in recorder.plan.outputs], ["out-headset", "out-speakers"])

    def test_muted_in_the_call_the_microphone_says_so_and_the_preference_reaches_the_recorder(self):
        from scriba import WINDOWS
        self.app.cfg.follow_call_mute = True
        page, recorder = self.recording(self.window())
        self.assertEqual(recorder.follow_call, WINDOWS)
        recorder.call_muted = True
        self.assertTrue(_setup.spin(lambda: page.input_side.row.get_subtitle() == "Muted in the call", 5))
        recorder.call_muted = False
        self.assertTrue(_setup.spin(lambda: "Recording · " in (page.input_side.row.get_subtitle() or ""), 5))

    def test_the_call_s_mute_is_followed_by_default(self):
        from scriba import WINDOWS
        _page, recorder = self.recording(self.window())
        self.assertEqual(recorder.follow_call, WINDOWS)

    def test_the_call_s_mute_is_not_followed_when_switched_off(self):
        self.app.cfg.follow_call_mute = False
        _page, recorder = self.recording(self.window())
        self.assertFalse(recorder.follow_call)

    def test_with_both_switches_off_there_is_nothing_to_record(self):
        page = self.window().record
        page.input_side.row.set_enable_expansion(False)
        page.output_side.row.set_enable_expansion(False)
        self.assertFalse(self.app.cfg.record_input or self.app.cfg.record_output)
        self.assertFalse(page.record_button.get_sensitive())
        page.output_side.row.set_enable_expansion(True)
        self.assertTrue(page.record_button.get_sensitive())

    def test_a_recording_shows_its_tracks_and_warnings_and_goes_to_transcribe_when_stopped(self):
        from scriba.app import recording as recordings
        from scriba.core import levels
        window = self.window()
        page, recorder = self.recording(window)
        self.assertTrue(window.record.busy)
        self.assertEqual([t.role for t in recorder.takes], ["me", "others", "others"])
        # a folder of its own, named with the date and the time its files begin with
        self.assertEqual(Path(recorder.folder).parent, self.tmp / "recordings")
        self.assertTrue(recorder.stem.startswith(Path(recorder.folder).name + " "))
        self.assertTrue(_setup.spin(lambda: len(page._track_rows) == 3, 5))
        self.assertTrue(page.sources.get_visible())
        self.assertTrue(page.stop_button.get_visible())
        recorder.elapsed = 75.0
        recorder.alerts = (levels.Alert(levels.QUIET, "me-1", -46.0),)
        recorder.events = (recordings.Event(70.0, recordings.GONE, "Cuffie (Headset BT)"),)
        recorder.takes[1].ended = 70.0
        self.assertTrue(_setup.spin(lambda: page.time_label.get_label() == "01:15", 5))
        self.assertTrue(_setup.spin(lambda: page.banner.get_revealed(), 5))
        self.assertIn("hears you faintly (-46 dB)", page.banner.get_title())
        self.assertIn("went away at 01:10", page._track_rows["others-2"][0].get_subtitle())
        page.pause_button.emit("clicked")
        self.assertEqual(recorder.state, "paused")
        page.pause_button.emit("clicked")
        self.assertEqual(recorder.state, "recording")
        page.stop_button.emit("clicked")
        self.assertTrue(_setup.spin(lambda: not page.busy, 5))
        joined = Path(recorder.folder) / f"{recorder.stem}.mka"
        self.assertTrue(joined.is_file())
        self.assertEqual(window.views.get_visible_child_name(), "transcribe")
        self.assertEqual(window.transcribe.source, joined)
        self.assertTrue(_setup.spin(lambda: window.transcribe.media is not None, 5))

    def test_the_banner_says_the_microphone_hears_the_computer_while_it_does(self):
        from scriba.core import echo, levels
        window = self.window()
        page, recorder = self.recording(window)
        recorder.alerts = (levels.Alert(echo.ECHO, "", 0.0),)
        self.assertTrue(_setup.spin(lambda: page.banner.get_revealed(), 5))
        self.assertIn("sound: with earphones the transcript comes out better", page.banner.get_title())
        recorder.alerts = ()
        self.assertTrue(_setup.spin(lambda: not page.banner.get_revealed(), 5))
        page.stop_button.emit("clicked")
        self.assertTrue(_setup.spin(lambda: not page.busy, 5))

    def test_a_recording_that_cannot_start_is_said_and_the_meters_come_back(self):
        self.engines.recorder_fails = Failure("There is not enough room on the disk for ten minutes of recording.")
        window = self.window()
        page = window.record
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            page.record_button.emit("clicked")
            self.assertTrue(_setup.spin(lambda: page.banner.get_revealed(), 5))
        self.assertIn("starting the recording: There is not enough room", said.getvalue())
        self.assertIn("not enough room", page.banner.get_title())
        self.assertIsNone(page.recorder)
        self.assertTrue(_setup.spin(lambda: page.monitor is not None, 5))
        self.assertTrue(page.record_button.get_sensitive())

    def test_closing_while_recording_asks_and_stopping_closes_the_files_first(self):
        window = self.window()
        self.app.window = window
        self.app.quit = mock.Mock()
        self.app.cfg.keep_running = False
        page, recorder = self.recording(window)
        window.close()
        self.assertTrue(window.get_visible())              # asked, not closed
        self.assertEqual(recorder.state, "recording")
        self.assertIsNotNone(self.app._quit_dialog)
        self.app._quit_dialog.emit("response", "quit")
        self.assertTrue(_setup.spin(lambda: self.app.exit_ready, 5))
        self.app.quit.assert_called_once()
        self.assertEqual(recorder.state, "stopped")
        self.assertEqual(len(self.engines.finished), 1)

    def test_without_gstreamer_the_page_says_so_and_transcribe_still_works(self):
        self.engines.missing_pieces = ["wasapi2src"]
        window = MainWindow(self.app)
        self.addCleanup(window.destroy)
        window.present()
        self.assertEqual(window.record.stack.get_visible_child_name(), "unavailable")
        self.assertEqual(self.engines.monitors, [])

    def test_the_microphone_s_voice_is_proposed_as_me(self):
        from scriba.app import jobs
        window = self.window()
        source = self.tmp / "call.mka"
        source.write_bytes(b"made up")
        made = fake_engines.made_result(self.tmp / "out", source, fake_engines.lines(3, 12, 0))
        made.me, made.names = "Speaker 2", {"Speaker 2": "Io"}
        window.show_result(made, jobs.Request(source))
        self.assertEqual(window.result.entries["Speaker 2"].get_text(), "Io")
        self.assertEqual(window.result.entries["Speaker 1"].get_text(), "")
        self.assertTrue(window.result.save_button.get_sensitive())


class RealWindowTest(unittest.TestCase):
    """The real engines, from the window: the first minute of the test clip."""

    @real("ffmpeg", "ffprobe", "whisper-cli", needs=(models.WHISPER_TURBO, models.SILERO_VAD, models.WESPEAKER),
          speech=True)
    def test_the_window_transcribes_the_first_minute_of_the_clip(self):
        from scriba.app.engines import audio, whisper
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = tmp / "clip.wav"
        audio.write(source, audio.read(AUDIO)[: 60 * audio.RATE])
        engines = backend.Backend(MODELS, tmp / "speeds.json")
        app = TestApp(str(tmp), engines)
        best = whisper.choose(engines.devices())
        app.cfg.measured_device = best.name if best is not None else "cpu"     # the cards' test is not this test's
        app.cfg.output_folder = str(tmp / "out")
        app.cfg.terms = "Cantiere Esempio, cavedi"
        window = MainWindow(app)
        self.addCleanup(window.destroy)
        window.present()
        window.open_path(str(source))
        page = window.transcribe
        self.assertTrue(_setup.spin(lambda: page.media is not None, 30))
        page.start_button.emit("clicked")
        self.assertTrue(_setup.spin(lambda: window.nav.get_visible_page() is window.result
                                    or page.stack.get_visible_child_name() == "failed", 600))
        self.assertIs(window.nav.get_visible_page(), window.result, page.failed_page.get_description())
        made = sorted(p.suffix for p in (tmp / "out").iterdir())
        self.assertEqual(made, [".json", ".md", ".srt", ".txt"])
        self.assertGreaterEqual(window.result.result.speakers, 1)
        self.assertTrue(window.result.result.segments)
        self.assertTrue(os.path.isfile(window.result.result.session))


@unittest.skipUnless(_setup.WINDOWS, "Windows only")
class FontTest(unittest.TestCase):
    def test_a_size_larger_and_following_windows(self):
        settings = Gtk.Settings.get_default()
        before = settings.props.gtk_font_name
        self.addCleanup(settings.reset_property, "gtk-font-name")
        settings.props.gtk_font_name = "Segoe UI 9"
        self.addCleanup(settings.disconnect, app_mod.larger_system_font())
        self.assertEqual(settings.props.gtk_font_name, "Segoe UI 10.5")
        settings.props.gtk_font_name = "Segoe UI 11"            # a larger text size chosen in Windows
        self.assertEqual(settings.props.gtk_font_name, "Segoe UI 13")
        settings.props.gtk_font_name = "Segoe UI Bold 9"
        self.assertEqual(settings.props.gtk_font_name, "Segoe UI Bold 10.5")
        self.assertTrue(before)

    def test_the_text_follows_the_screen_the_window_is_on(self):
        from scriba.app import win32
        from scriba.core import textscale
        settings = Gtk.Settings.get_default()
        self.addCleanup(settings.reset_property, "gtk-xft-dpi")
        settings.props.gtk_xft_dpi = -1             # GTK's default, as per monitor
        dpi, moved = [240], []
        with mock.patch.object(win32, "per_monitor_aware", return_value=True), \
                mock.patch.object(win32, "window_dpi", side_effect=lambda _hwnd: dpi[0]):
            stop = app_mod.text_follows_screen(lambda: None, lambda: moved.append(dpi[0]))
            self.addCleanup(stop)
            window = Gtk.Window(default_width=120, default_height=80)
            window.set_opacity(0.02)
            self.addCleanup(window.destroy)
            window.present()
            self.assertTrue(_setup.spin(window.get_mapped, 5))
            scale = window.get_scale_factor()
            self.assertEqual(settings.props.gtk_xft_dpi, textscale.text_dpi(-1, 240, scale))
            dpi[0] = 144                            # moved to a 150% screen
            window.notify("scale-factor")
            self.assertEqual(settings.props.gtk_xft_dpi, textscale.text_dpi(-1, 144, scale))
            self.assertEqual(moved, [240, 144])
            settings.props.gtk_xft_dpi = 110 * 1024     # someone else's dpi: kept, and multiplied
            self.assertEqual(settings.props.gtk_xft_dpi, textscale.text_dpi(110 * 1024, 144, scale))
            stop()
            dpi[0] = 240
            window.notify("scale-factor")
            self.assertEqual(settings.props.gtk_xft_dpi, textscale.text_dpi(110 * 1024, 144, scale))

    def test_a_system_aware_process_is_left_to_gdk(self):
        from scriba.app import win32
        settings = Gtk.Settings.get_default()
        before = settings.props.gtk_xft_dpi
        with mock.patch.object(win32, "per_monitor_aware", return_value=False), \
                mock.patch.object(win32, "window_dpi", return_value=240):
            stop = app_mod.text_follows_screen(lambda: None)
            window = Gtk.Window()
            self.addCleanup(window.destroy)
            window.set_opacity(0.02)
            window.present()
            self.assertTrue(_setup.spin(window.get_mapped, 5))
            stop()
        self.assertEqual(settings.props.gtk_xft_dpi, before)


class TitleBarColoursTest(unittest.TestCase):
    """What Windows' title bar is painted with (app/win32.py, title_bars):
    the window's own background and text, read from the style."""

    def test_the_windows_colours_follow_the_scheme(self):
        from scriba.gui.ui import window_colours
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        style = Adw.StyleManager.get_default()
        self.addCleanup(style.set_color_scheme, style.get_color_scheme())
        seen = {}
        for name, scheme in (("light", Adw.ColorScheme.FORCE_LIGHT), ("dark", Adw.ColorScheme.FORCE_DARK)):
            style.set_color_scheme(scheme)
            _setup.spin(lambda: False, 0.2)         # the style follows the scheme a moment later
            seen[name] = window_colours(window)
            for colour in seen[name]:
                self.assertEqual(len(colour), 3)
                self.assertTrue(all(isinstance(part, int) and 0 <= part <= 255 for part in colour), colour)
        (light_back, light_text), (dark_back, dark_text) = seen["light"], seen["dark"]
        self.assertGreater(sum(light_back), 600)        # a light background with dark text,
        self.assertLess(sum(light_text), 300)
        self.assertLess(sum(dark_back), 200)            # a dark one with light text
        self.assertGreater(sum(dark_text), 600)


class StyleSheetsTest(unittest.TestCase):
    """The style sheets (gui/style.css made from style/tokens.toml, then
    gui/app.css) are read by this GTK without a word."""

    def test_gtk_reads_the_apps_sheets(self):
        from gi.repository import Gdk

        from scriba.gui.ui import STYLE_SHEETS, load_style
        self.assertEqual(STYLE_SHEETS, ("style.css", "app.css"))
        self.assertEqual(load_style(Gdk.Display.get_default()), [])

    def test_what_gtk_refuses_is_said_with_file_and_line(self):
        from gi.repository import Gdk

        from scriba.gui.ui import load_style
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # classes no widget of the app has: the sheet stays on the display
        (folder / "style.css").write_text(
            ":root { --probe-m: 8px; }\n.probe-zone { border-radius: var(--probe-m); }\n"
            ".probe-broken { colour-of: nothing; }\n", encoding="utf-8")
        refused = load_style(Gdk.Display.get_default(), folder)
        self.assertEqual(len(refused), 2, refused)
        self.assertTrue(refused[0].startswith("style.css:3: "), refused)
        self.assertEqual(refused[1], "app.css: missing")


class ScreenshotTest(unittest.TestCase):
    """The developer aid the CI's smoke test relies on: the variables are
    named after the package."""

    def test_the_variables_are_the_apps_own(self):
        from scriba import ENV_PREFIX
        self.assertIn(f'"{ENV_PREFIX}_SCREENSHOT"', Path(app_mod.__file__).read_text(encoding="utf-8")
                      .replace("f\"{ENV_PREFIX}_SCREENSHOT\"", f'"{ENV_PREFIX}_SCREENSHOT"'))
        self.assertEqual(ENV_PREFIX, "SCRIBA")
        self.assertTrue(os.environ.get("LANGUAGE"))


class PlayCheckTest(unittest.TestCase):
    """The player of Listen tried muted (scriba check FILE)."""

    def test_a_recording_plays_muted_from_its_jump_and_a_broken_one_is_said(self):
        import numpy as np

        from scriba.app.engines import audio
        from scriba.gui.playcheck import JUMP, try_playing
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        wav = tmp / "call.wav"
        audio.write(wav, np.zeros(40 * audio.RATE, np.int16))
        played = try_playing(str(wav))
        self.assertTrue(played.prepared, played.error)
        self.assertAlmostEqual(played.duration, 40.0, delta=0.1)
        self.assertEqual(played.started, JUMP)
        self.assertGreater(played.reached, JUMP)
        # GTK clears the stream before its native playback worker necessarily
        # releases the Windows file handle. Exercise that completion instead
        # of racing TemporaryDirectory's synchronous cleanup.
        def release_finished():
            try:
                wav.unlink()
                return True
            except PermissionError:
                return False
        self.assertTrue(_setup.spin(release_finished, 5), "the player retained the recording after clear")
        broken = tmp / "broken.m4a"
        broken.write_bytes(b"not a recording at all")
        self.assertFalse(try_playing(str(broken), timeout=10).prepared)


if __name__ == "__main__":
    unittest.main()
