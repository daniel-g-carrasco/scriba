# SPDX-License-Identifier: GPL-3.0-or-later
"""Sizing for GTK secondary surfaces."""

import sys

WINDOWS = sys.platform == "win32"


def preference_size(width, height, available_width, available_height):
    """Bound the initial content to the work area without a size floor."""
    return min(width, max(1, available_width)), min(height, max(1, available_height))


def _win32_workarea(monitor):
    # GTK exports this API, but its Windows typelib omits the binding.
    # Use GTK's own application-pixel work area, including fractional DPI.
    import ctypes

    class Rectangle(ctypes.Structure):
        _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int),
                    ("width", ctypes.c_int), ("height", ctypes.c_int)]
    unpack = ctypes.pythonapi.PyCapsule_GetPointer
    unpack.argtypes = [ctypes.py_object, ctypes.c_char_p]
    unpack.restype = ctypes.c_void_p
    gtk = ctypes.CDLL("libgtk-4-1.dll")
    workarea = gtk.gdk_win32_monitor_get_workarea
    workarea.argtypes = [ctypes.c_void_p, ctypes.POINTER(Rectangle)]
    workarea.restype = None
    area = Rectangle()
    workarea(unpack(monitor.__gpointer__, None), ctypes.byref(area))
    return area


def available_size(window, margin=0):
    display = window.get_display()
    surface = window.get_surface()
    monitor = display.get_monitor_at_surface(surface) if surface is not None else display.get_monitors().get_item(0)
    if monitor is None:
        return 600, 640
    if WINDOWS:
        area = _win32_workarea(monitor)
    else:
        area = monitor.get_geometry()
    # Space for the native frame, in the same application pixels as GTK.
    return area.width - 2 * margin, area.height - 2 * margin


def centred(inner: tuple[int, int], outer: tuple[int, int, int, int],
            area: tuple[int, int, int, int]) -> tuple[int, int]:
    """Where a window of size `inner` goes to sit in the middle of `outer`
    (left, top, right, bottom) without leaving `area`; its top edge, with the
    title bar, stays inside whatever its size."""
    width, height = inner
    x = (outer[0] + outer[2] - width) // 2
    y = (outer[1] + outer[3] - height) // 2
    x = max(area[0], min(x, area[2] - width))
    y = max(area[1], min(y, area[3] - height))
    return x, y
