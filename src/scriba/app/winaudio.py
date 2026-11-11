"""Windows' sound devices as the Core Audio API tells them, through ctypes
and COM by hand (no comtypes): what GStreamer's device list does not say
and the choice of the call's devices needs (core/devices.py).

For every active endpoint: its id (IMMDevice::GetId, the same string
wasapi2src takes as `device`), its name, the physical device it belongs to
(PKEY_Device_ContainerId: a headset's microphone and earphones share it),
whether it is Bluetooth (PKEY_Device_EnumeratorName "BTH..."), whether it
is the default for the console role and for the communications role (two
different defaults: a headset is usually the second), the programs that
have it open now (IAudioSessionManager2: Teams' session on the headset),
and its peak meter (IAudioMeterInformation). Measured on 11/11/2026: an
output's meter moves with what plays on it, no stream needed; an input's
meter stays at 0 unless some program captures from it, so it says "in use
and hearing something", not the room's level.

Imported only on Windows, and only from a thread of the recorder's: COM is
initialised there for that thread (multithreaded apartment), never on the
window's thread. Every call has its argtypes and restype, through the
prototypes made in `_method`.
"""

from __future__ import annotations

import ctypes
import math
import os
import uuid
from ctypes import wintypes

from scriba.core.devices import INPUT, OUTPUT, Device

COINIT_MULTITHREADED = 0
CLSCTX_ALL = 23
DEVICE_STATE_ACTIVE = 1
E_RENDER, E_CAPTURE = 0, 1
E_CONSOLE, E_COMMUNICATIONS = 0, 2
STGM_READ = 0
SESSION_ACTIVE = 1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
RPC_E_CHANGED_MODE = -2147417850       # 0x80010106: the thread is in another apartment already
VT_LPWSTR, VT_CLSID = 31, 72


class GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        guid = cls()
        ctypes.memmove(guid.data, uuid.UUID(text).bytes_le, 16)
        return guid

    def __str__(self) -> str:
        return str(uuid.UUID(bytes_le=bytes(self.data)))


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):
    # the 16 bytes of the header and the value that matter here (a pointer):
    # 24 bytes in all on 64 bits, as Windows lays it out
    _fields_ = [("vt", ctypes.c_ushort), ("reserved1", ctypes.c_ushort), ("reserved2", ctypes.c_ushort),
                ("reserved3", ctypes.c_ushort), ("pointer", ctypes.c_void_p), ("more", ctypes.c_void_p)]


CLSID_MMDEVICE_ENUMERATOR = GUID.of("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDEVICE_ENUMERATOR = GUID.of("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAUDIO_METER_INFORMATION = GUID.of("C02216F6-8C67-4B5B-9D00-D008E73E0064")
IID_IAUDIO_SESSION_MANAGER2 = GUID.of("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAUDIO_SESSION_CONTROL2 = GUID.of("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")
PKEY_FRIENDLY_NAME = PROPERTYKEY(GUID.of("a45c254e-df1c-4efd-8020-67d146a850e0"), 14)
PKEY_ENUMERATOR_NAME = PROPERTYKEY(GUID.of("a45c254e-df1c-4efd-8020-67d146a850e0"), 24)
PKEY_CONTAINER_ID = PROPERTYKEY(GUID.of("8c7ed206-3f8a-4827-b3ab-ae9e1faefc6c"), 2)

_ole32 = ctypes.WinDLL("ole32")
_ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_ole32.CoInitializeEx.restype = ctypes.c_long
_ole32.CoUninitialize.argtypes = []
_ole32.CoUninitialize.restype = None
_ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID),
                                    ctypes.POINTER(ctypes.c_void_p)]
_ole32.CoCreateInstance.restype = ctypes.c_long
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_ole32.CoTaskMemFree.restype = None
_ole32.PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]
_ole32.PropVariantClear.restype = ctypes.c_long
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_PROTOTYPES: dict = {}


def _method(obj: ctypes.c_void_p, index: int, *argtypes):
    """The `index`-th method of a COM object's vtable, typed: HRESULT
    (a long) back, the object itself first."""
    key = argtypes
    if key not in _PROTOTYPES:
        _PROTOTYPES[key] = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    vtable = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    function = _PROTOTYPES[key](vtable[index])
    return lambda *args: function(obj, *args)


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        _method(obj, 2)()


class ComError(OSError):
    pass


def _check(result: int, what: str) -> None:
    if result < 0:
        raise ComError(f"{what}: HRESULT 0x{result & 0xFFFFFFFF:08X}")


class Apartment:
    """COM for this thread, for as long as the block lasts."""

    def __enter__(self):
        result = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        self.owned = result >= 0           # S_OK or S_FALSE: ours to undo
        if result < 0 and result != RPC_E_CHANGED_MODE:
            _check(result, "CoInitializeEx")
        return self

    def __exit__(self, *_exc):
        if self.owned:
            _ole32.CoUninitialize()
        return False


def _property(store: ctypes.c_void_p, key: PROPERTYKEY) -> str:
    value = PROPVARIANT()
    if _method(store, 5, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
            ctypes.byref(key), ctypes.byref(value)) < 0:
        return ""
    try:
        if value.vt == VT_LPWSTR and value.pointer:
            return ctypes.wstring_at(value.pointer)
        if value.vt == VT_CLSID and value.pointer:
            guid = GUID()
            ctypes.memmove(guid.data, value.pointer, 16)
            return str(guid)
        return ""
    finally:
        _ole32.PropVariantClear(ctypes.byref(value))


def _id(device: ctypes.c_void_p) -> str:
    text = ctypes.c_wchar_p()
    _check(_method(device, 5, ctypes.POINTER(ctypes.c_wchar_p))(ctypes.byref(text)), "IMMDevice::GetId")
    try:
        return text.value or ""
    finally:
        _ole32.CoTaskMemFree(text)


def _activate(device: ctypes.c_void_p, iid: GUID) -> ctypes.c_void_p:
    found = ctypes.c_void_p()
    _check(_method(device, 3, ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p,
                   ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(iid), CLSCTX_ALL, None, ctypes.byref(found)),
           "IMMDevice::Activate")
    return found


def _peak(device: ctypes.c_void_p) -> float | None:
    """The endpoint's meter now, dBFS; None when it cannot be read."""
    try:
        meter = _activate(device, IID_IAUDIO_METER_INFORMATION)
    except ComError:
        return None
    try:
        value = ctypes.c_float()
        if _method(meter, 3, ctypes.POINTER(ctypes.c_float))(ctypes.byref(value)) < 0:
            return None
        return 20 * math.log10(value.value) if value.value > 0 else -math.inf
    finally:
        _release(meter)


def program_of(pid: int) -> str:
    """The executable of a process, as a path; "" when it cannot be told
    (a process of another user, or gone)."""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        _kernel32.CloseHandle(handle)


def _programs(device: ctypes.c_void_p, own: int) -> tuple[str, ...]:
    """The programs with an active session on the endpoint, ours left out."""
    try:
        manager = _activate(device, IID_IAUDIO_SESSION_MANAGER2)
    except ComError:
        return ()
    found: list[str] = []
    sessions = ctypes.c_void_p()
    try:
        if _method(manager, 5, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(sessions)) < 0:
            return ()
        count = ctypes.c_int()
        _method(sessions, 3, ctypes.POINTER(ctypes.c_int))(ctypes.byref(count))
        for i in range(count.value):
            control = ctypes.c_void_p()
            if _method(sessions, 4, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(i, ctypes.byref(control)) < 0:
                continue
            control2 = ctypes.c_void_p()
            try:
                state = ctypes.c_int()
                _method(control, 3, ctypes.POINTER(ctypes.c_int))(ctypes.byref(state))
                if state.value != SESSION_ACTIVE:
                    continue
                if _method(control, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                        ctypes.byref(IID_IAUDIO_SESSION_CONTROL2), ctypes.byref(control2)) < 0:
                    continue
                pid = wintypes.DWORD()
                _method(control2, 14, ctypes.POINTER(wintypes.DWORD))(ctypes.byref(pid))
                if pid.value in (0, own):
                    continue            # the system's sounds, or our own streams
                program = program_of(pid.value)
                if program and program not in found:
                    found.append(program)
            finally:
                _release(control2)
                _release(control)
    finally:
        _release(sessions)
        _release(manager)
    return tuple(found)


def _defaults(enumerator: ctypes.c_void_p, flow: int) -> tuple[str, str]:
    """The ids of the console's and the communications' default endpoints."""
    out = []
    for role in (E_CONSOLE, E_COMMUNICATIONS):
        device = ctypes.c_void_p()
        if _method(enumerator, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                flow, role, ctypes.byref(device)) < 0:
            out.append("")
            continue
        try:
            out.append(_id(device))
        finally:
            _release(device)
    return out[0], out[1]


def endpoints(sessions: bool = True) -> list[Device]:
    """Every active input and output, as core/devices.py takes them, the
    outputs first; `sessions` False leaves the programs out (they cost a
    few milliseconds per device). ComError when Core Audio does not
    answer."""
    own = os.getpid()
    found: list[Device] = []
    with Apartment():
        enumerator = ctypes.c_void_p()
        _check(_ole32.CoCreateInstance(ctypes.byref(CLSID_MMDEVICE_ENUMERATOR), None, CLSCTX_ALL,
                                       ctypes.byref(IID_IMMDEVICE_ENUMERATOR), ctypes.byref(enumerator)),
               "CoCreateInstance(MMDeviceEnumerator)")
        try:
            for flow, kind in ((E_RENDER, OUTPUT), (E_CAPTURE, INPUT)):
                console, communications = _defaults(enumerator, flow)
                collection = ctypes.c_void_p()
                _check(_method(enumerator, 3, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
                    flow, DEVICE_STATE_ACTIVE, ctypes.byref(collection)), "EnumAudioEndpoints")
                try:
                    count = wintypes.UINT()
                    _method(collection, 3, ctypes.POINTER(wintypes.UINT))(ctypes.byref(count))
                    for i in range(count.value):
                        device = ctypes.c_void_p()
                        if _method(collection, 4, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(
                                i, ctypes.byref(device)) < 0:
                            continue
                        try:
                            found.append(_describe(device, kind, console, communications, own, sessions))
                        finally:
                            _release(device)
                finally:
                    _release(collection)
        finally:
            _release(enumerator)
    return found


def _describe(device: ctypes.c_void_p, kind: str, console: str, communications: str, own: int,
              sessions: bool) -> Device:
    ident = _id(device)
    store = ctypes.c_void_p()
    name = container = enumerator = ""
    if _method(device, 4, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(STGM_READ, ctypes.byref(store)) >= 0:
        try:
            name = _property(store, PKEY_FRIENDLY_NAME)
            container = _property(store, PKEY_CONTAINER_ID)
            enumerator = _property(store, PKEY_ENUMERATOR_NAME)
        finally:
            _release(store)
    return Device(id=ident, name=name or ident, kind=kind, default=ident == console,
                  communications=ident == communications, container=container,
                  bluetooth=enumerator.upper().startswith("BTH"),
                  programs=_programs(device, own) if sessions else (), peak=_peak(device))
