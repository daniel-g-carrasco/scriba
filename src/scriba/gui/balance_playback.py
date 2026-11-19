"""Stream the two prepared roles through one uninterrupted audio pipeline."""

from __future__ import annotations

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from scriba.app.preview import BalanceClip  # noqa: E402
from scriba.app.recording import PREVIEW_RATE  # noqa: E402

BUFFER_FRAMES = PREVIEW_RATE // 100


class BalancePlayback:
    def __init__(self, clip: BalanceClip, balance: float, muted: bool, finished):
        if not Gst.is_initialized():
            Gst.init(None)
        self._clip = clip
        self.balance = self._previous = balance
        self._position = 0
        self._finished = finished
        output = "fakesink sync=true" if muted else "autoaudiosink"
        self._pipeline = Gst.parse_launch(
            f"appsrc name=pcm format=time max-bytes={BUFFER_FRAMES * 4} "
            f"! audio/x-raw,format=F32LE,rate={PREVIEW_RATE},channels=1,layout=interleaved "
            f"! audioconvert ! audioresample ! {output}"
        )
        self._source = self._pipeline.get_by_name("pcm")
        self._need_data = self._source.connect("need-data", self._supply_audio)
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
        self._pipeline.set_state(Gst.State.NULL)
        self._source.disconnect(self._need_data)

    def _supply_audio(self, source, _requested) -> None:
        # The pipeline's streaming thread asks for the next short buffer.
        # A slider move changes only the target for future samples.
        start = self._position
        if start == self._clip.frames:
            source.emit("end-of-stream")
            return
        end = min(start + BUFFER_FRAMES, self._clip.frames)
        balance = self.balance
        samples = self._clip.render(start, end, self._previous, balance)
        self._previous, self._position = balance, end
        buffer = Gst.Buffer.new_wrapped(samples.tobytes())
        buffer.pts = start * Gst.SECOND // PREVIEW_RATE
        buffer.duration = end * Gst.SECOND // PREVIEW_RATE - buffer.pts
        source.emit("push-buffer", buffer)

    def _error(self, _bus, message) -> None:
        self._finished(message.parse_error()[0].message)

    def _end(self, _bus, _message) -> None:
        self._finished(None)
