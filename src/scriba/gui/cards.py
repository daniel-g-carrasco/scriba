"""The test of the graphics cards, as the window runs it: on the app's own sample of
speech, as soon as the models are there (after their download, or at a
start with the models there and no test made yet), without waiting for a
recording of the user's; and again whenever the user asks (Test Again, in
the preferences). One test at a time; a transcription started meanwhile
stops it, since the work wants the card.

The window works nothing out here either: the test itself is the
backend's (app/jobs.py, try_devices), in a thread; this object only
starts it, stops it, and says "changed" (a GObject signal) to the pages
that show its outcome: the Graphics Card row of Transcribe, the
preferences.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject  # noqa: E402

from scriba.app import jobs, models  # noqa: E402
from scriba.app.task import Cancelled  # noqa: E402

from .ui import report, run_in_thread  # noqa: E402


class CardTest(GObject.Object):
    __gtype_name__ = "ScribaCardTest"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, app, backend) -> None:
        super().__init__()
        self.app, self.backend = app, backend
        self.running: threading.Event | None = None
        self.failed = ""                    # why the last test failed, this session
        self.notes: list[str] = []          # what the last test said past its choice (jobs.choice_notes)
        run_in_thread(self.backend.test_notes, self._notes_read)

    def _changed(self) -> None:
        self.emit("changed")

    def _notes_read(self, notes, error) -> None:
        if getattr(self.app, "closing", False):
            return
        if error is not None:
            report("reading the last test of the graphics cards", error)
        elif self.running is None:
            self.notes = list(notes or [])
            self._changed()

    @property
    def needed(self) -> bool:
        """Never tested, nothing chosen by hand, and the model to test with there."""
        cfg = self.app.cfg
        return not (cfg.device or cfg.measured_device) and self.backend.has(models.WHISPER_TURBO)

    def start_if_needed(self) -> None:
        if not getattr(self.app, "closing", False) and self.needed and self.running is None and not self.failed:
            self.start()

    def start(self) -> bool:
        """The test, now (Test Again); False when one runs already or the
        model is not there."""
        if (getattr(self.app, "closing", False) or self.app.work_busy
                or self.running is not None or not self.backend.has(models.WHISPER_TURBO)):
            return False
        cancel = self.running = threading.Event()
        self.failed = ""
        self._changed()
        try:
            run_in_thread(lambda: self.backend.try_devices(cancel), lambda choice, error: self._done(cancel, choice, error))
        except Exception as error:
            self._done(cancel, None, error)
            return False
        return True

    def stop(self) -> None:
        if self.running is not None and not self.running.is_set():
            self.running.set()
            self._changed()

    def _done(self, cancel: threading.Event, choice, error) -> None:
        if self.running is not cancel:
            return                          # an obsolete callback cannot finish another worker
        self.running = None
        if cancel.is_set() or getattr(self.app, "closing", False):
            self._changed()
            return                          # cancellation can race a successful backend return
        if error is not None:
            if not isinstance(error, Cancelled):
                report("testing the graphics cards", error)
                self.failed = str(error)
        else:
            self.app.cfg.measured_device = choice.device
            self.app.save_config()
            self.notes = jobs.choice_notes(choice)
        self._changed()
