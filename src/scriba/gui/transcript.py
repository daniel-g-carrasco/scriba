"""The whole transcript, on a page of its own pushed over the Result page.

GNOME's guidelines: one scrolling area for a view, never one inside
another. The Result page scrolls by itself and shows only the first lines;
this page has the text, all of it, in one scrolling area as tall as the
window, as wide as a reading column (Adw.ClampScrollable, libadwaita's
width). The voices, the times and the doubtful lines are as on the Result
page and in the files (core/paragraphs.py): a voice's name where the voice
changes, the time of each paragraph before it, a doubtful line faded with
its mark and, on the pointer, the engine's reasons.

A read-only Gtk.TextView rather than a label per paragraph: the text can be
selected and copied across paragraphs, the keyboard scrolls it (arrows, Page
Up and Down, Home and End, Ctrl+A), and a meeting of two hours (390
paragraphs, 13 400 words) is laid out by GTK a screen at a time. A name
given on the Result page reaches this page too (rename). Back, Escape and
Alt+Left go back to the Result page (Adw.NavigationView's own keys).
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402

from scriba.core import speakers  # noqa: E402
from scriba.core.paragraphs import paragraphs  # noqa: E402
from scriba.data import markdown  # noqa: E402
from scriba.data.timing import clock  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .style_values import SPACE_L, SPACE_M, SPACE_S, SPACE_XS, SPACE_XXL  # noqa: E402
from .ui import escape, header_bar  # noqa: E402

DIM = 0.55          # libadwaita's dim-label: the text's colour at this opacity
TIME_SCALE = 0.85   # the times a little smaller, as a caption beside the text


class TranscriptPage(Adw.NavigationPage):
    def __init__(self, window, result, names: dict[str, str]):
        super().__init__(title=_("Full Transcript"), tag="transcript")
        self._style_handlers: list[int] = []
        self._heads: dict[str, list[tuple[Gtk.TextMark, Gtk.TextMark]]] = {}
        self._reasons: dict[Gtk.TextTag, str] = {}

        view = Adw.ToolbarView()
        header = header_bar()
        title = Adw.WindowTitle(title=_("Full Transcript"))
        title.set_subtitle(escape(Path(result.source or result.session).name))
        header.set_title_widget(title)
        header.pack_end(window.menu_button())
        view.add_top_bar(header)

        self.text = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 top_margin=SPACE_XXL, bottom_margin=SPACE_XXL,
                                 left_margin=SPACE_L, right_margin=SPACE_L)
        self.text.update_property([Gtk.AccessibleProperty.LABEL], [_("Full Transcript")])
        self.text.set_has_tooltip(True)
        self.text.connect("query-tooltip", self._tooltip)
        self.text.connect("map", lambda *_: self._follow_style(True))
        self.text.connect("unmap", lambda *_: self._follow_style(False))
        # one scrolling area, the whole page's background the text's own
        scroll = Gtk.ScrolledWindow(child=Adw.ClampScrollable(child=self.text),
                                    hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, css_classes=["view"])
        view.set_content(scroll)
        self.set_child(view)
        # the keys scroll the text at once (Page Down, arrows), Tab reaches the header
        self.connect("shown", lambda *_: self.text.grab_focus())
        self._fill(result, names)

    # -- the text --------------------------------------------------------------------

    def _fill(self, result, names: dict[str, str]) -> None:
        buffer = self.text.get_buffer()
        lines = paragraphs(result.segments)     # as the files have them; a doubtful line on its own
        times = [clock(paragraph[0].start) for paragraph in lines]
        # a hanging indent: the time under the voice's name, a tab, the text,
        # and the text's wrapped lines under its first word (a negative indent
        # leaves the first line where it is and moves the others)
        width = self._width(max(times, key=len, default="00:00")) + SPACE_M
        tabs = Pango.TabArray.new(1, True)
        tabs.set_tab(0, Pango.TabAlign.LEFT, width)
        self.voice = buffer.create_tag("voice", weight=Pango.Weight.BOLD)
        self.voice_after = buffer.create_tag("voice-after", pixels_above_lines=SPACE_L)
        self.paragraph = buffer.create_tag("paragraph", indent=-width, tabs=tabs, pixels_above_lines=SPACE_XS)
        self.same_voice = buffer.create_tag("same-voice", pixels_above_lines=SPACE_S)
        self.time = buffer.create_tag("time", scale=TIME_SCALE, font_features="tnum")
        reasons = markdown.reasons()
        voiced = any(s.speaker for s in result.segments)
        shown = speakers.display_names({s.speaker for s in result.segments if s.speaker}, names)
        previous, first = None, True
        end = buffer.get_end_iter()
        for paragraph, at in zip(lines, times, strict=True):
            seg = paragraph[0]
            if voiced and seg.speaker != previous:
                start = buffer.create_mark(None, end, True)
                buffer.insert_with_tags(end, shown.get(seg.speaker, "?") if seg.speaker else "?", self.voice,
                                        *(() if first else (self.voice_after,)))
                self._heads.setdefault(seg.speaker or "", []).append((start, buffer.create_mark(None, end, True)))
                buffer.insert(end, "\n")
                spacing = ()
                previous = seg.speaker
            else:
                spacing = () if first else (self.same_voice,)
            first = False
            line = buffer.create_mark(None, end, True)
            buffer.insert_with_tags(end, at, self.time)
            words = " ".join(" ".join(s.text.split()) for s in paragraph)
            doubts = tuple(dict.fromkeys(code for s in paragraph for code in s.doubts))
            if doubts:
                tag = buffer.create_tag(None)
                self._reasons[tag] = ", ".join(reasons.get(code, code) for code in doubts)
                buffer.insert_with_tags(end, f"\t{markdown.DOUBT_MARK} {words}", tag)
            else:
                buffer.insert(end, f"\t{words}")
            buffer.apply_tag(self.paragraph, buffer.get_iter_at_mark(line), end)
            for tag in spacing:
                buffer.apply_tag(tag, buffer.get_iter_at_mark(line), end)
            buffer.delete_mark(line)
            buffer.insert(end, "\n")
        if not lines:
            buffer.insert(end, _("No speech was found in the recording."))
        buffer.place_cursor(buffer.get_start_iter())

    def _width(self, text: str) -> int:
        """The width of a time as the page writes it, in pixels."""
        layout = self.text.create_pango_layout(text)
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_scale_new(TIME_SCALE))
        attributes.insert(Pango.attr_font_features_new("tnum"))
        layout.set_attributes(attributes)
        return layout.get_pixel_size()[0]

    def rename(self, speaker: str, shown: str) -> None:
        """A voice's name changed on the Result page: here too."""
        buffer = self.text.get_buffer()
        for start, end in self._heads.get(speaker, ()):
            buffer.delete(buffer.get_iter_at_mark(start), buffer.get_iter_at_mark(end))
            where = buffer.get_iter_at_mark(start)
            first = where.is_start()
            buffer.insert_with_tags(where, shown, self.voice, *(() if first else (self.voice_after,)))
            buffer.move_mark(end, where)        # (where: now after the name)

    def contents(self) -> str:
        buffer = self.text.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    # -- the colours: libadwaita's accent for the names, dim for times and doubts ------

    def _follow_style(self, on: bool) -> None:
        manager = Adw.StyleManager.get_default()
        for handler in self._style_handlers:
            manager.disconnect(handler)
        self._style_handlers = []
        if on:
            self._style_handlers = [manager.connect(signal, lambda *_: self._colour())
                                    for signal in ("notify::dark", "notify::accent-color", "notify::high-contrast")]
            self._colour()

    def _colour(self) -> None:
        manager = Adw.StyleManager.get_default()
        accent = Adw.AccentColor.to_standalone_rgba(manager.get_accent_color(), manager.get_dark())
        text = self.text.get_color()
        dim = Gdk.RGBA()
        dim.red, dim.green, dim.blue, dim.alpha = text.red, text.green, text.blue, text.alpha * DIM
        self.voice.set_property("foreground-rgba", accent)
        self.time.set_property("foreground-rgba", dim)
        for tag in self._reasons:
            tag.set_property("foreground-rgba", dim)

    def _tooltip(self, view: Gtk.TextView, x: int, y: int, keyboard: bool, tooltip: Gtk.Tooltip) -> bool:
        """A doubtful paragraph's reasons, as the Result page gives them."""
        if keyboard:
            buffer = view.get_buffer()
            where = buffer.get_iter_at_mark(buffer.get_insert())
        else:
            bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, x, y)
            found, where = view.get_iter_at_location(bx, by)
            if not found:
                return False
        for tag in where.get_tags():
            if tag in self._reasons:
                tooltip.set_text(self._reasons[tag])
                return True
        return False
