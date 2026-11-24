"""The same recording timeline for choosing and showing an excerpt."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from scriba.core.recording import cut_seconds  # noqa: E402
from scriba.core.trim_range import Selection  # noqa: E402
from scriba.data.timing import clock  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .style_values import SPACE_XS  # noqa: E402

EDGES = ("start", "end")
PAD = 24


class TrimRange(Gtk.Box):
    def __init__(self, duration, interval, call=None, *, editable=False, changed=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_XS, hexpand=True)
        self.selection = Selection(duration, *interval)
        self.call, self.editable, self.changed = call, editable, changed
        self._drag_edge = None
        self._width = 0
        self.area = Gtk.DrawingArea(content_width=240, content_height=64, hexpand=True)
        self.area.connect("resize", self._resized)
        overlay = Gtk.Overlay(child=self.area, hexpand=True)
        self.fixed = Gtk.Fixed(hexpand=True, vexpand=True)
        overlay.add_overlay(self.fixed)
        self.append(overlay)
        self.background = self._element("trim-track", 6)
        self.kept = self._element("trim-kept", 6)
        self.call_marks = [self._element("trim-call", 6) for _ in call] if call else []
        self.labels = {}
        self.handles = {}
        self.entries = {}
        inputs = Gtk.Box(spacing=SPACE_XS, halign=Gtk.Align.CENTER)
        for edge, name in zip(EDGES, (_("Selection Start"), _("Selection End")), strict=True):
            label = self.labels[edge] = Gtk.Label(css_classes=["numeric", "caption"], width_chars=7)
            label.set_size_request(56, -1)
            self.fixed.put(label, 0, 0)
            if editable:
                knob = self.handles[edge] = Gtk.Button(css_classes=["trim-handle"], tooltip_text=name)
                knob.update_property([Gtk.AccessibleProperty.LABEL], [name])
                key = Gtk.EventControllerKey()
                key.connect("key-pressed", lambda _c, value, _code, state, which=edge:
                            self.key(which, value, state))
                knob.add_controller(key)
                self.fixed.put(knob, 0, 0)
                entry = self.entries[edge] = Gtk.Entry(width_chars=7, max_width_chars=9, tooltip_text=name)
                entry.update_property([Gtk.AccessibleProperty.LABEL], [name])
                entry.connect("activate", lambda *_: self.read_entries())
                inputs.append(entry)
        self.summary = Gtk.Label(css_classes=["caption", "dim-label"], wrap=True)
        self.append(self.summary)
        if editable:
            self.append(inputs)
            drag = Gtk.GestureDrag()
            drag.set_button(1)
            drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            drag.connect("drag-begin", self._begin)
            drag.connect("drag-update", self._drag)
            drag.connect("drag-end", lambda *_: setattr(self, "_drag_edge", None))
            overlay.add_controller(drag)
        self._display()

    def _element(self, css, height):
        widget = Gtk.Box(css_classes=[css], can_target=False)
        widget.set_size_request(1, height)
        self.fixed.put(widget, 0, 0)
        return widget

    def _resized(self, _area, width, _height):
        self._width = width
        self._layout()

    def _layout(self):
        length = max(1, self._width - 2 * PAD)
        self.background.set_size_request(length, 6)
        self.fixed.move(self.background, PAD, 28)
        first, last = (self.selection.position(edge, length) + PAD for edge in EDGES)
        self.kept.set_size_request(max(1, round(last - first)), 6)
        self.fixed.move(self.kept, first, 28)
        for mark, seconds in zip(self.call_marks, self.call or (), strict=True):
            self.fixed.move(mark, PAD + seconds * length / max(1e-6, self.selection.duration), 36)
        for edge, position, y in zip(EDGES, (first, last), (3, 44), strict=True):
            self.fixed.move(self.labels[edge], max(0, min(position - 28, self._width - 56)), y)
            if edge in self.handles:
                self.fixed.move(self.handles[edge], position - 9, 22)

    def _display(self):
        start, end = self.selection.interval
        for edge, point in zip(EDGES, (start, end), strict=True):
            label = clock(point)
            self.labels[edge].set_text(label)
            if edge in self.entries:
                self.entries[edge].set_text(label)
                self.entries[edge].remove_css_class("error")
        self.summary.set_text(_("Keep {kept} of {total}").format(kept=clock(end - start),
                                                               total=clock(self.selection.duration)))
        self._layout()

    def _updated(self):
        self._display()
        if self.changed is not None:
            self.changed(self.selection.interval)

    def _begin(self, _gesture, x, _y):
        length = max(1, self._width - 2 * PAD)
        self._drag_x = x
        self._drag_edge = min(EDGES, key=lambda edge: abs(x - PAD - self.selection.position(edge, length)))
        self.handles[self._drag_edge].grab_focus()

    def _drag(self, _gesture, offset, _dy):
        if self._drag_edge:
            point = (self._drag_x + offset - PAD) * self.selection.duration / max(1, self._width - 2 * PAD)
            self.selection.move(self._drag_edge, round(point))
            self._updated()

    def key(self, edge, value, state):
        if value not in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
            return False
        delta = 10 if state & Gdk.ModifierType.SHIFT_MASK else 1
        self.selection.step(edge, -delta if value in (Gdk.KEY_Left, Gdk.KEY_Down) else delta)
        self._updated()
        return True

    def read_entries(self) -> bool:
        if not self.editable:
            return True
        values = []
        try:
            for edge in EDGES:
                entry = self.entries[edge]
                old = getattr(self.selection, edge)
                values.append(old if entry.get_text() == clock(old) else cut_seconds(entry.get_text()))
            start, end = values
            if not 0 <= start < end <= self.selection.duration:
                raise ValueError
        except ValueError:
            for entry in self.entries.values():
                entry.add_css_class("error")
            return False
        self.selection.start, self.selection.end = start, end
        self._updated()
        return True
