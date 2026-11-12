"""The small native backport must retain exact, verifiable build inputs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_gstplay', ROOT / 'tools/build_gstplay.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class GstPlayBuildTest(unittest.TestCase):
    def test_cached_source_is_always_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'source.tar.xz'
            p.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                build.acquire('https://example.invalid/source', p, '0' * 64)

    def test_receipt_requires_current_recipe_and_every_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / 'bin').mkdir()
            dll = p / 'bin/libgstplay.dll'
            dll.write_bytes(b'library')
            r = {'schema_version': 1, 'component': 'gstplay', 'version': '1.28.9',
                 'cache_key': 'key', 'outputs': [{'file': 'bin/libgstplay.dll',
                 'sha256': hashlib.sha256(dll.read_bytes()).hexdigest()}]}
            (p / 'BUILD.json').write_text(json.dumps(r))
            self.assertTrue(build.cached(p, 'key'))
            self.assertFalse(build.cached(p, 'changed-recipe'))
            dll.write_bytes(b'corrupted')
            self.assertFalse(build.cached(p, 'key'))

    def test_receipt_cannot_point_outside_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            for name in ['../outside', '/etc/passwd', 'C:/outside', 'bin\\outside']:
                with self.subTest(name=name):
                    with self.assertRaises(ValueError):
                        build.relative_file(p, name)

    def test_unknown_version_cannot_silently_use_newer_sources(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            build.source('9.99.99')

    def test_pinned_patch_only_changes_gstplay(self):
        patch = (ROOT / 'build-aux/gstplay/gstplay-dispose.patch').read_text()
        self.assertEqual(patch.count('diff --git'), 1)
        self.assertIn('a/gst-libs/gst/play/gstplay.c', patch)
        self.assertIn('dispose_deferred', patch)


class GstPlayWorkflowTest(unittest.TestCase):
    def test_both_windows_jobs_build_the_exact_library_before_gtk(self):
        text = (ROOT / '.github/workflows/windows.yml').read_text()
        self.assertEqual(text.count('python tools/build_gstplay.py --output build/gstplay'), 2)
        self.assertEqual(text.count('gstplay-v1-${{ runner.os }}-'), 2)
        self.assertIn('mingw-w64-ucrt-x86_64-meson', text)

    def test_ubuntu_tests_load_the_custom_library(self):
        text = (ROOT / '.github/workflows/test.yml').read_text()
        self.assertIn('python3 tools/build_gstplay.py --output build/gstplay', text)
        self.assertIn('LD_LIBRARY_PATH=$PWD/build/gstplay/lib', text)
        self.assertIn("ctypes.CDLL('libgstplay-1.0.so.0')", text)
        self.assertIn('assert loaded == {expected}', text)
        self.assertLess(text.index('python3 tools/build_gstplay.py'),
                        text.index('The full suite with a display'))


class GstPlayBootstrapTest(unittest.TestCase):
    def test_loaded_module_must_be_the_verified_absolute_dll(self):
        sys.path.insert(0, str(ROOT / 'tools'))
        import gstplay_bootstrap as bootstrap
        original_library, original_directory = bootstrap._LIBRARY, bootstrap._DIRECTORY
        self.addCleanup(setattr, bootstrap, '_LIBRARY', original_library)
        self.addCleanup(setattr, bootstrap, '_DIRECTORY', original_directory)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dll = root / 'bin/libgstplay-1.0-0.dll'
            for matching in (False, True):
                def filename(_handle, buffer, _length, matching=matching):
                    buffer.value = str(dll if matching else root / 'wrong.dll')
                    return len(buffer.value)
                kernel = mock.Mock(GetModuleFileNameW=filename)
                bootstrap._LIBRARY = None
                with mock.patch.dict('os.environ', {'SCRIBA_GSTPLAY_BUILD': str(root)}), \
                        mock.patch.object(sys, 'platform', 'win32'), \
                        mock.patch.object(bootstrap, 'read_receipt', return_value={
                            'outputs': [{'file': 'bin/libgstplay-1.0-0.dll'}]}), \
                        mock.patch.object(bootstrap.os, 'add_dll_directory', create=True), \
                        mock.patch.object(bootstrap.ctypes, 'WinDLL', create=True) as load, \
                        mock.patch.object(bootstrap.ctypes, 'windll', mock.Mock(kernel32=kernel), create=True):
                    if matching:
                        bootstrap.preload()
                        load.assert_called_once_with(str(dll))
                        self.assertIsNotNone(bootstrap._LIBRARY)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'not the verified'):
                            bootstrap.preload()
                        self.assertIsNone(bootstrap._LIBRARY)
            bootstrap._LIBRARY = None
        sys.path.remove(str(ROOT / 'tools'))
