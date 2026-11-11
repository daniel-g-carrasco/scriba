"""onnxruntime's C library, called with ctypes: the speech filter and the
voice embedder are ONNX models, and this is how they run.

Why not `import onnxruntime`: MSYS2's python-onnxruntime 1.29.0-2 ships
without its native module (onnxruntime_pybind11_state is missing, also
from the wheel's RECORD), so the import fails; the C package
(mingw-w64-ucrt-x86_64-onnxruntime) has a working onnxruntime.dll, and
through it the two models give the same numbers as the Python module on
the system's Python on the reference recordings (identical segments). Only the few calls the two models need are
declared: an environment, a session, float32 and int64 tensors in, float32
tensors out.

The functions are found in the OrtApi table by their index, as the
header onnxruntime_c_api.h numbers them (ORT_API_VERSION 29): the table
only ever grows, so an index never changes between versions, and asking
for version 29 fails cleanly on a library older than that.

Every ctypes call has its argument and result types declared: without
them a 64-bit pointer comes back cut to 32 bits, one time in ten.

ctypes is imported at the top here, against the rule of the app (heavy
imports inside the functions), because every line of the module uses it;
the module itself is imported only inside the functions of vad.py and
diarize.py, so the command line does not load it (tests/test_engines.py
holds that).
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path

from scriba import ENV_PREFIX
from scriba.app.task import Failure
from scriba.i18n import _

API_VERSION = 29
LIBRARY_VARIABLE = f"{ENV_PREFIX}_ONNXRUNTIME"      # a library elsewhere (a developer's build)
# OrtApi's functions by index (onnxruntime_c_api.h, version 29)
_INDEX = dict(GetErrorMessage=2, CreateEnv=3, CreateSession=7, Run=9, CreateSessionOptions=10,
              SetIntraOpNumThreads=24, SetInterOpNumThreads=25, CreateTensorWithDataAsOrtValue=49,
              GetTensorMutableData=51, GetDimensionsCount=61, GetDimensions=62, GetTensorTypeAndShape=65,
              CreateCpuMemoryInfo=69, ReleaseEnv=92, ReleaseStatus=93, ReleaseMemoryInfo=94, ReleaseSession=95,
              ReleaseValue=96, ReleaseTensorTypeAndShapeInfo=99, ReleaseSessionOptions=100)
_FLOAT, _INT64 = 1, 7               # ONNXTensorElementDataType
_LOGGING_ERROR = 3                  # ORT_LOGGING_LEVEL_ERROR: the library says nothing unless it fails

_lock = threading.Lock()
_api: _Api | None = None


def library_names() -> list[str]:
    if sys.platform == "win32":
        return ["onnxruntime.dll"]
    if sys.platform == "darwin":
        return ["libonnxruntime.dylib"]
    return ["libonnxruntime.so", "libonnxruntime.so.1"]


def find_library() -> Path | None:
    """onnxruntime's library: the frozen build's own; from the sources the
    variable's, next to the Python that runs (MSYS2's ucrt64/bin), or on
    the PATH (bundle.py)."""
    from . import bundle
    found = bundle.library(library_names(), LIBRARY_VARIABLE,
                           (Path(sys.prefix) / "bin", Path(sys.prefix) / "lib", Path(sys.executable).parent))
    if found is not None or bundle.FROZEN:
        return found
    if sys.platform != "win32":
        import ctypes.util
        found = ctypes.util.find_library("onnxruntime")
        if found:
            return Path(found)
    return None


class _Api:
    def __init__(self, path: Path) -> None:
        from ctypes import POINTER, c_char_p, c_int, c_int64, c_size_t, c_uint32, c_void_p
        if sys.platform == "win32":
            os.add_dll_directory(str(path.parent))
        self.dll = ctypes.CDLL(str(path))
        self.dll.OrtGetApiBase.argtypes = []
        self.dll.OrtGetApiBase.restype = c_void_p
        base = ctypes.cast(self.dll.OrtGetApiBase(), POINTER(c_void_p))
        self.version = ctypes.CFUNCTYPE(c_char_p)(base[1])().decode()
        table = ctypes.CFUNCTYPE(c_void_p, c_uint32)(base[0])(API_VERSION)
        if not table:
            raise Failure(_("The installed onnxruntime is too old for Scriba."), f"onnxruntime {self.version}")
        self.table = ctypes.cast(table, POINTER(c_void_p))
        # ORTCHAR_T: wchar_t on Windows, char elsewhere
        self.path_type = ctypes.c_wchar_p if sys.platform == "win32" else c_char_p
        P = POINTER
        f = self._function
        self.GetErrorMessage = f("GetErrorMessage", c_char_p, c_void_p)
        self.ReleaseStatus = f("ReleaseStatus", None, c_void_p)
        self.CreateEnv = f("CreateEnv", c_void_p, c_int, c_char_p, P(c_void_p))
        self.CreateSessionOptions = f("CreateSessionOptions", c_void_p, P(c_void_p))
        self.SetIntraOpNumThreads = f("SetIntraOpNumThreads", c_void_p, c_void_p, c_int)
        self.SetInterOpNumThreads = f("SetInterOpNumThreads", c_void_p, c_void_p, c_int)
        self.CreateSession = f("CreateSession", c_void_p, c_void_p, self.path_type, c_void_p, P(c_void_p))
        self.ReleaseSession = f("ReleaseSession", None, c_void_p)
        self.CreateCpuMemoryInfo = f("CreateCpuMemoryInfo", c_void_p, c_int, c_int, P(c_void_p))
        self.CreateTensor = f("CreateTensorWithDataAsOrtValue", c_void_p, c_void_p, c_void_p, c_size_t, P(c_int64),
                              c_size_t, c_int, P(c_void_p))
        self.Run = f("Run", c_void_p, c_void_p, c_void_p, P(c_char_p), P(c_void_p), c_size_t, P(c_char_p), c_size_t,
                     P(c_void_p))
        self.GetTensorMutableData = f("GetTensorMutableData", c_void_p, c_void_p, P(c_void_p))
        self.GetTensorTypeAndShape = f("GetTensorTypeAndShape", c_void_p, c_void_p, P(c_void_p))
        self.GetDimensionsCount = f("GetDimensionsCount", c_void_p, c_void_p, P(c_size_t))
        self.GetDimensions = f("GetDimensions", c_void_p, c_void_p, P(c_int64), c_size_t)
        self.ReleaseValue = f("ReleaseValue", None, c_void_p)
        self.ReleaseTensorTypeAndShapeInfo = f("ReleaseTensorTypeAndShapeInfo", None, c_void_p)
        self.ReleaseSessionOptions = f("ReleaseSessionOptions", None, c_void_p)
        # one environment and one description of CPU memory for the process,
        # never released: they live as long as the library
        self.env = c_void_p()
        self.ok(self.CreateEnv(_LOGGING_ERROR, b"scriba", ctypes.byref(self.env)))
        self.memory = c_void_p()
        self.ok(self.CreateCpuMemoryInfo(1, 0, ctypes.byref(self.memory)))      # arena allocator, default memory

    def _function(self, name, restype, *argtypes):
        return ctypes.CFUNCTYPE(restype, *argtypes)(self.table[_INDEX[name]])

    def ok(self, status) -> None:
        """A non-null OrtStatus is an error: its message, and the status freed."""
        if status:
            message = self.GetErrorMessage(status).decode("utf-8", "replace")
            self.ReleaseStatus(status)
            raise Failure(_("The speech engine (onnxruntime) failed."), message)


def api() -> _Api:
    """The library, loaded once. Failure, with a sentence to show, when it
    is not there."""
    global _api
    with _lock:
        if _api is None:
            path = find_library()
            if path is None:
                raise Failure(_("onnxruntime is missing: Scriba needs it to find speech and tell the voices apart."),
                              "no " + " or ".join(library_names()))
            try:
                _api = _Api(path)
            except OSError as e:
                raise Failure(_("onnxruntime cannot be loaded."), f"{path}: {e}") from e
        return _api


class Session:
    """An ONNX model ready to run, with run() as onnxruntime's own
    InferenceSession has it: names of the outputs, a map of the inputs,
    a list of float32 arrays back. One thread at a time per session."""

    def __init__(self, model: str | os.PathLike, threads: int = 1) -> None:
        o = self.api = api()
        options = ctypes.c_void_p()
        o.ok(o.CreateSessionOptions(ctypes.byref(options)))
        try:
            o.ok(o.SetIntraOpNumThreads(options, max(1, int(threads))))
            o.ok(o.SetInterOpNumThreads(options, 1))
            session = ctypes.c_void_p()
            name = os.fspath(model) if sys.platform == "win32" else os.fsencode(model)
            o.ok(o.CreateSession(o.env, name, options, ctypes.byref(session)))
        finally:
            o.ReleaseSessionOptions(options)
        self.session = session

    def close(self) -> None:
        if self.session:
            self.api.ReleaseSession(self.session)
            self.session = ctypes.c_void_p()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def run(self, outputs: list[str], inputs: dict):
        import numpy as np
        o = self.api
        types = {np.dtype(np.float32): _FLOAT, np.dtype(np.int64): _INT64}
        kept, values = [], []
        try:
            for array in inputs.values():
                array = np.ascontiguousarray(array)
                shape = (ctypes.c_int64 * max(array.ndim, 1))(*array.shape)
                value = ctypes.c_void_p()
                o.ok(o.CreateTensor(o.memory, array.ctypes.data, array.nbytes, shape, array.ndim, types[array.dtype],
                                    ctypes.byref(value)))
                kept.append(array)          # the tensor uses the array's memory: it must live until Run is over
                values.append(value)
            names_in = (ctypes.c_char_p * len(values))(*[k.encode() for k in inputs])
            values_in = (ctypes.c_void_p * len(values))(*[v.value for v in values])
            names_out = (ctypes.c_char_p * len(outputs))(*[k.encode() for k in outputs])
            values_out = (ctypes.c_void_p * len(outputs))()
            o.ok(o.Run(self.session, None, names_in, values_in, len(values), names_out, len(outputs), values_out))
            out = []
            for pointer in values_out:
                try:
                    info = ctypes.c_void_p()
                    o.ok(o.GetTensorTypeAndShape(pointer, ctypes.byref(info)))
                    count = ctypes.c_size_t()
                    o.ok(o.GetDimensionsCount(info, ctypes.byref(count)))
                    dims = (ctypes.c_int64 * max(count.value, 1))()
                    o.ok(o.GetDimensions(info, dims, count.value))
                    o.ReleaseTensorTypeAndShapeInfo(info)
                    shape = tuple(dims[i] for i in range(count.value))
                    data = ctypes.c_void_p()
                    o.ok(o.GetTensorMutableData(pointer, ctypes.byref(data)))
                    n = int(np.prod(shape)) if shape else 1
                    buffer = (ctypes.c_float * n).from_address(data.value)
                    out.append(np.frombuffer(buffer, dtype=np.float32).reshape(shape).copy())
                finally:
                    o.ReleaseValue(pointer)
            return out
        finally:
            for value in values:
                o.ReleaseValue(value)
