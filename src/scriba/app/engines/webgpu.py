"""Official ORT plugin API via C, independent of the CPython extension ABI.

Append-only API indices verified against onnxruntime_c_api.h 1.29. The
plugin and matching official runtime live together in community-runtime.
Only this job's child process loads them; Silero keeps its existing runtime.
"""
from __future__ import annotations
import ctypes as c
import os
from pathlib import Path
from . import onnx, bundle


class Unavailable(RuntimeError):
    pass


def runtime_folder():
    root = bundle.folder()
    if root is not None:
        return root / 'community-runtime'
    return Path(os.environ.get('SCRIBA_COMMUNITY_RUNTIME', '/app/lib/scriba-community'))


class Runtime:
    def __init__(self):
        root = runtime_folder()
        library = root / ('onnxruntime.dll' if os.name == 'nt' else 'libonnxruntime.so.1.32.0')
        plugin = root / ('onnxruntime_providers_webgpu.dll' if os.name == 'nt' else 'libonnxruntime_providers_webgpu.so')
        if not library.is_file() or not plugin.is_file():
            raise Unavailable('Official WebGPU runtime or plugin missing')
        try:
            self.api = onnx._Api(library)
            self.api.ok(self.function(301, c.c_void_p, c.c_void_p, c.c_char_p, self.api.path_type)(
                self.api.env, b'scriba-webgpu', str(plugin.resolve()) if os.name == 'nt' else os.fsencode(plugin.resolve())))
            devices, count = c.POINTER(c.c_void_p)(), c.c_size_t()
            self.api.ok(self.function(303, c.c_void_p, c.c_void_p, c.POINTER(c.POINTER(c.c_void_p)),
                                     c.POINTER(c.c_size_t))(self.api.env, c.byref(devices), c.byref(count)))
            name = self.function(312, c.c_char_p, c.c_void_p)
            self.device = next((devices[i] for i in range(count.value)
                                if name(devices[i]) == b'WebGpuExecutionProvider'), None)
            if not self.device:
                raise Unavailable('No WebGPU device')
        except (OSError, onnx.Failure) as e:
            raise Unavailable(str(e)) from e

    def function(self, index, result, *arguments):
        return c.CFUNCTYPE(result, *arguments)(self.api.table[index])

    def session(self, path, threads, gpu):
        o = self.api
        options = c.c_void_p()
        o.ok(o.CreateSessionOptions(c.byref(options)))
        result = onnx.Session.__new__(onnx.Session)
        result.api, result.session = o, c.c_void_p()
        try:
            o.ok(o.SetIntraOpNumThreads(options, threads))
            o.ok(o.SetInterOpNumThreads(options, 1))
            if gpu:
                keys = (c.c_char_p * 3)(b'dawnBackendType',b'adapterIndex',b'enableGraphCapture')
                values = (c.c_char_p * 3)(b'Vulkan',b'0',b'0')
                devices = (c.c_void_p * 1)(self.device)
                o.ok(self.function(304, c.c_void_p, c.c_void_p,c.c_void_p,c.POINTER(c.c_void_p),c.c_size_t,
                                   c.POINTER(c.c_char_p),c.POINTER(c.c_char_p),c.c_size_t)(
                    options,o.env,devices,1,keys,values,3))
            name = str(path) if os.name == 'nt' else os.fsencode(path)
            o.ok(o.CreateSession(o.env,name,options,c.byref(result.session)))
        finally:
            o.ReleaseSessionOptions(options)
        return result


def session_factory(provenance):
    """GPU failure retries only the embedding batch on CPU, with provenance."""
    try:
        runtime = Runtime()
    except Unavailable as error:
        provenance.update(accelerator='cpu', fallback=str(error))
        return lambda path, threads: onnx.Session(path, threads=threads)
    provenance.update(accelerator='webgpu-vulkan', runtime=runtime.api.version)

    class Session:
        def __init__(self, path, threads):
            self.path, self.threads = path, threads
            self.gpu = Path(path).name.endswith('embedding.onnx')
            try:
                self.inner = runtime.session(path, threads, self.gpu)
            except (OSError, onnx.Failure) as error:
                if not self.gpu:
                    raise
                self.fallback(error)

        def fallback(self, error):
            provenance.update(accelerator='cpu', fallback=str(error))
            self.gpu = False
            self.inner = runtime.session(self.path,self.threads,False)

        def __enter__(self):
            return self

        def __exit__(self,*exc):
            self.inner.close()

        def run(self,*args):
            try:
                return self.inner.run(*args)
            except (OSError, onnx.Failure) as error:
                if not self.gpu:
                    raise
                self.inner.close()
                self.fallback(error)
                return self.inner.run(*args)
    return Session
