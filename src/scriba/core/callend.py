"""Combine observations of a call with its microphone and output activity."""

SILENCE_SECONDS = 2
# Teams confirmed the meeting gone (app/teams.py, "ended") but a call program
# still holds a microphone: Teams itself may open another one right after
# hanging up. The end is believed only after this much output silence.
CONFIRMED_SILENCE_SECONDS = 60
CALL_MARGIN = 10
MIN_OUTSIDE = 60


class CallEnd:
    def __init__(self):
        self.began = None
        self.seen = False
        self.absent = None
        self.last_present = 0.0
        self.confirmed_absent = False
        self.ended = None
        self.detected = None
        self.dismissed = False

    def _reset_end(self):
        self.ended = self.detected = None
        self.dismissed = False

    def teams(self, state, at):
        if state in ("ready", "minimized", "present"):
            if self.began is None:
                self.began = at
            if self.absent is not None or self.ended is not None:
                self._reset_end()
            self.seen, self.absent = True, None
            self.confirmed_absent = False
            self.last_present = at
        elif self.seen and state in ("ended", "no-meeting"):
            if self.absent is None:
                self.absent = at
            self.confirmed_absent |= state == "ended"
        elif state == "unavailable" and self.ended is None:
            self.absent = None
            self.confirmed_absent = False

    def update(self, at, open_mic, silent_since, output_playing):
        if self.ended is not None:
            if open_mic and output_playing:
                self._reset_end()
                self.seen, self.absent, self.confirmed_absent = False, None, False
            return
        if not self.seen and open_mic and output_playing and self.began is None:
            self.began = at
        if self.began is None or silent_since is None:
            return
        quiet = at - silent_since
        if not self.seen:
            believed = not open_mic and quiet >= SILENCE_SECONDS and silent_since > self.began
        elif self.absent is None:
            return
        elif open_mic:
            believed = self.confirmed_absent and quiet >= CONFIRMED_SILENCE_SECONDS
        else:
            believed = quiet >= SILENCE_SECONDS and (self.confirmed_absent or at - self.absent >= SILENCE_SECONDS)
        if believed:
            self.ended = max(self.last_present, silent_since)
            self.detected = at

    def interval(self, elapsed):
        """The first call through the last confirmed end, with room for greetings."""
        if self.began is None or self.ended is None or not self.began < self.ended <= elapsed:
            return None
        start = max(0.0, self.began - CALL_MARGIN)
        end = min(elapsed, self.ended + CALL_MARGIN)
        return (start, end) if start + elapsed - end >= MIN_OUTSIDE else None

    @property
    def blocks_follow(self):
        return self.absent is not None or self.ended is not None

    @property
    def prompt(self):
        return self.ended is not None and not self.dismissed

    def continue_call(self):
        self.dismissed = True

    def due(self, at, minutes):
        return bool(minutes and self.prompt and at - self.detected >= minutes * 60)
