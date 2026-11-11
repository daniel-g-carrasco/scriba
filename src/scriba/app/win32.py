"""What the app needs from Windows itself, through ctypes: the title bars
in the content's colour, the executable's icon on every window, the files
that would run when opened. Imported only on Windows.

Every function sets the argtypes and restype of what it calls: without
them ctypes passes and returns C ints, and a HANDLE (64 bit) comes back
truncated to 32, a mistake that shows only now and then.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


# -- the title bar -----------------------------------------------------------------

DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR = 35, 36      # Windows 11 (build 22000) and later
GWL_STYLE, WS_CAPTION = -16, 0x00C00000             # a window with a title bar (not a popover's surface)


class MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int), ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


def colorref(rgb: tuple[int, int, int]) -> int:
    """(red, green, blue), each 0-255, as Windows writes a colour: 0x00BBGGRR."""
    red, green, blue = (max(0, min(255, int(part))) for part in rgb)
    return red | green << 8 | blue << 16


def title_bars(dark: bool, background: tuple[int, int, int] | None = None,
               text: tuple[int, int, int] | None = None) -> None:
    """The system title bars of this process's windows as the content is:
    dark or light (Windows 10 1809 and later: the buttons' glyphs follow
    it), and on Windows 11 in the content's very colours, `background` and
    `text` as (red, green, blue), so that the bar and the window read as
    one surface. Where Windows does not know the colour attributes
    (Windows 10) the dark or light bar stays; ignored elsewhere."""
    # our own handles: argtypes set on ctypes.windll's shared objects would
    # reach every other module that calls the same functions
    user32, dwmapi = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("dwmapi")
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
    dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(MARGINS)]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    pid, value = os.getpid(), ctypes.c_int(1 if dark else 0)
    colours = [(attribute, wintypes.DWORD(colorref(rgb)))
               for attribute, rgb in ((DWMWA_CAPTION_COLOR, background), (DWMWA_TEXT_COLOR, text)) if rgb is not None]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            for attribute in (20, 19):      # DWMWA_USE_IMMERSIVE_DARK_MODE, then its 1809 number
                if dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
            for attribute, colour in colours:
                dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(colour), ctypes.sizeof(colour))
            # one pixel of DWM's frame under the content, at the top. The
            # app is system DPI aware (GTK scales by whole numbers only): on a
            # screen whose scale is not the main one's Windows stretches the
            # window, and the seam between the title bar and the content
            # showed what was behind the window
            if user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_CAPTION == WS_CAPTION:
                dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(0, 0, 1, 0)))
        return True

    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.EnumWindows(visit, 0)


SM_CXICON, SM_CXSMICON = 11, 49
ICON_SMALL, ICON_BIG, WM_SETICON = 0, 1, 0x0080
_icons: dict[tuple[str, int], int] = {}     # kept for the life of the process: a window does not copy its icon


def _exe_icon(exe: str, size: int) -> int:
    """The exe's own icon at this size (its .ico holds 16, 20, 24 ... 256,
    the small ones drawn for small sizes); 0 when there is none."""
    if (exe, size) not in _icons:
        handles, ids = (wintypes.HICON * 1)(), (wintypes.UINT * 1)()
        got = ctypes.windll.user32.PrivateExtractIconsW(exe, 0, size, size, handles, ids, 1, 0)
        _icons[(exe, size)] = (handles[0] or 0) if got == 1 else 0
    return _icons[(exe, size)]


def window_icons(exe: str) -> None:
    """This process's windows with the exe's icon, at the sizes Windows draws
    for each window's DPI (the title bar's small one, Alt+Tab's big one).
    GTK gives Windows a 48-pixel icon, which the title bar shrinks badly to
    16."""
    user32 = ctypes.windll.user32
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    pid = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            dpi = user32.GetDpiForWindow(hwnd) or 96
            for kind, metric in ((ICON_SMALL, SM_CXSMICON), (ICON_BIG, SM_CXICON)):
                icon = _exe_icon(exe, user32.GetSystemMetricsForDpi(metric, dpi))
                if icon:
                    user32.SendMessageW(hwnd, WM_SETICON, kind, icon)
        return True

    user32.EnumWindows(visit, 0)


# -- a file that runs when it is opened ---------------------------------------------

# What Windows runs, rather than shows, when such a file is opened: programs,
# scripts, installers, shortcuts, settings files. AssocIsDangerous gives the
# system's own list, which policies can change; these always count.
RUNS_WHEN_OPENED = frozenset((
    ".exe", ".com", ".scr", ".pif", ".cpl", ".msc", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".hta", ".msi", ".msp", ".mst", ".appx", ".msix", ".lnk", ".url", ".scf",
    ".reg", ".inf", ".jar", ".application", ".appref-ms", ".gadget", ".chm", ".library-ms",
    ".settingcontent-ms", ".diagcab", ".xbap", ".website",
))


def runs_when_opened(path: str) -> bool:
    """Opening this file would run it (ShellExecute): a program, a script, a
    shortcut. "Fattura.pdf.exe" counts by its last extension, as Windows
    reads it. An app that opens files it did not make (downloads, files
    shared by others) shows such a file in its folder instead."""
    ext = os.path.splitext(path)[1].lower()
    if not ext:
        return False
    if ext in RUNS_WHEN_OPENED:
        return True
    try:
        shlwapi = ctypes.WinDLL("shlwapi")
        shlwapi.AssocIsDangerous.argtypes = [wintypes.LPCWSTR]
        shlwapi.AssocIsDangerous.restype = wintypes.BOOL
        return bool(shlwapi.AssocIsDangerous(ext))
    except (AttributeError, OSError):
        return False


# -- a file on another computer -----------------------------------------------------

DRIVE_REMOTE = 4


def on_network(path: str) -> bool:
    """The file is on another computer: a UNC path, or a drive letter
    mapped to a share (GetDriveTypeW says DRIVE_REMOTE). What Scriba writes
    for such a file goes to the computer's own folders instead: a shared
    server's folder belongs to everybody (trascrivi.py did the same)."""
    full = os.path.abspath(path)
    sep = os.sep
    if full.startswith(sep + sep):
        return True
    drive = os.path.splitdrive(full)[0]
    if not drive:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetDriveTypeW.restype = wintypes.UINT
    return kernel32.GetDriveTypeW(drive + sep) == DRIVE_REMOTE
