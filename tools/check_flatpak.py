#!/usr/bin/env python3
"""Check the packaged runtime without models, devices, audio playback or network.

Run this with the installed Platform, not only during the SDK build:
    flatpak run --command=python3 APP_ID /app/share/scriba/check_flatpak.py
"""

import os
import subprocess
import sys

sys.path.insert(0, "/app/share/scriba")


def main():
    import numpy
    import scipy
    import sklearn

    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gst", "1.0")
    from gi.repository import Adw, Gst, Gtk

    from scriba.app.engines import bundle, ffmpeg, onnx, whisper
    Gst.init(None)
    for name in ("pulsesrc", "flacenc", "wavparse", "matroskamux"):
        assert Gst.ElementFactory.find(name), f"GStreamer element is missing: {name}"
    # The echo canceller (webrtcdsp, from the runtime's gst-plugins-bad and
    # webrtc-audio-processing) is said, not required: without it the app
    # uses the raw microphone and says so. Not yet seen in GNOME 50's runtime.
    from scriba.app.engines import echo
    lacking = echo.missing()
    print(f"echo canceller: {'missing ' + ', '.join(lacking) if lacking else 'present'}", flush=True)
    api = onnx.api()
    assert api.version.startswith("1.29."), f"Unexpected ONNX Runtime: {api.version}"
    assert whisper.find() and bundle.program("whisper-bench"), "whisper.cpp programs are missing"
    subprocess.run([str(whisper.find()), "--help"], check=True, capture_output=True)
    licence = subprocess.run([str(ffmpeg.find()), "-hide_banner", "-L"],
                             check=True, capture_output=True, text=True).stdout
    assert "Lesser General Public License" in licence.replace("\n", " "), licence
    assert "General Public License" in licence, licence
    for name, version in (("Python", sys.version.split()[0]), ("numpy", numpy.__version__),
                          ("scipy", scipy.__version__), ("scikit-learn", sklearn.__version__),
                          ("ONNX Runtime", api.version), ("GTK", Gtk.get_major_version()),
                          ("libadwaita", Adw.get_major_version())):
        print(f"{name}: {version}", flush=True)
    print(f"ffmpeg: {ffmpeg.find()}; whisper-cli: {whisper.find()}; ONNX: {onnx.find_library()}")
    assert os.path.isfile("/.flatpak-info"), "Run this check inside Flatpak"


if __name__ == "__main__":
    main()
