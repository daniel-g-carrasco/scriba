"""Read-only Teams speaker observations. Pixels stay in memory, never on disk.

UIA and GDI live on one worker with physical coordinates. PrintWindow(2)
is tried first; a covered, unchanged frame cannot prove freshness and is
unavailable. Screen fallback is allowed only when no higher window overlaps.
"""
from __future__ import annotations

import ctypes
import hashlib
import math
import re
import threading
from ctypes import wintypes as w
from dataclasses import dataclass
from pathlib import PureWindowsPath

from scriba.app import callmute as com
from scriba.app.log import complain
from scriba.core import teams

EVERY_S = 0.5
END_OBSERVATIONS = 6
PW_RENDERFULLCONTENT = 2


@dataclass(frozen=True)
class Reading:
    state: str  # ready, minimized, no-meeting, unavailable, ended
    active: tuple[str, ...] = ()
    people: int = 0
    title: str = ""
    meeting: bool = False
    tiles: int | None = None
    capture_source: str = ""
    covered: bool | None = None
    error: bool = False
    border_sides: tuple[float, float, float, float] | None = None


class CapturePolicy:
    """Conservative freshness gate; a static covered frame is not evidence."""
    def __init__(self):
        self.previous = None

    def choose(self, printed: bytes | None, screen: bytes | None) -> bytes | None:
        digest = hashlib.blake2b(printed, digest_size=16).digest() if printed else None
        # GDI often leaves alpha zero, so alpha is deliberately ignored.
        nonblack = printed is not None and any(printed[i] > 16 for i in range(len(printed)) if i % 4 != 3)
        fresh = nonblack and self.previous is not None and digest != self.previous
        self.previous = digest
        if screen is not None:
            return printed if nonblack and printed == screen else screen
        return printed if fresh else None


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                ("planes", w.WORD), ("bits", w.WORD), ("compression", w.DWORD),
                ("image_size", w.DWORD), ("xppm", w.LONG), ("yppm", w.LONG),
                ("used", w.DWORD), ("important", w.DWORD), ("colors", w.DWORD * 3)]


class Reader(com.Indicator):
    """COM ownership follows Indicator; no control is ever invoked or clicked."""
    def __init__(self, *, names=True):
        self.names = names
        super().__init__()
        self._true = ctypes.c_void_p()
        self._dpi = None
        self._policy, self._window = CapturePolicy(), None
        self.capture_source = "unavailable"
        self.covered = None
        try:
            self._gdi = ctypes.WinDLL("gdi32", use_last_error=True)
            self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self._signatures()
            self._dpi = self._user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            if not self._dpi:
                raise ctypes.WinError(ctypes.get_last_error())
            com._checked(com._method(self._uia, 21, ctypes.POINTER(ctypes.c_void_p))(
                self._uia, ctypes.byref(self._true)), "CreateTrueCondition")
        except BaseException:
            self.close()
            raise

    def _signatures(self):
        self._enum_proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        entries = [
            (self._user32, "EnumWindows", [self._enum_proc, w.LPARAM], w.BOOL),
            (self._user32, "GetWindowThreadProcessId", [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            (self._user32, "IsWindowVisible", [w.HWND], w.BOOL),
            (self._user32, "IsIconic", [w.HWND], w.BOOL),
            (self._user32, "GetWindowRect", [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            (self._user32, "GetWindow", [w.HWND, w.UINT], w.HWND),
            (self._user32, "GetDC", [w.HWND], w.HDC),
            (self._user32, "ReleaseDC", [w.HWND, w.HDC], ctypes.c_int),
            (self._user32, "PrintWindow", [w.HWND, w.HDC, w.UINT], w.BOOL),
            (self._user32, "SetThreadDpiAwarenessContext", [ctypes.c_void_p], ctypes.c_void_p),
            (self._gdi, "CreateCompatibleDC", [w.HDC], w.HDC),
            (self._gdi, "DeleteDC", [w.HDC], w.BOOL),
            (self._gdi, "CreateDIBSection", [w.HDC, ctypes.POINTER(_BitmapInfo), w.UINT,
                                            ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD], w.HBITMAP),
            (self._gdi, "SelectObject", [w.HDC, w.HANDLE], w.HANDLE),
            (self._gdi, "DeleteObject", [w.HANDLE], w.BOOL),
            (self._gdi, "BitBlt", [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD], w.BOOL),
            (self._kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            (self._kernel, "QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR,
                                                          ctypes.POINTER(w.DWORD)], w.BOOL),
            (self._kernel, "CloseHandle", [w.HANDLE], w.BOOL),
        ]
        for library, name, args, result in entries:
            function = getattr(library, name)
            function.argtypes, function.restype = args, result

    def _teams_windows(self):
        windows = []
        def visit(hwnd, _):
            pid = w.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = self._kernel.OpenProcess(0x1000, False, pid.value)
            if process:
                try:
                    text, length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
                    if (self._kernel.QueryFullProcessImageNameW(process, 0, text, ctypes.byref(length))
                            and PureWindowsPath(text.value).name.casefold() == "ms-teams.exe"):
                        windows.append(hwnd)
                finally:
                    self._kernel.CloseHandle(process)
            return True
        callback = self._enum_proc(visit)
        if not self._user32.EnumWindows(callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return windows

    def _elements(self, hwnd):
        root, found = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            com._checked(com._method(self._uia, 6, w.HWND, ctypes.POINTER(ctypes.c_void_p))(
                self._uia, hwnd, ctypes.byref(root)), "ElementFromHandle")
            com._checked(com._method(root, 6, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                root, 4, self._true, ctypes.byref(found)), "FindAll")
            count = ctypes.c_int()
            com._checked(com._method(found, 3, ctypes.POINTER(ctypes.c_int))(found, ctypes.byref(count)), "Length")
            meeting, tiles = False, []
            for index in range(count.value):
                element = ctypes.c_void_p()
                try:
                    com._checked(com._method(found, 4, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                        found, index, ctypes.byref(element)), "GetElement")
                    if self._text(element, 29) == "hangup-button":
                        meeting = True
                        if not self.names:
                            break
                    if not self.names:
                        continue
                    kind = ctypes.c_int()
                    com._checked(com._method(element, 21, ctypes.POINTER(ctypes.c_int))(
                        element, ctypes.byref(kind)), "ControlType")
                    if kind.value not in (50011, 50006):  # MenuItem, Image
                        continue
                    person = teams.tile_name(self._text(element, 23))
                    if person is None:
                        continue
                    rect = w.RECT()
                    com._checked(com._method(element, 43, ctypes.POINTER(w.RECT))(
                        element, ctypes.byref(rect)), "BoundingRectangle")
                    if rect.right - rect.left >= 60 and rect.bottom - rect.top >= 40:
                        tiles.append((person, (rect.left, rect.top, rect.right, rect.bottom)))
                finally:
                    com._release(element)
            return meeting, tiles
        finally:
            com._release(found)
            com._release(root)

    def _rect(self, hwnd):
        rect = w.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return rect.left, rect.top, rect.right, rect.bottom

    def _uncovered(self, hwnd, rect):
        left, top, right, bottom = rect
        # Any overlapping top-level window above Teams makes screen fallback unsafe.
        higher = self._user32.GetWindow(hwnd, 3)  # GW_HWNDPREV
        while higher:
            if self._user32.IsWindowVisible(higher) and not self._user32.IsIconic(higher):
                a, b, c, d = self._rect(higher)
                if max(left, a) < min(right, c) and max(top, b) < min(bottom, d):
                    return False
            higher = self._user32.GetWindow(higher, 3)
        return True

    def _capture(self, hwnd, rect, screen=False):
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0 or width * height > 32_000_000:
            return None
        source = self._user32.GetDC(None)
        dc, bitmap, old = None, None, None
        try:
            if not source:
                raise ctypes.WinError(ctypes.get_last_error())
            dc = self._gdi.CreateCompatibleDC(source)
            info = _BitmapInfo(size=40, width=width, height=-height, planes=1, bits=32)
            bits = ctypes.c_void_p()
            bitmap = self._gdi.CreateDIBSection(source, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not dc or not bitmap or not bits:
                raise ctypes.WinError(ctypes.get_last_error())
            old = self._gdi.SelectObject(dc, bitmap)
            ctypes.memset(bits, 0, width * height * 4)
            ok = (self._gdi.BitBlt(dc, 0, 0, width, height, source, left, top, 0x00CC0020) if screen else
                  self._user32.PrintWindow(hwnd, dc, PW_RENDERFULLCONTENT))
            return ctypes.string_at(bits, width * height * 4) if ok else None
        finally:
            if old:
                self._gdi.SelectObject(dc, old)
            if bitmap:
                self._gdi.DeleteObject(bitmap)
            if dc:
                self._gdi.DeleteDC(dc)
            if source:
                self._user32.ReleaseDC(None, source)

    def read(self):
        self.capture_source = "unavailable"
        self.covered = None
        for hwnd in self._teams_windows():
            meeting, tiles = self._elements(hwnd)
            if not meeting:
                continue
            if not getattr(self, "names", True):
                return Reading("present", meeting=True)
            title = self._meeting_title(hwnd)
            if self._window != hwnd:
                self._policy, self._window = CapturePolicy(), hwnd
            if self._user32.IsIconic(hwnd):
                self._policy = CapturePolicy()
                return Reading("minimized", title=title)
            if not self._user32.IsWindowVisible(hwnd) or not tiles:
                return Reading("unavailable", title=title, meeting=True)
            people = sum(not person.own for person, _rect in tiles) + any(person.own for person, _rect in tiles)
            rect = self._rect(hwnd)
            printed = self._capture(hwnd, rect)
            self.covered = not self._uncovered(hwnd, rect)
            screen = self._capture(hwnd, rect, screen=True) if not self.covered else None
            pixels = self._policy.choose(printed, screen)
            if pixels is None:
                return Reading("unavailable", people=people, title=title, meeting=True,
                               tiles=len(tiles), covered=self.covered)
            self.capture_source = "print-window" if pixels is printed else "screen"
            left, top, right, bottom = rect
            active = []
            best_sides = None
            for person, (a, b, c, d) in tiles:
                if person.own:
                    continue
                tile = (math.floor(a - left), math.floor(b - top), math.ceil(c - left), math.ceil(d - top))
                border = teams.border(pixels, right - left, bottom - top, (right - left) * 4, tile)
                best_sides = (border.sides if best_sides is None else
                              tuple(map(max, best_sides, border.sides)))
                if border.active:
                    active.append(person.name)
            return Reading("ready", tuple(dict.fromkeys(active)), people, title, tiles=len(tiles),
                           capture_source=self.capture_source, covered=self.covered, border_sides=best_sides)
        self._policy, self._window = CapturePolicy(), None
        return Reading("no-meeting")

    def _meeting_title(self, hwnd) -> str:
        text = ctypes.create_unicode_buffer(256)
        read = self._user32.GetWindowTextW
        read.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        read.restype = ctypes.c_int
        if not read(hwnd, text, len(text)):
            return ""
        title = re.split(r"\s+(?:[|–-])\s+(?:Microsoft )?Teams\b", text.value, maxsplit=1,
                         flags=re.IGNORECASE)[0].strip()
        return title if title.casefold() not in {"microsoft teams", "teams", "meeting", "riunione"} else ""

    def _uncovered(self, hwnd, rect):
        """Conservative z-order check: even a partial overlap disables screen fallback."""
        left, top, right, bottom = rect
        above = self._user32.GetWindow(hwnd, 3)  # GW_HWNDPREV
        while above:
            if self._user32.IsWindowVisible(above) and not self._user32.IsIconic(above):
                other = self._rect(above)
                if max(left, other[0]) < min(right, other[2]) and max(top, other[1]) < min(bottom, other[3]):
                    return False
            above = self._user32.GetWindow(above, 3)
        return True

    def _rect(self, hwnd):
        rect = w.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return rect.left, rect.top, rect.right, rect.bottom

    def close(self):
        com._release(getattr(self, "_true", None))
        self._true = ctypes.c_void_p()
        if getattr(self, "_dpi", None):
            self._user32.SetThreadDpiAwarenessContext(self._dpi)
            self._dpi = None
        super().close()


def follow(told, stop: threading.Event, every=EVERY_S, opener=Reader):
    try:
        reader = opener()
    except OSError as error:
        complain("opening Teams observations", error)
        told(Reading("unavailable", error=True))
        return
    saw_meeting = False
    absent = 0
    try:
        while not stop.is_set():
            try:
                reading = reader.read()
            except OSError as error:
                complain("reading Teams observations", error)
                reading = Reading("unavailable", error=True, covered=getattr(reader, "covered", None))
            if reading.meeting or reading.state in ("ready", "minimized", "present"):
                saw_meeting = True
            absent = absent + 1 if saw_meeting and reading.state == "no-meeting" else 0
            if absent == END_OBSERVATIONS:
                reading = Reading("ended")
                saw_meeting = False
            told(reading)
            if stop.wait(every):
                break
    finally:
        reader.close()
