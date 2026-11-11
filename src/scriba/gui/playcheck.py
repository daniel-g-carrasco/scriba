"""The diagnostic raw-file player behind `scriba check FILE`, always muted.

This deliberately tests GTK/GStreamer's native input support in a build,
including its codecs, seeking and plugin bundle. ResultPage's voice
previews instead decode a bounded WAV with ffmpeg before reaching GTK;
this raw diagnostic is not their playback path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

JUMP = 30.0                 # seconds: where Listen would start in a call
PLAY = 1.5                  # seconds played


@dataclass(frozen=True)
class Played:
    prepared: bool
    seekable: bool
    duration: float         # seconds, as the player says
    started: float          # where it was sent
    reached: float          # where it was after PLAY seconds of playing
    error: str = ""


def _spin(until, seconds: float) -> bool:
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if until():
            return True
        context.iteration(False)
        time.sleep(0.005)
    return until()


def try_playing(path: str, timeout: float = 15.0) -> Played:
    Gtk.init()
    media = Gtk.MediaFile.new_for_filename(str(path))
    media.set_muted(True)
    media.set_volume(0.0)
    try:
        if not _spin(lambda: media.is_prepared() or media.get_error() is not None, timeout):
            return Played(False, False, 0.0, 0.0, 0.0, "not ready in time")
        if media.get_error() is not None:
            return Played(False, False, 0.0, 0.0, 0.0, media.get_error().message)
        duration = media.get_duration() / 1_000_000
        start = JUMP if duration > JUMP + PLAY + 1 else 0.0
        if start:
            media.seek(int(start * 1_000_000))
            _spin(lambda: not media.is_seeking(), timeout)
        media.play()
        _spin(lambda: media.get_error() is not None, PLAY)
        media.pause()
        error = media.get_error().message if media.get_error() is not None else ""
        return Played(True, media.is_seekable(), duration, start, media.get_timestamp() / 1_000_000, error)
    finally:
        media.set_playing(False)
        media.clear()
