# SPDX-License-Identifier: GPL-3.0-or-later
"""GTK secondary surfaces and the native frame for detached dialogs."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from gi.repository import Gtk

from .sizing import centred, preference_size


GWL_STYLE, WS_MINIMIZEBOX = -16, 0x00020000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0001, 0x0002, 0x0004, 0x0010, 0x0020
MONITOR_DEFAULTTONEAREST = 2

# our own handle: argtypes set on ctypes.windll.user32 would reach every
# other user of that shared object (win32.py, the tray)
_user32 = None
if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wintypes.UINT]
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HANDLE
    _user32.IsIconic.argtypes = [wintypes.HWND]


    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]


    _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]



def present_secondary(dialog, parent, *, modal=True):
    """Present an Adw dialog or an existing simple GTK content window."""
    if isinstance(dialog, Gtk.Window):
        dialog.set_transient_for(parent)
        dialog.set_modal(modal)
        dialog.present()
    else:
        dialog.present(parent)


def fit_preferences(dialog, parent, available, preferred=(600, 640)):
    """Fit controls to the parent's display and leave detached windows resizable."""
    size = preference_size(*preferred, *available(parent))
    dialog.set_content_width(size[0])
    dialog.set_content_height(size[1])

    def detached(*_args):
        root = dialog.get_root()
        if isinstance(root, Gtk.Window) and root is not parent:
            root.set_resizable(True)
            root.set_default_size(*preference_size(*preferred, *available(parent)))

    dialog.connect("realize", detached)


def header_bars_below(widget: Gtk.Widget, adw):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, adw.HeaderBar):
            yield child
        yield from header_bars_below(child, adw)
        child = child.get_next_sibling()


def title_once(bar, window_title: str, adw) -> None:
    """The window's title is in the system's title bar: a header bar under it
    does not say it again. One that would show it by itself shows no title;
    one with the title and a second line (the file's name under "Share")
    keeps that line alone, as its title. Other titles (a page's own, in a
    dialog with several pages) stay."""
    if not window_title:
        return
    widget = bar.get_title_widget()
    if widget is None:
        page = bar.get_ancestor(adw.NavigationPage)
        if (page.get_title() if page is not None else window_title) == window_title:
            bar.set_show_title(False)
    elif isinstance(widget, adw.WindowTitle) and widget.get_title() == window_title:
        if widget.get_subtitle():
            widget.set_title(widget.get_subtitle())
            widget.set_subtitle("")
        else:
            bar.set_show_title(False)


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def centre_on_parent(window: Gtk.Window, hwnd_of) -> bool:
    """The window moved to the middle of the one it belongs to, inside that
    one's screen (the work area, without the taskbar). False when there is
    nothing to centre on (no parent shown, or a minimized one)."""
    parent = window.get_transient_for()
    if parent is None or not parent.get_mapped():
        return False
    hwnd, owner = hwnd_of(window), hwnd_of(parent)
    if not hwnd or not owner or _user32.IsIconic(owner):
        return False
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    if not _user32.GetMonitorInfoW(_user32.MonitorFromWindow(owner, MONITOR_DEFAULTTONEAREST), ctypes.byref(info)):
        return False
    left, top, right, bottom = _rect(hwnd)
    work = info.rcWork
    x, y = centred((right - left, bottom - top), _rect(owner), (work.left, work.top, work.right, work.bottom))
    _user32.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    return True


def watch_dialog(window: Gtk.Window, adw, hwnd_of) -> None:
    """A window libadwaita opens for a dialog: Windows' own frame and title
    bar where it has a header bar, and in the middle of its parent (see the
    module's notes). For every new toplevel: it does nothing to the others."""
    def on_child(w: Gtk.Window, _pspec) -> None:
        dialog = w.get_child()
        if not isinstance(dialog, adw.Dialog) or getattr(w, "dialog_watched", False):
            return
        w.dialog_watched = True
        framed = not isinstance(dialog, adw.AlertDialog) and w.get_titlebar() is not None
        if framed:
            w.set_titlebar(None)        # with GTK_CSD=0: the system's title bar and frame

        def on_map(mapped: Gtk.Window) -> None:
            if framed:
                for bar in header_bars_below(mapped, adw):       # the title bar has the close button
                    bar.set_show_start_title_buttons(False)
                    bar.set_show_end_title_buttons(False)
                    title_once(bar, mapped.get_title() or "", adw)
                hwnd = hwnd_of(mapped)
                if hwnd and _user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_MINIMIZEBOX:
                    # a dialog is not minimized by itself: its parent would stay behind, not answering
                    _user32.SetWindowLongW(hwnd, GWL_STYLE, _user32.GetWindowLongW(hwnd, GWL_STYLE) & ~WS_MINIMIZEBOX)
                    _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                         SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
            if not getattr(mapped, "dialog_placed", False):
                mapped.dialog_placed = centre_on_parent(mapped, hwnd_of)
        w.connect("map", on_map)
    if _user32 is None:
        return
    window.connect("notify::child", on_child)
    on_child(window, None)
