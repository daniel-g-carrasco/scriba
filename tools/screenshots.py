#!/usr/bin/env python3
"""The window photographed in each of its states, with made-up data (no
engine, no network: tests/fake_engines.py), to be looked at before a page
is called done (the template's docs/01-filosofia.md, "Come si presenta").

    python tools/screenshots.py [--out FOLDER] [--lang it|en] [--scale 2] [--only NAME...]

One PNG per state in FOLDER (by default ./screenshots): the models missing, Transcribe
empty, Transcribe with a video chosen, the work going, the Result in the
normal case (3 voices) and in the long one (8 voices with long names, 6
files, 40 doubtful lines; three pictures down the page), the test of the
cards going and a faster card left out for its wrong words (with the
preferences that say it), the preferences,
a page in the dark theme; Record at rest (and with the microphone's
devices open), recording with its levels, with a warning, and the long
case (seven devices with long names, both sides open); the whole transcript on its
page (top, middle, dark); the preferences' files of terms (one too long to
fit whole, one missing; light and dark); the Result of a transcript largely
doubtful, with its banner (light and dark). Each window is opened, drawn, photographed and
destroyed by the script, almost transparent while it is on the screen
(the picture is GTK's own drawing of the content: Gtk.WidgetPaintable, as
SCRIBA_SCREENSHOT does), so as not to cover what the user is doing; no
pointer, no keyboard. Run it from MSYS2 UCRT64 without MSYSTEM, as the
tests (`env -u MSYSTEM python tools/screenshots.py`).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

STATES = ("models", "models-downloading", "empty", "setup-video", "cards-testing", "cards-left-out", "working", "result", "result-long",
          "preferences", "dark", "record", "record-going", "record-warning", "record-long", "balance", "transcript",
          "terms", "result-doubtful")
GHOST = 0.02            # the window's opacity on the screen while it is drawn and photographed


def main(argv=None) -> int:
    default = ROOT / "screenshots"
    parser = argparse.ArgumentParser(description="Photograph the window's states with made-up data.")
    parser.add_argument("--out", type=Path, default=default)
    parser.add_argument("--lang", default="it", choices=("it", "en"))
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--only", nargs="*", choices=STATES, default=None)
    parser.add_argument("--visible", action="store_true", help="the windows fully opaque on the screen")
    args = parser.parse_args(argv)
    os.environ["LANGUAGE"] = args.lang
    if os.name == "nt":
        os.environ.setdefault("GTK_CSD", "0")       # as the app: the system's title bar, not GTK's
        from scriba.app import win32
        win32.per_monitor_dpi()                     # as the app: each screen's dpi, before GTK starts

    from scriba import PACKAGE_DIR, TEXTDOMAIN, i18n
    i18n.setup(TEXTDOMAIN, PACKAGE_DIR.parent.parent, args.lang)
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk

    import locale
    before = locale.setlocale(locale.LC_ALL)
    Gtk.init()
    locale.setlocale(locale.LC_ALL, before)
    Adw.init()
    from scriba import WINDOWS
    from scriba.gui import application as app_mod
    from scriba.gui.ui import load_style
    if WINDOWS:
        app_mod.larger_system_font()            # as the app starts: the text a size larger on Windows
        app_mod.text_follows_screen(lambda: None)   # and as large as the screen's scale asks
    for refused in load_style(Gdk.Display.get_default()):
        print(f"style: {refused}", file=sys.stderr)
    if app_mod.SOURCE_ICONS.is_dir():
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(app_mod.SOURCE_ICONS))

    # the light theme, whatever the desktop's: the dark one has a picture of its own
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    args.out.mkdir(parents=True, exist_ok=True)
    shots = Shots(args.out, args.scale, 1.0 if args.visible else GHOST)
    try:
        for state in args.only or STATES:
            getattr(shots, state.replace("-", "_"))()
    finally:
        import shutil
        shutil.rmtree(shots.data, ignore_errors=True)
    for path in shots.written:
        print(path)
    return 0


class Shots:
    def __init__(self, out: Path, scale: float, opacity: float):
        self.out, self.scale, self.opacity = out, scale, opacity
        self.data = out / "_dati"           # the made-up recordings and their files, for as long as the script runs
        self.written: list[Path] = []

    # -- the machinery ---------------------------------------------------------------

    def spin(self, cond, timeout: float = 10.0) -> bool:
        import _setup
        return _setup.spin(cond, timeout)

    def app(self, engines_kwargs=None, cfg=None):
        import fake_engines

        from scriba.app.config import Config
        from scriba.app.paths import Paths
        from scriba.gui import application as app_mod

        from scriba.app import jobs
        # the made-up files next to the pictures (their folder shows in the
        # window), thrown away at the end
        self.data.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="state-", dir=self.data))

        class ShotApp(app_mod.App):
            def __init__(self):
                super().__init__()
                self.paths = Paths(tmp / "config", tmp / "state", tmp / "data")
                self.cfg = cfg or Config()
                # the made-up recordings next to the pictures, never in the user's Music
                self.cfg.recordings_folder = str(tmp / "Musica" / "Scriba")
                self.backend = fake_engines.FakeBackend(tmp, **(engines_kwargs or {}))

            def save_config(self):
                pass

        app = ShotApp()
        # the speeds the test of the cards would have kept (11/11/2026's)
        jobs.remember_speeds(app.backend.speeds_file, {jobs.speed_key("turbo", fake_engines.CARD.label): 17.5,
                                                       jobs.speed_key("speakers", "cpu"): 23.0})
        return app, tmp

    def window(self, app, width: int = 640, height: int = 820, view: str = "transcribe"):
        from scriba.gui.window import MainWindow
        window = MainWindow(app)
        window.result.muted = True
        window.set_default_size(width, height)
        if self.opacity < 1.0:
            window.set_opacity(self.opacity)
        window.present()
        self.spin(lambda: window.get_mapped(), 5)
        window.views.set_visible_child_name(view)
        self.ghost(window)
        self.settle()
        return window

    def ghost(self, window) -> None:
        """Almost transparent on the screen, fully drawn in the picture.
        GTK's own opacity would be drawn into the picture too: on Windows
        the window is made a layered one, which the desktop composes
        transparent and through which the clicks pass, while GTK draws it
        whole. Until then (no handle before it is mapped) GTK's opacity
        hides it; elsewhere the pictures stay faint (--visible)."""
        if self.opacity >= 1.0 or os.name != "nt":
            return
        handle = self.handle(window)
        if handle:
            self.layered(handle)
            window.set_opacity(1.0)

    @staticmethod
    def handle(window) -> int:
        """The window's HWND, found by a title of its own for a moment."""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.FindWindowW.restype = wintypes.HWND
        title = window.get_title() or ""
        probe = f"scriba-shot-{id(window)}"
        window.set_title(probe)
        import _setup
        found = []
        _setup.spin(lambda: found.append(user32.FindWindowW(None, probe)) or found[-1], 2)
        window.set_title(title)
        return found[-1] or 0

    def layered(self, handle: int) -> None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetLayeredWindowAttributes.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD)
        user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
        gwl_exstyle, ws_ex_layered, ws_ex_transparent, lwa_alpha = -20, 0x80000, 0x20, 0x2
        style = user32.GetWindowLongPtrW(handle, gwl_exstyle)
        user32.SetWindowLongPtrW(handle, gwl_exstyle, style | ws_ex_layered | ws_ex_transparent)
        user32.SetLayeredWindowAttributes(handle, 0, max(1, round(255 * self.opacity)), lwa_alpha)

    def settle(self, seconds: float = 0.6) -> None:
        self.spin(lambda: False, seconds)       # the layout, the crossfades, the threads' answers

    def photograph(self, widget, name: str) -> Path:
        """The widget's toplevel as a PNG, drawn right after a frame (between
        two the window may have a layout pending and draw nothing)."""
        import gi
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene, Gtk
        window = widget.get_root()
        path = self.out / f"{name}.png"
        done = []

        def painted(clock):
            clock.disconnect(handler[0])
            width, height = window.get_width(), window.get_height()
            snapshot = Gtk.Snapshot()
            snapshot.scale(self.scale, self.scale)
            Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
            node = snapshot.to_node()
            if node is None:
                print(f"{name}: nothing to draw", file=sys.stderr)
            else:
                area = Graphene.Rect().init(0, 0, width * self.scale, height * self.scale)
                window.get_renderer().render_texture(node, area).save_to_png(str(path))
                self.written.append(path)
            done.append(True)

        clock = window.get_frame_clock()
        handler = [clock.connect("after-paint", painted)]
        window.queue_draw()
        self.spin(lambda: bool(done), 5)
        return path

    @staticmethod
    def scroller(widget):
        """The first scrolled window inside `widget` (a PreferencesPage's own)."""
        from gi.repository import Gtk
        stack = [widget]
        while stack:
            current = stack.pop(0)
            if isinstance(current, Gtk.ScrolledWindow):
                return current
            child = current.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return None

    def recording(self, tmp: Path, name: str) -> Path:
        path = tmp / "Registrazioni" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"made up")
        return path

    def opened(self, app, tmp, name="2026-11-11 Riunione di coordinamento impianti.mkv"):
        window = self.window(app)
        window.open_path(str(self.recording(tmp, name)))
        self.spin(lambda: window.transcribe.media is not None)
        self.settle()
        return window

    # -- the states ------------------------------------------------------------------

    def models(self):
        from scriba.app.backend import needed_models
        app, _tmp = self.app({"missing": needed_models()})
        window = self.window(app)
        window.show_models()
        self.settle()
        self.photograph(window, "01-modelli-mancanti")
        window.destroy()

    def models_downloading(self):
        """The first model held at 42 %, at the usual width and at a narrow one."""
        from scriba.app.backend import needed_models
        app, _tmp = self.app({"missing": needed_models()})
        hold = threading.Event()
        made_up = app.backend.download

        def download(model, progress=None, cancelled=None):
            if progress is not None:
                progress(model.size * 0.42, model.size)
            while not hold.wait(0.05):
                if cancelled is not None and cancelled.is_set():
                    break
            return made_up(model, progress, cancelled)

        app.backend.download = download
        app.downloads.start_required()
        self.spin(lambda: app.downloads.state.phase == "downloading", 5)
        for width, name in ((640, "01b-modelli-scaricamento"), (360, "01c-modelli-scaricamento-stretta")):
            window = self.window(app, width=width)
            window.show_models()
            self.settle()
            self.photograph(window, name)
            window.destroy()
        hold.set()
        app.downloads.shutdown()

    def empty(self):
        app, _tmp = self.app()
        window = self.window(app)
        self.photograph(window, "02-trascrivi-vuota")
        window.destroy()

    def setup_video(self):
        import fake_engines

        from scriba.app.config import Config
        from scriba.app.engines import ffmpeg
        cfg = Config(measured_device=fake_engines.CARD.name, subtitled_video=True, speaker_count=4,
                     terms="Cantiere Esempio, cavedi, montacarichi, UPS, QG&UPS")
        app, tmp = self.app({"media": ffmpeg.Media(3725.0, 1, True, 0), "precise": True}, cfg)
        window = self.opened(app, tmp)
        self.photograph(window, "03-trascrivi-video")
        scroll = self.scroller(window.transcribe.setup_page)
        if scroll is not None:
            adjustment = scroll.get_vadjustment()
            adjustment.set_value(adjustment.get_upper())
            self.settle(0.3)
            self.photograph(window, "03b-trascrivi-video-fondo")
        window.destroy()

    def cards_testing(self):
        """The test of the cards going, at the first start, with a recording
        opened meanwhile: the Graphics Card row says so, with its spinner."""
        app, tmp = self.app()
        hold = app.backend.hold_test = threading.Event()
        window = self.opened(app, tmp)
        self.spin(lambda: window.cards.running is not None)
        self.settle(0.4)
        self.photograph(window, "03c-trascrivi-prova-schede")
        hold.set()
        self.spin(lambda: window.cards.running is None)
        window.destroy()

    def cards_left_out(self):
        """A faster card that wrote the sample wrong: left out, and said in
        the row and in the preferences."""
        import fake_engines

        from scriba.app import jobs
        from scriba.gui.preferences import PreferencesDialog
        app, tmp = self.app()
        iris, card = fake_engines.IRIS, fake_engines.CARD
        app.backend.choice = jobs.Choice(card.name, card.label, 25.7, 3600 / 25.7 + 3600 / 20.0, (
            jobs.Tried(iris.name, iris.label, 31.0, 5.6, 0.31), jobs.Tried(card.name, card.label, 25.7, 3.1, 0.019),
            jobs.Tried("cpu", "cpu", 1.8, 15.5, 0.019)))
        window = self.opened(app, tmp)
        self.spin(lambda: app.cfg.measured_device == card.name)
        self.settle(0.4)
        self.photograph(window, "03d-trascrivi-scheda-scartata")
        dialog = PreferencesDialog(window, app)
        dialog.present(window)
        self.spin(lambda: dialog.get_root() is not None and dialog.get_root().get_mapped(), 5)
        root = dialog.get_root()
        if root is not window:
            self.ghost(root)
        self.settle()
        self.photograph(dialog, "07b-preferenze-scheda-scartata")
        dialog.close()
        window.destroy()

    def working(self):
        import fake_engines

        from scriba.app.config import Config
        hold = threading.Event()
        cfg = Config(measured_device=fake_engines.CARD.name)
        app, tmp = self.app(None, cfg)
        app.backend.runner = fake_engines.runner(lambda r: None, hold=hold)
        window = self.opened(app, tmp)
        window.transcribe.start()
        self.spin(lambda: window.transcribe.job is not None and window.transcribe.job[0].stage == "transcribe")
        self.settle(0.8)
        self.photograph(window, "04-lavoro-in-corso")
        window.transcribe.cancel()
        hold.set()
        self.spin(lambda: window.transcribe.job is None)
        window.destroy()

    def _result(self, voices, count, doubtful, names=None, notes=(), named=False, dark=False, name="05-risultato"):
        import fake_engines

        from scriba.app import jobs
        from scriba.app.config import Config
        cfg = Config(measured_device=fake_engines.CARD.name)
        app, tmp = self.app(None, cfg)
        source = self.recording(tmp, "2026-11-11 Riunione di coordinamento impianti.m4a")
        made = fake_engines.made_result(tmp / "Registrazioni", source, fake_engines.lines(voices, count, doubtful),
                                        notes=notes, duration=3725.0, total=462.0,
                                        device="cpu" if notes else fake_engines.CARD.label)
        if named:
            jobs.save_names(made, names)
        app.backend.runner = fake_engines.runner(lambda r: made)
        if dark:
            from gi.repository import Adw
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        window = self.opened(app, tmp, source.name)
        window.transcribe.start()
        self.spin(lambda: window.nav.get_visible_page() is window.result)
        for label, text in (names or {}).items():
            if label in window.result.entries:
                window.result.entries[label].set_text(text)
        self.settle(0.8)
        self.photograph(window, name)
        if not dark:
            scroll = self.scroller(window.result.page)
            if scroll is not None:
                adjustment = scroll.get_vadjustment()
                for part, where in (("b", 0.5), ("c", 1.0)):
                    adjustment.set_value((adjustment.get_upper() - adjustment.get_page_size()) * where)
                    self.settle(0.3)
                    self.photograph(window, f"{name}{part}")
        window.destroy()
        if dark:
            from gi.repository import Adw
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    def result(self):
        self._result(3, 24, 2)

    def result_long(self):
        long_names = ("Ing. Maria Francesca Bianchi-Lombardi (Direzione Lavori, committente)",
                      "Giovanni Battista De Santis", "Arch. Eleonora Fumagalli Castiglioni",
                      "Per. Ind. Riccardo Dell'Acqua (impresa elettrica)", "Dott.ssa Anna Maria Ricci",
                      "Responsabile della sicurezza in fase di esecuzione", "Luca", "Ospite collegato da Teams 2")
        names = {f"Speaker {i + 1}": n for i, n in enumerate(long_names)}
        from scriba.i18n import _
        self._result(8, 130, 40, names=names, named=True,
                     notes=(_("The graphics card did not work: the text was made on the processor (slower)."),),
                     name="06-risultato-lungo")

    def balance(self):
        """The balance with a long recording name, without reading or playing audio."""
        import fake_engines
        from scriba.app import jobs
        app, tmp = self.app()
        app.backend.balanceable = True
        source = self.recording(tmp, "2026-11-11 Riunione di coordinamento impianti elettrici e meccanici.mka")
        made = fake_engines.made_result(tmp / "Registrazioni", source, fake_engines.lines(3, 24, 2))
        made.names = {"Speaker 1": "Io", "Speaker 2": "Ing. Maria Francesca Bianchi-Lombardi"}
        window = self.window(app)
        window.show_result(made, jobs.Request(source))
        self.spin(lambda: window.result.balance_group.get_visible())
        window.result.balance_scale.set_value(0.5)
        self.settle()
        scroll = self.scroller(window.result.page)
        if scroll is not None:
            scroll.get_vadjustment().set_value(220)
        self.settle()
        self.photograph(window, "13-bilanciamento")
        window.destroy()

    def transcript(self):
        """The whole text on its own page, from "Read the Whole Transcript":
        the long case (8 voices with long names, doubtful lines), at the top
        and further down, then in the dark theme; with one name changed on
        the Result page after the page was made."""
        import fake_engines
        from gi.repository import Adw
        from scriba.app import jobs
        for dark, name in ((False, "14-trascrizione-completa"), (True, "14c-trascrizione-completa-scura")):
            app, tmp = self.app()
            source = self.recording(tmp, "2026-11-11 Riunione di coordinamento impianti.m4a")
            made = fake_engines.made_result(tmp / "Registrazioni", source, fake_engines.lines(8, 130, 40),
                                            duration=3725.0)
            made.names = {"Speaker 1": "Ing. Maria Francesca Bianchi-Lombardi (Direzione Lavori, committente)",
                          "Speaker 2": "Giovanni Battista De Santis"}
            if dark:
                Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            window = self.window(app)
            window.show_result(made, jobs.Request(source))
            window.result.read_all()
            self.spin(lambda w=window: w.nav.get_visible_page() is w.result.transcript)
            window.result.entries["Speaker 3"].set_text("Arch. Eleonora Fumagalli Castiglioni")
            self.settle(0.8)
            self.photograph(window, name)
            if not dark:
                scroll = self.scroller(window.result.transcript)
                adjustment = scroll.get_vadjustment()
                adjustment.set_value((adjustment.get_upper() - adjustment.get_page_size()) * 0.5)
                self.settle(0.3)
                self.photograph(window, "14b-trascrizione-completa-meta")
            window.destroy()
            if dark:
                Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

    def preferences(self):
        from scriba.app.config import Config
        from scriba.gui.preferences import PreferencesDialog
        import fake_engines
        app, _tmp = self.app(None, Config(measured_device=fake_engines.CARD.name))
        window = self.window(app)
        dialog = PreferencesDialog(window, app)
        dialog.present(window)
        self.spin(lambda: dialog.get_root() is not None and dialog.get_root().get_mapped(), 5)
        root = dialog.get_root()
        if root is not window:
            self.ghost(root)
        self.settle()
        self.photograph(dialog, "07-preferenze")
        dialog.close()
        window.destroy()

    def dark(self):
        self._result(3, 24, 2, dark=True, name="08-risultato-scuro")

    def _dark(self, dark: bool) -> None:
        from gi.repository import Adw
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark
                                                        else Adw.ColorScheme.FORCE_LIGHT)

    def terms(self):
        """The group Terms of the preferences: a file of made-up terms too
        long to be given whole (the count says how many go), and a file
        that is no longer there (a warning)."""
        import fake_engines

        from scriba.app.config import Config
        from scriba.gui.preferences import PreferencesDialog
        for dark, name in ((False, "15-preferenze-termini"), (True, "15b-preferenze-termini-scure")):
            app, tmp = self.app(None, Config(measured_device=fake_engines.CARD.name))
            trade = tmp / "Termini" / "ambito-impianti-esempio.txt"
            trade.parent.mkdir(parents=True, exist_ok=True)
            lines = ["# termini inventati per la fotografia", *(f"quadro di zona numero {n}" for n in range(1, 41))]
            trade.write_text("\n".join(lines) + "\n", encoding="utf-8")
            app.cfg.set_term_files([str(trade), str(tmp / "Termini" / "cliente-esempio-spostato.txt")])
            self._dark(dark)
            window = self.window(app)
            dialog = PreferencesDialog(window, app)
            dialog.present(window)
            self.spin(lambda d=dialog: d.get_root() is not None and d.get_root().get_mapped(), 5)
            root = dialog.get_root()
            if root is not window:
                self.ghost(root)
            self.spin(lambda d=dialog: d.terms_count is not None, 5)
            self.settle()
            from gi.repository import Gtk
            scroll = dialog.terms_group.get_ancestor(Gtk.ScrolledWindow)     # the page's, not the search's
            if scroll is not None:
                import gi
                gi.require_version("Graphene", "1.0")
                from gi.repository import Graphene
                found, point = dialog.terms_group.compute_point(scroll.get_child(), Graphene.Point())
                if found:
                    scroll.get_vadjustment().set_value(max(0.0, point.y - 24))
                else:
                    print("terms: the group's place was not found", file=sys.stderr)
            self.settle(0.4)
            self.photograph(dialog, name)
            dialog.close()
            window.destroy()
            self._dark(False)

    def result_doubtful(self):
        """A transcript where one line wrote a word over and over: more than a
        quarter of the text is doubtful, and the Result page says so."""
        import fake_engines

        from scriba.app import jobs
        from scriba.core import segments as seg
        for dark, name in ((False, "16-risultato-dubbio"), (True, "16b-risultato-dubbio-scuro")):
            app, tmp = self.app()
            source = self.recording(tmp, "2026-11-11 Riunione di coordinamento impianti.m4a")
            made = fake_engines.lines(3, 24, 2)
            loop = seg.Segment(made[-1].end + 1, made[-1].end + 31, " ".join(["allora"] * 600), "Speaker 2")
            result = fake_engines.made_result(tmp / "Registrazioni", source, [*made, *seg.flag_doubtful([loop])],
                                              duration=3725.0)
            self._dark(dark)
            window = self.window(app)
            window.show_result(result, jobs.Request(source))
            self.settle(0.8)
            self.photograph(window, name)
            window.destroy()
            self._dark(False)

    # -- the Record page -------------------------------------------------------------

    def _record(self, devices=None, levels=None, closed=()):
        import fake_engines
        app, tmp = self.app()
        if devices is not None:
            fake_engines.install_recorder(app.backend, devices, levels, closed)
        window = self.window(app, view="record")
        self.spin(lambda: window.record.monitor is not None and window.record.input_side.rows, 5)
        self.settle()
        return window

    def _going(self, window, elapsed: float):
        page = window.record
        page.record_button.emit("clicked")
        self.spin(lambda: page.recorder is not None)
        recorder = page.recorder
        recorder.elapsed = elapsed
        recorder.levels = {"me-1": (-26.0, -11.0), "others-2": (-20.0, -7.0), "others-3": (-700.0, -350.0)}
        self.settle()
        return recorder

    def record(self):
        window = self._record()
        self.photograph(window, "09-registra")
        window.record.input_side.row.set_expanded(True)
        self.settle(0.4)
        self.photograph(window, "09b-registra-microfoni")
        window.destroy()

    def record_going(self):
        window = self._record()
        recorder = self._going(window, 754.0)
        self.photograph(window, "10-registra-in-corso")
        recorder.state = "paused"
        self.settle(0.3)
        self.photograph(window, "10b-registra-in-pausa")
        recorder.state = "stopped"
        window.destroy()

    def record_warning(self):
        from scriba.app import recording as recordings
        from scriba.core import levels
        window = self._record()
        recorder = self._going(window, 2484.0)
        recorder.levels["me-1"] = (-47.0, -38.0)
        recorder.takes[1].ended = 2412.0
        recorder.alerts = (levels.Alert(levels.QUIET, "me-1", -46.0),)
        recorder.events = (recordings.Event(2412.0, recordings.GONE, "Cuffie (Headset BT)"),)
        recorder.seconds_left = 22 * 60
        self.settle(1.0)
        self.photograph(window, "11-registra-avviso")
        recorder.state = "stopped"
        window.destroy()

    def record_long(self):
        from scriba.core import devices as dev
        long = (
            dev.Device("o1", "Altoparlanti (Realtek(R) High Definition Audio con Dolby Atmos per cuffie)", dev.OUTPUT,
                       default=True),
            dev.Device("o2", "Cuffie (Sennheiser MOMENTUM 4 Wireless Hands-Free AG Audio)", dev.OUTPUT,
                       communications=True, container="m4", bluetooth=True, programs=("ms-teams.exe", "chrome.exe")),
            dev.Device("o3", "Monitor esterno (NVIDIA High Definition Audio, uscita DisplayPort 2)", dev.OUTPUT),
            dev.Device("i1", "Cuffia auricolare (Sennheiser MOMENTUM 4 Wireless Hands-Free AG Audio)", dev.INPUT,
                       communications=True, container="m4", bluetooth=True, programs=("ms-teams.exe",)),
            dev.Device("i2", "Microfono (HD Webcam con riduzione del rumore integrata)", dev.INPUT,
                       default=True),
            dev.Device("i3", "Gruppo microfoni (Tecnologia Intel® Smart Sound per microfoni digitali)", dev.INPUT),
            dev.Device("i4", "Microfono (Jabra Speak2 75, vivavoce per sale riunioni)", dev.INPUT, bluetooth=True),
        )
        levels = {"o1": (-700.0, -350.0), "o2": (-22.0, -9.0), "o3": (-700.0, -350.0), "i1": (-19.0, -6.0),
                  "i2": (-45.0, -33.0), "i3": (-52.0, -41.0)}
        window = self._record(long, levels, closed=("i4",))
        window.record.input_side.row.set_expanded(True)
        window.record.output_side.row.set_expanded(True)
        self.settle(0.5)
        self.photograph(window, "12-registra-lungo")
        scroll = self.scroller(window.record.page)
        if scroll is not None:
            adjustment = scroll.get_vadjustment()
            adjustment.set_value(adjustment.get_upper())
            self.settle(0.3)
            self.photograph(window, "12b-registra-lungo-fondo")
        window.destroy()


if __name__ == "__main__":
    sys.exit(main())
