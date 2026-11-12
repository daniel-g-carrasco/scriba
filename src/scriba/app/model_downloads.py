"""Application-owned model downloads, independent of any widget or toolkit.

One writer owns the queue. Pause preserves backend partial files and persists
only the user's choice; shutdown requests cancellation without turning an
involuntary exit into a persistent pause. Observers run through the caller's
dispatcher and can leave without cancelling a transfer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace

from . import models
from .backend import needed_models
from .log import complain
from .task import Cancelled


@dataclass(frozen=True)
class DownloadState:
    phase: str = "idle"
    done: int = 0
    total: int = 0
    model_key: str = ""
    error: str = ""


class ModelDownloads:
    def __init__(self, backend, cfg, save, *, dispatch, required=None):
        self.backend, self.cfg, self.save = backend, cfg, save
        self.required = tuple(needed_models() if required is None else required)
        self._dispatch = dispatch
        self._lock = threading.RLock()
        self._idle = threading.Event()
        self._idle.set()
        self._thread = None
        self._cancel = threading.Event()
        self._queue = []
        self._base = self._total = 0
        self._started = self._closed = False
        self._observers = []
        self._notification_pending = False
        self._state = DownloadState()

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def busy(self):
        with self._lock:
            return self._thread is not None

    @property
    def queued_keys(self):
        with self._lock:
            return tuple(model.key for model in self._queue)

    def subscribe(self, callback):
        with self._lock:
            self._observers.append(callback)
            self._notify()

        def unsubscribe():
            with self._lock:
                if callback in self._observers:
                    self._observers.remove(callback)
        return unsubscribe

    def _notify(self):
        if self._notification_pending:
            return
        self._notification_pending = True

        def deliver():
            with self._lock:
                self._notification_pending = False
                state, observers = self._state, tuple(self._observers)
            for callback in observers:
                callback(state)
        self._dispatch(deliver)

    def _change(self, **values):
        self._state = replace(self._state, **values)
        self._notify()

    def _enqueue(self, candidates):
        if self._thread is None and not self._queue:
            self._base = self._total = 0
        keys = {model.key for model in self._queue}
        for model in candidates:
            if model.key not in keys and not self.backend.has(model):
                self._queue.append(model)
                keys.add(model.key)
                self._total += model.size

    def start_required(self):
        with self._lock:
            if self._closed or self._started:
                return
            self._started = True
            self._enqueue(self.required)
            self._start()

    def download(self, model):
        with self._lock:
            if self._closed:
                return
            was_paused = self.cfg.models_download_paused
            self.cfg.models_download_paused = False
            self._enqueue((model,))
            self._change(total=self._total)
            self._start()
        if was_paused:
            self.save()

    def _start(self):
        if self._closed or self._thread is not None:
            return
        if not self._queue:
            self._change(phase="ready", done=self._total, total=self._total, model_key="", error="")
            return
        if self.cfg.models_download_paused:
            self._change(phase="paused", total=self._total, error="")
            return
        self._cancel = threading.Event()
        self._idle.clear()
        self._change(phase="connecting", total=self._total, error="")
        self._thread = threading.Thread(target=self._work, name="model-downloads", daemon=True)
        self._thread.start()

    def pause(self):
        with self._lock:
            if self._closed:
                return
            self.cfg.models_download_paused = True
            self._cancel.set()
            self._change(phase="stopping" if self._thread is not None else "paused", error="")
        self.save()

    def resume(self):
        with self._lock:
            if self._closed:
                return
            self.cfg.models_download_paused = False
            if not self._started:
                self._started = True
                self._enqueue(self.required)
            self._start()
        self.save()

    def shutdown(self):
        with self._lock:
            self._closed = True
            self._cancel.set()
            if self._thread is not None:
                self._change(phase="stopping")

    def wait(self, timeout=None):
        """For non-GUI callers/tests; GUI observes busy and never blocks on join."""
        return self._idle.wait(timeout)

    def _part_bytes(self, model):
        folder = getattr(self.backend, "models_dir", None)
        if folder is None:
            return 0
        try:
            return min(model.size, (folder / (model.filename + models.PART_SUFFIX)).stat().st_size)
        except OSError:
            return 0

    def _reporter(self, model, cancelled):
        owner = self

        class Reporter:
            current_phase = "connecting"

            def __call__(self, done, _total):
                with owner._lock:
                    if owner._cancel is cancelled and not cancelled.is_set():
                        owner._change(phase=self.current_phase if self.current_phase in ("checking", "verifying") else "downloading",
                                      done=owner._base + min(model.size, int(done)),
                                      total=owner._total, model_key=model.key)

            def phase(self, phase):
                self.current_phase = phase
                with owner._lock:
                    if owner._cancel is cancelled and not cancelled.is_set():
                        owner._change(phase=phase, model_key=model.key)
        return Reporter()

    def _after_cancel(self):
        with self._lock:
            if not self._closed and not self.cfg.models_download_paused:
                self._cancel = threading.Event()
                return True
            self._change(phase="paused" if self.cfg.models_download_paused else "idle", error="")
            return False

    def _work(self):
        try:
            while True:
                with self._lock:
                    if self._closed or self.cfg.models_download_paused:
                        self._change(phase="paused" if self.cfg.models_download_paused else "idle")
                        return
                    if not self._queue:
                        self._change(phase="ready", done=self._total, model_key="", error="")
                        return
                    model, cancelled = self._queue[0], self._cancel
                    partial = self._part_bytes(model)
                    self._change(phase="checking" if partial else "connecting", model_key=model.key,
                                 done=self._base + partial, total=self._total, error="")
                try:
                    self.backend.download(model, self._reporter(model, cancelled), cancelled)
                except Cancelled:
                    if self._after_cancel():
                        continue
                    return
                except Exception as error:
                    if cancelled.is_set():
                        if self._after_cancel():
                            continue
                        return
                    detail = getattr(error, "detail", "")
                    complain("downloading models" + (": " + detail if detail else ""), error)
                    with self._lock:
                        self._change(phase="error", error=str(error))
                    return
                with self._lock:
                    self._queue.pop(0)
                    self._base += model.size
        finally:
            with self._lock:
                self._thread = None
                self._idle.set()
                self._notify()
