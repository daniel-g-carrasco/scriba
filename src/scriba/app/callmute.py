"""Windows 11's microphone indicator in the notification area, read with UI
Automation through COM and ctypes, and followed once a second while a
recording asks for it (core/callmute.py says what the words mean).

The indicator is a descendant of the taskbar's window (class Shell_TrayWnd)
with the AutomationId "SystemTrayIcon" and the class
"SystemTray.AccentButton", whose name begins with the word for microphone;
found the same way PowerShell's System.Windows.Automation found it on
2026-11-13. Windows only; one IUIAutomation per thread (COM's apartment).

Every COM method is called through its slot in the interface's table, with
the argument and result types set (UIAutomationClient.h's order).
"""

from __future__ import annotations

import ctypes
import threading
import uuid
from collections.abc import Callable
from ctypes import wintypes

from scriba.app.log import complain
from scriba.core import callmute

EVERY_S = 1.0
TRAY_CLASS = "Shell_TrayWnd"
INDICATOR_ID = "SystemTrayIcon"
INDICATOR_CLASS = "SystemTray.AccentButton"
MICROPHONE_WORDS = ("microfono", "microphone")      # the name's first word: the microphone's indicator

UIA_AUTOMATION_ID = 30011       # UIA_AutomationIdPropertyId
TREE_SCOPE_DESCENDANTS = 4
VT_BSTR = 8
CLSCTX_INPROC_SERVER = 1
RPC_E_CHANGED_MODE = 0x80010106

# slots in the interfaces' tables (IUnknown takes 0-2)
IUIA_ELEMENT_FROM_HANDLE, IUIA_CREATE_PROPERTY_CONDITION = 6, 23
ELEMENT_FIND_ALL, ELEMENT_NAME, ELEMENT_CLASS_NAME = 6, 23, 30
ARRAY_LENGTH, ARRAY_ELEMENT = 3, 4


class _GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16), ("data3", ctypes.c_uint16),
                ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> _GUID:
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class _VARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("value", ctypes.c_void_p), ("extra", ctypes.c_void_p)]


CLSID_CUIAUTOMATION = _GUID.parse("ff48dba4-60ef-4201-aa87-54103eef594e")
IID_IUIAUTOMATION = _GUID.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")


def _method(pointer, slot: int, *types):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[slot])


def _release(pointer) -> None:
    if pointer:
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(pointer)


def _checked(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what}: 0x{hr & 0xffffffff:08X}")


class Indicator:
    """The microphone indicator, read on the thread that made this object.
    `read()`: its name, None when there is none (no app uses the microphone);
    OSError when the reading fails. close() when done."""

    def __init__(self) -> None:
        self._ole = ctypes.WinDLL("ole32")
        self._oleaut = ctypes.WinDLL("oleaut32")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        self._ole.CoInitializeEx.restype = ctypes.c_long
        self._ole.CoUninitialize.argtypes = []
        self._ole.CoUninitialize.restype = None
        self._ole.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
                                               ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        self._ole.CoCreateInstance.restype = ctypes.c_long
        self._oleaut.SysAllocString.argtypes = [wintypes.LPCWSTR]
        self._oleaut.SysAllocString.restype = ctypes.c_void_p
        self._oleaut.SysFreeString.argtypes = [ctypes.c_void_p]
        self._oleaut.SysFreeString.restype = None
        self._user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        self._user32.FindWindowW.restype = wintypes.HWND
        started = self._ole.CoInitializeEx(None, 0)      # COINIT_MULTITHREADED: a worker of its own
        if started < 0 and started & 0xffffffff != RPC_E_CHANGED_MODE:
            _checked(started, "CoInitializeEx")
        self._started = started >= 0
        self._uia = ctypes.c_void_p()
        self._condition = ctypes.c_void_p()
        try:
            _checked(self._ole.CoCreateInstance(ctypes.byref(CLSID_CUIAUTOMATION), None, CLSCTX_INPROC_SERVER,
                                                ctypes.byref(IID_IUIAUTOMATION), ctypes.byref(self._uia)),
                     "CoCreateInstance(CUIAutomation)")
            text = self._oleaut.SysAllocString(INDICATOR_ID)
            try:
                value = _VARIANT(vt=VT_BSTR, value=text)
                _checked(_method(self._uia, IUIA_CREATE_PROPERTY_CONDITION, ctypes.c_int, _VARIANT,
                                 ctypes.POINTER(ctypes.c_void_p))(self._uia, UIA_AUTOMATION_ID, value,
                                                                  ctypes.byref(self._condition)),
                         "CreatePropertyCondition")
            finally:
                self._oleaut.SysFreeString(text)
        except BaseException:
            self.close()
            raise

    def _text(self, element, slot: int) -> str:
        bstr = ctypes.c_void_p()
        _checked(_method(element, slot, ctypes.POINTER(ctypes.c_void_p))(element, ctypes.byref(bstr)), "property")
        try:
            return ctypes.wstring_at(bstr.value) if bstr.value else ""
        finally:
            self._oleaut.SysFreeString(bstr)

    def read(self) -> str | None:
        tray = self._user32.FindWindowW(TRAY_CLASS, None)
        if not tray:
            return None
        root, found = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            _checked(_method(self._uia, IUIA_ELEMENT_FROM_HANDLE, wintypes.HWND, ctypes.POINTER(ctypes.c_void_p))(
                self._uia, tray, ctypes.byref(root)), "ElementFromHandle")
            _checked(_method(root, ELEMENT_FIND_ALL, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                root, TREE_SCOPE_DESCENDANTS, self._condition, ctypes.byref(found)), "FindAll")
            if not found:
                return None
            count = ctypes.c_int()
            _checked(_method(found, ARRAY_LENGTH, ctypes.POINTER(ctypes.c_int))(found, ctypes.byref(count)), "Length")
            for i in range(count.value):
                element = ctypes.c_void_p()
                _checked(_method(found, ARRAY_ELEMENT, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                    found, i, ctypes.byref(element)), "GetElement")
                try:
                    if self._text(element, ELEMENT_CLASS_NAME) != INDICATOR_CLASS:
                        continue
                    name = self._text(element, ELEMENT_NAME)
                    if name.strip().lower().startswith(MICROPHONE_WORDS):
                        return name
                finally:
                    _release(element)
            return None
        finally:
            _release(found)
            _release(root)

    def close(self) -> None:
        _release(self._condition)
        _release(self._uia)
        self._condition, self._uia = ctypes.c_void_p(), ctypes.c_void_p()
        if self._started:
            self._ole.CoUninitialize()
            self._started = False


def follow(told: Callable[[str], None], stop: threading.Event, every: float = EVERY_S,
           opener: Callable[[], Indicator] = Indicator) -> None:
    """Until `stop` is set: the indicator read every `every` seconds and its
    state (core/callmute.py) given to `told`, also when it did not change.
    A failed reading keeps the last state; the indicator that cannot be
    opened at all gives UNKNOWN once and the thread ends (said in the log)."""
    reading = callmute.Reading()
    try:
        indicator = opener()
    except OSError as e:
        complain("opening UI Automation for the call's mute", e)
        told(callmute.UNKNOWN)
        return
    try:
        while not stop.wait(every):
            try:
                state = reading.read(indicator.read())
            except OSError:
                state = reading.failed()
            told(state)
    finally:
        indicator.close()
