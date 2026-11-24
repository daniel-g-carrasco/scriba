"""Stream the two prepared roles through one uninterrupted audio pipeline."""

from __future__ import annotations

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from scriba.app.preview import BalanceClip  # noqa: E402
from scriba.app.recording import PREVIEW_RATE  # noqa: E402

from .playback import Playback  # noqa: E402

BUFFER_FRAMES = PREVIEW_RATE // 100


class BalancePlayback(Playback):
    def __init__(self, clip: BalanceClip, balance: float, muted: bool, finished):
        super().__init__(
            f"appsrc name=pcm format=time max-bytes={BUFFER_FRAMES * 4} "
            f"! audio/x-raw,format=F32LE,rate={PREVIEW_RATE},channels=1,layout=interleaved",
            muted, finished,
        )
        self._clip = clip
        self.balance = self._previous = balance
        self._position = 0
        self._source = self._pipeline.get_by_name("pcm")
        self._need_data = self._source.connect("need-data", self._supply_audio)

    def close(self) -> None:
        super().close()
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
