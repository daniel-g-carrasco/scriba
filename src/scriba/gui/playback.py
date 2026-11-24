"""Own a GStreamer audio pipeline from the main loop."""

from __future__ import annotations

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402


class Playback:
    def __init__(self, chain: str, muted: bool, finished):
        if not Gst.is_initialized():
            Gst.init(None)
        self._finished = finished
        output = "fakesink sync=true" if muted else "autoaudiosink"
        self._pipeline = Gst.parse_launch(f"{chain} ! audioconvert ! audioresample ! {output} name=output")
        self._bus = self._pipeline.get_bus()
        self._bus.add_signal_watch()
        self._error_id = self._bus.connect("message::error", self._error)
        self._end_id = self._bus.connect("message::eos", self._end)

    def play(self) -> None:
        if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            message = self._bus.pop_filtered(Gst.MessageType.ERROR)
            if message is not None:
                raise RuntimeError(message.parse_error()[0].message)
            raise RuntimeError("GStreamer could not start")

    def close(self) -> None:
        self._bus.disconnect(self._error_id)
        self._bus.disconnect(self._end_id)
        self._bus.remove_signal_watch()
        # Transitioning to NULL closes the source even during preroll.
        self._pipeline.set_state(Gst.State.NULL)

    def _error(self, _bus, message) -> None:
        self._finished(message.parse_error()[0].message)

    def _end(self, _bus, _message) -> None:
        self._finished(None)


class VoicePlayback(Playback):
    def __init__(self, clip, muted: bool, finished):
        super().__init__("filesrc name=voice ! wavparse", muted, finished)
        self._pipeline.get_by_name("voice").set_property("location", str(clip.path))
