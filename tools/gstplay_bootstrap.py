#!/usr/bin/env python3
"""Select and verify the custom GstPlay DLL for Windows source CI only."""
import ctypes
import os
from pathlib import Path
import runpy
import sys

from gstplay_receipt import read_receipt

_LIBRARY = None
_DIRECTORY = None


def preload():
    global _LIBRARY, _DIRECTORY
    prefix = os.environ.get('SCRIBA_GSTPLAY_BUILD')
    if not prefix or sys.platform != 'win32' or _LIBRARY is not None:
        return
    root = Path(prefix)
    if not root.is_absolute():
        raise ValueError('SCRIBA_GSTPLAY_BUILD must be an absolute verified build prefix')
    receipt = read_receipt(root / 'BUILD.json')
    dll = root / next(f['file'] for f in receipt['outputs'] if f['file'].endswith('.dll'))
    # Keep both handles alive before GTK or any GStreamer consumer is imported.
    _DIRECTORY = os.add_dll_directory(str(Path(sys.prefix) / 'bin'))
    library = ctypes.WinDLL(str(dll))
    get_filename = ctypes.windll.kernel32.GetModuleFileNameW
    get_filename.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    get_filename.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_filename(library._handle, buffer, len(buffer))
    if not length or length >= len(buffer) or Path(buffer.value).resolve() != dll.resolve():
        raise RuntimeError('The loaded GstPlay DLL is not the verified custom build')
    _LIBRARY = library
    print(f'Verified loaded GstPlay: {buffer.value}', file=sys.stderr, flush=True)


if __name__ == '__main__':
    preload()
    sys.argv = sys.argv[1:]
    if not sys.argv:
        raise SystemExit('Pass the Python script to run with the verified GstPlay library')
    runpy.run_path(sys.argv[0], run_name='__main__')
