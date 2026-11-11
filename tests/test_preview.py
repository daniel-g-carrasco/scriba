"""Safe voice excerpts, cancellation and tagged MP3 playback in a subprocess.

The subprocess matters: a GStreamer assertion terminates Python itself.
Everything played here is muted; sources are synthetic and unchanged.
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _setup

from scriba.app.engines import audio
from scriba.app.task import Cancelled, Failure
from scriba.core.speakers import Voice


def _child_play(source: Path):
    if _setup.WINDOWS:
        import ctypes
        ctypes.windll.kernel32.SetErrorMode.argtypes = [ctypes.c_uint]
        ctypes.windll.kernel32.SetErrorMode.restype = ctypes.c_uint
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)  # no error-reporting dialog after a native abort
    _setup.gtk_or_skip()
    import fake_engines
    from test_window import TestApp
    from scriba.app import jobs
    from scriba.core.segments import Segment
    from scriba.gui.window import MainWindow
    with tempfile.TemporaryDirectory() as tmp:
        app = TestApp(tmp)
        app.cfg.measured_device = fake_engines.CARD.name
        window = MainWindow(app)
        page = window.result
        page.muted = True
        made = fake_engines.made_result(Path(tmp), source, [Segment(1, 2.2, "Voice sample", "Speaker 1")])
        window.show_result(made, jobs.Request(source))
        page.listen(Voice("Speaker 1", 1, 1, (1, 2.2)))
        assert _setup.spin(lambda: page.media is not None and page.media.is_prepared(), 12), "player not ready"
        assert page.media.get_muted(), "the regression must never play aloud"
        # Let native preparation and playback run before inspecting the path.
        _setup.spin(lambda: False, 0.15)
        assert page.media is not None, "excerpt ended too soon"
        assert Path(page.media.get_file().get_path()).suffix == ".wav", "GTK received the tagged MP3"
        assert page.media.get_duration() / 1e6 < 1.5, "the whole source reached GTK"
        page.stop()
        # Let the native ended signal stop an excerpt shorter than the requested span.
        from gi.repository import GLib
        timeout_add = GLib.timeout_add
        with mock.patch.object(GLib, "timeout_add", lambda _ms, callback: timeout_add(10_000, callback)):
            page.listen(Voice("Speaker 1", 3.8, 1, (3.8, 12)))
            assert _setup.spin(lambda: page.media is not None, 12), "short excerpt not ready"
            assert page._clip.duration < 0.3, "source end was not respected"
            folder = page._clip.path.parent
            assert _setup.spin(lambda: page._playing is None, 4), "ended did not stop the short excerpt"
            assert _setup.spin(lambda: not folder.exists(), 4), "short excerpt was not removed"
        window.destroy()
    print("tagged MP3: muted WAV voice preview passed", flush=True)


def _child_close(source: Path):
    _setup.gtk_or_skip()
    from gi.repository import Adw, Gio, GLib
    import fake_engines
    from test_window import TestApp
    from scriba.app import jobs, preview
    from scriba.app.engines import ffmpeg, process
    from scriba.core.segments import Segment
    from scriba.gui.window import MainWindow

    children, folders, faults = [], [], []
    continue_cleanup = threading.Event()
    popen, mkdtemp = subprocess.Popen, tempfile.mkdtemp

    def started(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    def temporary(*args, **kwargs):
        folder = mkdtemp(*args, **kwargs)
        if kwargs.get("prefix", "").startswith("scriba-preview-"):
            folders.append(Path(folder))
        return folder

    with tempfile.TemporaryDirectory() as tmp:
        class CloseApp(TestApp):
            def do_startup(self):
                Adw.Application.do_startup(self)  # no Windows registrations, notifications or update network

            def do_activate(self):
                self.window = MainWindow(self)
                self.window.set_opacity(0.02)
                self.window.result.muted = True
                made = fake_engines.made_result(Path(tmp), source, [Segment(0, 2, "Voice", "Speaker 1")])
                self.window.show_result(made, jobs.Request(source))
                self.window.present()
                self.window.result.listen(Voice("Speaker 1", 0, 1, (0, 2)))

        app = CloseApp(tmp)
        app.set_application_id("io.github.scriba.previewtest")
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        app.cfg.measured_device = fake_engines.CARD.name
        requested_close = [False]

        def decoded(_source, dest, **kwargs):
            argv = ffmpeg.decode_arguments(str(ffmpeg.need("ffmpeg")), str(_source), str(dest), length=2)
            at = argv.index("-i")
            argv[at:at] = ["-readrate", "0.5"]

            def progress(line):
                if line.startswith("out_time_us=") and int(line.split("=", 1)[1]) > 0 and not requested_close[0]:
                    requested_close[0] = True
                    GLib.idle_add(lambda: app.window.close() or False)
            try:
                process.run(argv, cancelled=kwargs["cancelled"], on_stdout=progress)
            except Cancelled:
                # A deterministic handshake: cleanup needs one more turn of the main loop.
                GLib.idle_add(lambda: continue_cleanup.set() or False, priority=GLib.PRIORITY_LOW)
                continue_cleanup.wait(5)
                raise
            raise AssertionError("the decoder was not cancelled by close")

        def watchdog():
            faults.append("the application did not finish its preview workers")
            app.quit()
            return False

        timeout = GLib.timeout_add_seconds(12, watchdog)
        try:
            with mock.patch.object(subprocess, "Popen", started), mock.patch.object(preview.tempfile, "mkdtemp", temporary), \
                    mock.patch.object(ffmpeg, "decode", decoded):
                app.run(["preview-close-test"])
                assert requested_close[0], "the recording was not closed during decoding"
                assert not app.get_windows(), "an application window is still alive"
                assert continue_cleanup.is_set(), "the main loop exited before the cancelled worker could finish"
                assert all(child.poll() is not None for child in children), "a decoder outlived the window"
                assert folders and all(not folder.exists() for folder in folders), "preview directories were left behind"
                assert not faults, faults
        finally:
            GLib.source_remove(timeout)
            continue_cleanup.set()
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    child.wait(5)
            _setup.spin(lambda: all(not folder.exists() for folder in folders), 3)
    print("application close: decoder cancelled, no windows, processes or preview folders left", flush=True)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg")
class PreviewFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def test_a_nonzero_excerpt_has_the_right_samples_and_is_capped_at_twelve_seconds(self):
        import numpy as np
        from scriba.app import preview
        source = self.folder / "recording.wav"
        samples = np.concatenate([np.zeros(2 * audio.RATE, np.int16), np.full(16 * audio.RATE, 7000, np.int16)])
        audio.write(source, samples)
        before = source.read_bytes()
        clip = preview.prepare(source, 2, 30)
        try:
            got = audio.read(clip.path)
            self.assertEqual(len(got), 12 * audio.RATE)
            self.assertEqual(int(got[audio.RATE]), 7000)
            self.assertEqual(clip.duration, 12)
            self.assertEqual(source.read_bytes(), before)
        finally:
            clip.close()
        self.assertFalse(clip.path.parent.exists())

    def test_short_audio_finishes_at_its_real_end_and_empty_or_failed_clips_are_cleaned(self):
        import numpy as np
        from scriba.app import preview
        source = self.folder / "short.wav"
        audio.write(source, np.zeros(2 * audio.RATE, np.int16))
        clip = preview.prepare(source, 1.5, 12)
        self.assertAlmostEqual(clip.duration, 0.5, places=3)
        clip.close()
        mkdtemp = tempfile.mkdtemp
        with mock.patch.object(preview.tempfile, "mkdtemp", lambda **kw: mkdtemp(dir=self.folder, **kw)):
            with self.assertRaises(Failure):
                preview.prepare(source, 9, 12)
            with self.assertRaises(Failure):
                preview.prepare(self.folder / "missing.mp3", 0, 1)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["short.wav"])
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            preview.prepare(source, 0, 1, stop)

    def test_cancellation_never_removes_a_directory_while_the_decoder_is_writing(self):
        from scriba.app import preview
        started, release, cancel = threading.Event(), threading.Event(), threading.Event()
        paths, errors = [], []

        def writing(source, dest, **kwargs):
            paths.append(dest)
            with open(dest, "wb") as out:
                out.write(b"partial WAV")
                started.set()
                release.wait(3)
                raise Cancelled()

        def worker():
            try:
                preview.prepare(self.folder / "source.mp3", 0, 1, cancel)
            except Cancelled as error:
                errors.append(error)

        with mock.patch.object(preview.ffmpeg, "decode", writing):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(started.wait(3))
                cancel.set()
                self.assertTrue(paths[0].exists())
                self.assertTrue(paths[0].parent.exists())
            finally:
                release.set()
                thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertFalse(paths[0].parent.exists())

    def test_application_close_waits_for_decoder_cancellation_and_leaves_no_temporary_files(self):
        import numpy as np
        source = self.folder / "close-test.wav"
        audio.write(source, np.zeros(4 * audio.RATE, np.int16))
        # On Windows, NON_UNIQUE still lets GTK contact the desktop bus.
        # A stalled shared bus blocks before startup and the test's watchdog.
        # Isolate only this child: it tests worker lifetime, not desktop services.
        child_env = dict(os.environ)
        if _setup.WINDOWS:
            child_env["DBUS_SESSION_BUS_ADDRESS"] = "disabled:"
        done = subprocess.run([sys.executable, str(Path(__file__)), "--close", str(source)],
                              cwd=Path(__file__).parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                              env=child_env)
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace")[-4500:])
        self.assertIn(b"no windows, processes or preview folders left", done.stdout)

    def test_tagged_mp3_is_played_muted_in_a_child_process_and_the_source_is_untouched(self):
        from test_engines import run_ffmpeg
        source = self.folder / "voice with ID3.mp3"
        run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-metadata", "title=Voice preview",
                   "-id3v2_version", "3", "-c:a", "libmp3lame", str(source))
        before = source.read_bytes()
        self.assertTrue(before.startswith(b"ID3"))
        done = subprocess.run([sys.executable, str(Path(__file__)), "--play", str(source)],
                              cwd=Path(__file__).parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace")[-4500:])
        self.assertIn(b"muted WAV voice preview passed", done.stdout)
        self.assertEqual(source.read_bytes(), before)


class PreviewRaceTest(unittest.TestCase):
    def setUp(self):
        _setup.gtk_or_skip()
        import fake_engines
        from test_window import TestApp
        from scriba.app import jobs
        from scriba.gui.window import MainWindow
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.app = TestApp(str(self.folder))
        self.app.cfg.measured_device = fake_engines.CARD.name
        self.window = MainWindow(self.app)
        self.addCleanup(self.window.destroy)
        self.page = self.window.result
        self.page.muted = True
        source = self.folder / "source.mp3"
        source.write_bytes(b"made up")
        made = fake_engines.made_result(self.folder, source, fake_engines.lines(2, 4, 0))
        self.window.show_result(made, jobs.Request(source))
        self.pending = []

        def start(work, done):
            self.pending.append((work, done))
        # Deliver preview and cleanup callbacks in a controlled order.
        import scriba.gui.result as result_ui
        self.enterContext(mock.patch.object(result_ui, "run_in_thread", start))

    def clip(self, name):
        import numpy as np
        from scriba.app.preview import Clip
        folder = self.folder / name
        folder.mkdir()
        path = folder / "voice.wav"
        audio.write(path, np.zeros(audio.RATE, np.int16))
        return Clip(path, 1)

    def drain_cleanup(self):
        # The cleanup callable is run only after its worker/GTK reader has ended.
        while self.pending:
            work, done = self.pending.pop(0)
            done(work(), None)

    def test_old_callbacks_cannot_replace_the_new_voice_or_remove_its_clip(self):
        a, b = Voice("Speaker 1", 0, 1, (0, 1)), Voice("Speaker 2", 1, 1, (1, 2))
        self.page.listen(a)
        _work_a, done_a = self.pending.pop()
        self.page.listen(b)
        _work_b, done_b = self.pending.pop()
        clip_b, clip_a = self.clip("b"), self.clip("a")
        done_b(clip_b, None)
        done_a(clip_a, None)
        self.drain_cleanup()
        self.assertEqual(self.page._playing, "Speaker 2")
        self.assertEqual(Path(self.page.media.get_file().get_path()), clip_b.path)
        self.assertFalse(clip_a.path.exists())
        self.assertTrue(clip_b.path.exists())
        self.page.stop()
        self.drain_cleanup()
        self.assertFalse(clip_b.path.exists())

    def test_stop_then_same_voice_invalidates_the_first_request_and_hide_or_close_cancel_pending_work(self):
        a = Voice("Speaker 1", 0, 1, (0, 1))
        self.page.listen(a)
        _work_old, done_old = self.pending.pop()
        self.page.stop()
        self.page.listen(a)
        _work_new, done_new = self.pending.pop()
        newest = self.clip("newest")
        done_new(newest, None)
        old = self.clip("old")
        done_old(old, None)
        self.drain_cleanup()
        self.assertTrue(newest.path.exists())
        self.assertFalse(old.path.exists())
        self.page.emit("hidden")
        self.drain_cleanup()
        self.assertIsNone(self.page.media)
        self.assertFalse(newest.path.exists())
        self.page.listen(a)
        _work_close, done_close = self.pending.pop()
        self.assertFalse(self.window._on_close())
        self.window.destroy()
        late = self.clip("after-close")
        done_close(late, None)
        self.drain_cleanup()
        self.assertIsNone(self.page.media)
        self.assertFalse(late.path.exists())

    def test_stop_during_decode_sets_cancellation_and_an_error_is_said_without_a_player(self):
        a = Voice("Speaker 1", 0, 1, (0, 1))
        received = []

        def prepare(source, start, end, cancelled):
            received.append(cancelled)
            return self.clip("decoder")

        self.enterContext(mock.patch.object(self.app.backend, "preview", prepare))
        self.page.listen(a)
        work, done = self.pending.pop()
        self.page.stop()
        clip = work()  # a worker that ignored cancellation and finished late
        self.assertTrue(received[0].is_set())
        done(clip, None)
        self.drain_cleanup()
        self.assertFalse(clip.path.exists())
        self.page.listen(a)
        _work, done = self.pending.pop()
        said = []
        with mock.patch.object(self.window, "toast", said.append), contextlib.redirect_stderr(io.StringIO()):
            done(None, Failure("The audio could not be decoded.", "ffmpeg failed"))
        self.assertIn("could not be decoded", said[0])
        self.assertIsNone(self.page.media)
        self.assertIsNone(self.page._playing)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--play":
        _child_play(Path(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--close":
        _child_close(Path(sys.argv[2]))
    else:
        unittest.main()
