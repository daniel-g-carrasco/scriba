#!/usr/bin/env python3
"""Where the engines are found: the frozen build's own folder and only it;
from the sources the variable, the checkout's own build, the PATH."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup

from scriba.app.engines import bundle, ffmpeg, onnx, whisper

EXE = ".exe" if _setup.WINDOWS else ""


def program(folder: Path, name: str) -> Path:
    path = folder / (name + EXE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"made up")
    path.chmod(0o755)                   # shutil.which wants it runnable on Linux
    return path


class FrozenTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.inside = self.tmp / "_internal"
        self.elsewhere = self.tmp / "msys64"

    def frozen(self):
        return mock.patch.multiple(bundle, FROZEN=True), mock.patch.object(sys, "_MEIPASS", str(self.inside),
                                                                             create=True)

    def test_the_frozen_build_takes_its_own_engines_and_not_the_environment_s(self):
        own = program(self.inside, "ffmpeg")
        other = program(self.elsewhere, "ffmpeg")
        environ = {"PATH": str(self.elsewhere), ffmpeg.FFMPEG_VARIABLE: str(other),
                   whisper.CLI_VARIABLE: str(program(self.elsewhere, "whisper-cli"))}
        first, second = self.frozen()
        with first, second, mock.patch.dict(os.environ, environ):
            self.assertEqual(ffmpeg.find(), own)
            self.assertIsNone(ffmpeg.find("ffprobe"))           # not in the build: missing, not MSYS2's
            self.assertIsNone(whisper.find())
            self.assertEqual(bundle.folder(), self.inside)

    def test_the_frozen_build_loads_its_own_onnxruntime_only(self):
        name = onnx.library_names()[0]
        (self.elsewhere).mkdir()
        (self.elsewhere / name).write_bytes(b"made up")
        first, second = self.frozen()
        with first, second, mock.patch.dict(os.environ, {"PATH": str(self.elsewhere)}):
            self.assertIsNone(onnx.find_library())
            self.inside.mkdir()
            (self.inside / name).write_bytes(b"made up")
            self.assertEqual(onnx.find_library(), self.inside / name)


class EntryPointTest(unittest.TestCase):
    """build-aux/windows/scriba_main.py, what the frozen build runs first."""

    @staticmethod
    def entry():
        import importlib.util
        path = _setup.ROOT / "build-aux" / "windows" / "scriba_main.py"
        spec = importlib.util.spec_from_file_location("scriba_main_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)             # not __main__: nothing runs
        return module

    def test_gstreamer_takes_the_build_s_plugins_and_a_registry_of_the_user_s(self):
        from scriba import __version__
        with tempfile.TemporaryDirectory() as tmp:
            environ = {"GST_PLUGIN_PATH": r"C:\msys64\ucrt64\lib\gstreamer-1.0", "GST_PLUGIN_PATH_1_0": "x",
                       "GST_PLUGIN_SYSTEM_PATH_1_0": "y", "GST_REGISTRY_1_0": "z",
                       "XDG_CONFIG_HOME": os.path.join(tmp, "config"), "XDG_CACHE_HOME": os.path.join(tmp, "cache")}
            with mock.patch.dict(os.environ, environ):
                self.entry().own_gstreamer(os.path.join(tmp, "_internal"))
                for name in ("GST_PLUGIN_PATH", "GST_PLUGIN_PATH_1_0", "GST_PLUGIN_SYSTEM_PATH_1_0",
                             "GST_REGISTRY_1_0"):
                    self.assertNotIn(name, os.environ)
                self.assertEqual(os.environ["GST_PLUGIN_SYSTEM_PATH"], os.path.join(tmp, "_internal", "gst_plugins"))
                registry = Path(os.environ["GST_REGISTRY"])
                self.assertEqual(registry.parent, Path(tmp) / "cache" / "scriba")
                self.assertIn(__version__, registry.name)
                self.assertTrue(registry.parent.is_dir())


class SourcesTest(unittest.TestCase):
    def test_a_flatpak_library_is_found_beside_the_installed_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "share" / "scriba" / "scriba"
            package.mkdir(parents=True)
            lib = root / "lib" / onnx.library_names()[0]
            lib.parent.mkdir()
            lib.write_bytes(b"made up")
            with mock.patch.object(bundle, "PACKAGE_DIR", package), \
                    mock.patch.object(bundle, "FROZEN", False), \
                    mock.patch.dict(os.environ, {"PATH": ""}):
                os.environ.pop(onnx.LIBRARY_VARIABLE, None)
                self.assertEqual(bundle.library(onnx.library_names()), lib)

    def test_the_variable_then_the_checkout_s_build_then_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            on_path = program(tmp / "path", "ffmpeg")
            program(tmp / "path", "ffprobe")
            built = program(tmp / "build", "ffmpeg")
            given = program(tmp / "given", "ffmpeg")
            program(tmp / "given", "ffprobe")
            with mock.patch.dict(os.environ, {"PATH": str(tmp / "path")}), \
                    mock.patch.object(bundle, "LOCAL_BUILDS", (tmp / "build",)):
                os.environ.pop(ffmpeg.FFMPEG_VARIABLE, None)
                self.assertEqual(ffmpeg.find(), built)
                self.assertEqual(Path(ffmpeg.find("ffprobe")).parent, tmp / "path")  # not built: the PATH's
                os.environ[ffmpeg.FFMPEG_VARIABLE] = str(given)
                self.assertEqual(ffmpeg.find(), given)
                self.assertEqual(ffmpeg.find("ffprobe"), given.with_name("ffprobe" + EXE))
            with mock.patch.dict(os.environ, {"PATH": str(tmp / "path")}), \
                    mock.patch.object(bundle, "LOCAL_BUILDS", ()):
                os.environ.pop(ffmpeg.FFMPEG_VARIABLE, None)
                self.assertEqual(Path(ffmpeg.find()).resolve(), on_path.resolve())


if __name__ == "__main__":
    unittest.main()
