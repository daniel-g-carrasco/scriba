"""What tests/test_strati.py must know of Scriba, the one part of the
layers' check that is the app's own (no fingerprint). test_strati.py is the
model's file, the same in every app, and reads these names. The shared
modules are not listed here: they are the manifest's (impronte/comune.json).
"""

# The toolkit, imported in gui/ only.
TOOLKIT = ("gi",)

# The parts of the toolkit's library that show nothing, which app/ may
# import (and the bare package with them). Scriba's recorder drives
# GStreamer pipelines through gi without windows, in app/recorder.py; the
# echo canceller (WebRTC's, a GStreamer plugin: it takes the computer's
# sound out of the microphone's track after the recording) in
# app/engines/echo.py. The meeting commands use GLib to find the system's
# Music folder when the user has left the recordings preference empty.
WINDOWLESS: tuple[str, ...] = ("gi.repository.Gst", "gi.repository.GLib")

# The files of app/ that may use WINDOWLESS.
WINDOWLESS_FILES: tuple[str, ...] = ("app/recorder.py", "app/engines/echo.py", "app/meetings.py")

# "file -> what it imports": "why it is still there, and when it goes".
# The list can only get shorter.
DEBTS: dict[str, str] = {}

# gui modules that do not import the toolkit, "file": "why it stays in gui".
# Frozen: none may be added (plain logic belongs in core, data or app).
GUI_WITHOUT_TOOLKIT: dict[str, str] = {}

# gui modules that are values and nothing else: the style's, written by
# tools/make_style.py.
GUI_VALUES: set[str] = {"gui/style_values.py"}
