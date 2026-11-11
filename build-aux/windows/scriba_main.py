"""Entry point of the Windows build (PyInstaller freezes this file).

Before anything else, GStreamer is told to use the plugins the build
carries and those only (the recorder, and GTK's player behind Listen):
PyInstaller's own runtime hook points GST_PLUGIN_PATH at the whole
library folder too (GStreamer would open every DLL there to look for
plugins) and keeps the plugins' registry inside the program's folder,
which an installation for all users cannot write. Here the plugins are
the build's gst_plugins folder alone, the registry is the user's own
(in the app's cache, one per version: a registry another GStreamer wrote,
MSYS2's for one, lists other plugins), and whatever the user's
environment says of GStreamer is not listened to.

The build's command line is the same program: `Scriba.exe transcribe ...`
works without a window. Scriba.exe is a windowed program, which Windows
starts with no console: when it is started from one with a command, the
output goes back to that console (AttachConsole); scriba-cli.exe, the
console twin of the same build, is the one for scripts that wait for the
end and read the output.
"""

import os
import sys


def own_gstreamer(base: str) -> None:
    from scriba import __version__
    from scriba.app.services import app_paths
    cache = app_paths().cache_folder            # %LOCALAPPDATA%\Scriba\cache, or XDG_CACHE_HOME's
    cache.mkdir(parents=True, exist_ok=True)
    for name in ("GST_PLUGIN_PATH", "GST_PLUGIN_PATH_1_0", "GST_PLUGIN_SYSTEM_PATH_1_0", "GST_PLUGIN_SCANNER",
                 "GST_PLUGIN_SCANNER_1_0", "GST_REGISTRY_1_0", "GST_PLUGIN_LOADING_WHITELIST"):
        os.environ.pop(name, None)
    os.environ["GST_PLUGIN_SYSTEM_PATH"] = os.path.join(base, "gst_plugins")
    os.environ["GST_REGISTRY"] = str(cache / f"gstreamer-registry-{__version__}.bin")
    os.environ["GST_REGISTRY_FORK"] = "no"          # no plugin scanner program: the scan runs in the process


def console_streams() -> None:
    """A windowed program has no streams: for a command typed in a
    console, the console it was typed in; else nowhere (a print must not
    fail)."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    attached = False
    if len(sys.argv) > 1 and os.name == "nt":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AttachConsole.argtypes = (ctypes.c_ulong,)
        kernel32.AttachConsole.restype = ctypes.c_int
        attached = bool(kernel32.AttachConsole(0xFFFFFFFF))     # ATTACH_PARENT_PROCESS
    target = "CONOUT$" if attached else os.devnull
    if sys.stdout is None:
        sys.stdout = open(target, "w", encoding="utf-8", errors="replace")
    if sys.stderr is None:
        sys.stderr = open(target, "w", encoding="utf-8", errors="replace")


if __name__ == "__main__":             # tests/test_bundle.py imports the functions above
    console_streams()
    own_gstreamer(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    from scriba.__main__ import main
    sys.exit(main())
