#!/usr/bin/env python3
"""The README's pictures: the window driven through its two tabs with
made-up data (tools/screenshots.py's machinery and tests/fake_engines.py),
frames for two GIFs and the still pictures of the gallery.

    python tools/readme_pictures.py --out FOLDER --scene NAME [--scale 2] [--dark]

Scenes: record (the Record tab at rest, recording with its levels moving,
paused), transcribe (a recording opened, the work going, the result, the
whole transcript), models, preferences. One scene a run: the made-up files
land in $HOME/Music/Scriba, so run it with a home of its own (a throwaway
one, in English, as the pictures show the folders), on a display of its own;
tools/readme_pictures.sh does both.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import screenshots  # noqa: E402  (puts src/ and tests/ on the path)


class Pictures(screenshots.Shots):
    def app(self, engines_kwargs=None, cfg=None):
        """As the Shots' own, with the made-up recordings in the home's Music/Scriba."""
        app, _tmp = super().app(engines_kwargs, cfg)
        music = Path.home() / "Music" / "Scriba"
        music.mkdir(parents=True, exist_ok=True)
        app.cfg.recordings_folder = str(music)
        return app, music

    def frame(self, widget, scene: str) -> None:
        count = len([p for p in self.written if p.name.startswith(scene + "-")])
        self.photograph(widget, f"{scene}-{count:02}")

    # -- the scenes ------------------------------------------------------------------

    def record(self):
        window = self._record()
        self.frame(window, "record")
        page = window.record
        page.record_button.emit("clicked")
        self.spin(lambda: page.recorder is not None)
        recorder = page.recorder
        steps = [(1, (-30, -14), (-60, -40)), (2, (-24, -9), (-50, -30)), (3, (-40, -26), (-22, -8)),
                 (4, (-45, -30), (-18, -6)), (5, (-26, -10), (-35, -20)), (6, (-20, -7), (-55, -38))]
        for elapsed, me, others in steps:
            recorder.elapsed = 754.0 + elapsed
            recorder.levels = {"me-1": me, "others-2": others, "others-3": (-700.0, -350.0)}
            self.settle(0.35)
            self.frame(window, "record")
        recorder.state = "paused"
        self.settle(0.3)
        self.frame(window, "record")
        recorder.state = "stopped"
        window.destroy()

    def transcribe(self):
        import threading

        import fake_engines

        from scriba.app import jobs
        from scriba.app.config import Config
        from scriba.app.engines import ffmpeg
        cfg = Config(measured_device=fake_engines.CARD.name, subtitled_video=True, speaker_count=3,
                     terms="Cantiere Esempio, cavedi, montacarichi, UPS")
        app, tmp = self.app({"media": ffmpeg.Media(3725.0, 1, True, 0)}, cfg)
        window = self.window(app)
        self.frame(window, "transcribe")
        source = self.recording(tmp, "2026-11-11 Riunione di coordinamento impianti.mkv")
        window.open_path(str(source))
        self.spin(lambda: window.transcribe.media is not None)
        self.settle()
        self.frame(window, "transcribe")
        made = fake_engines.made_result(tmp / "Registrazioni", source, fake_engines.lines(3, 24, 2),
                                        duration=3725.0, total=462.0, device=fake_engines.CARD.label)
        hold = threading.Event()
        app.backend.runner = fake_engines.runner(lambda r: made, hold=hold)
        window.transcribe.start()
        self.spin(lambda: window.transcribe.job is not None and window.transcribe.job[0].stage == "transcribe")
        self.settle(0.8)
        self.frame(window, "transcribe")
        hold.set()
        self.spin(lambda: window.nav.get_visible_page() is window.result, 15)
        for label, text in (("Speaker 2", "Giovanni De Santis"), ("Speaker 3", "Eleonora Fumagalli")):
            if label in window.result.entries:
                window.result.entries[label].set_text(text)
        self.settle(0.8)
        self.frame(window, "transcribe")
        window.result.read_all()
        self.spin(lambda: window.nav.get_visible_page() is window.result.transcript)
        self.settle(0.8)
        self.frame(window, "transcribe")
        window.destroy()
        del jobs

    def models(self):
        self.models_downloading()

    def preferences(self):
        super().preferences()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The README's pictures, with made-up data.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scene", required=True, choices=("record", "transcribe", "models", "preferences"))
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--dark", action="store_true")
    args = parser.parse_args(argv)
    os.environ["LANGUAGE"] = "en"
    from scriba import PACKAGE_DIR, TEXTDOMAIN, i18n
    i18n.setup(TEXTDOMAIN, PACKAGE_DIR.parent.parent, "en")
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk
    Gtk.init()
    Adw.init()
    Gtk.Settings.get_default().props.gtk_enable_animations = False    # nothing drives them without a viewer
    from scriba.gui import application as app_mod
    from scriba.gui.ui import load_style
    load_style(Gdk.Display.get_default())
    if app_mod.SOURCE_ICONS.is_dir():
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(app_mod.SOURCE_ICONS))
    Adw.StyleManager.get_default().set_color_scheme(
        Adw.ColorScheme.FORCE_DARK if args.dark else Adw.ColorScheme.FORCE_LIGHT)
    args.out.mkdir(parents=True, exist_ok=True)
    pictures = Pictures(args.out, args.scale, 1.0)
    pictures.data = Path(tempfile.mkdtemp(prefix="scriba-pictures-"))
    getattr(pictures, args.scene)()
    for path in pictures.written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
