#!/usr/bin/env python3
"""Where the app keeps its files: the XDG folders, Windows' profile
folders, the nearest folder of a path that is gone."""

import os
import tempfile
import unittest
from pathlib import Path

import _setup

from scriba import EXE_NAME, SLUG
from scriba.app.paths import Paths, nearest_folder


def absolute(name: str) -> str:
    """An absolute folder for the tests: since Python 3.13 "/c" is not
    absolute on Windows (no drive), and the XDG variables are taken only
    when absolute."""
    return rf"C:\{name}" if _setup.WINDOWS else f"/{name}"


class PathsTest(unittest.TestCase):
    def test_xdg_variables_win(self):
        paths = Paths.from_env(SLUG, EXE_NAME, {"XDG_CONFIG_HOME": absolute("c"), "XDG_STATE_HOME": absolute("s"),
                                "XDG_DATA_HOME": absolute("d"), "HOME": absolute("h")})
        self.assertEqual((paths.config, paths.state, paths.data),
                         (Path(absolute("c")) / SLUG, Path(absolute("s")) / SLUG, Path(absolute("d")) / SLUG))
        self.assertEqual(paths.config_file, Path(absolute("c")) / SLUG / "config.json")
        self.assertEqual(paths.window_state_file, Path(absolute("s")) / SLUG / "window.json")
        self.assertEqual(paths.cache, Path(absolute("h")) / ".cache" / SLUG)     # no XDG_CACHE_HOME: under HOME
        cached = Paths.from_env(SLUG, EXE_NAME, {"XDG_CONFIG_HOME": absolute("c"), "XDG_CACHE_HOME": absolute("k")})
        self.assertEqual(cached.cache_folder, Path(absolute("k")) / SLUG)
        self.assertEqual(Paths(Path("c"), Path("s"), Path("d")).cache_folder, Path("s") / "cache")

    @unittest.skipIf(_setup.WINDOWS, "Linux folders (Windows: the profile folders, below)")
    def test_the_home_folder_without_them(self):
        paths = Paths.from_env(SLUG, EXE_NAME, {"HOME": "/h", "XDG_STATE_HOME": "relative/is/ignored"})
        self.assertEqual(paths.config, Path("/h") / ".config" / SLUG)
        self.assertEqual(paths.state, Path("/h") / ".local" / "state" / SLUG)

    @unittest.skipUnless(_setup.WINDOWS, "Windows only")
    def test_windows_profile_folders(self):
        paths = Paths.from_env(SLUG, EXE_NAME, {"APPDATA": r"C:\U\me\AppData\Roaming", "LOCALAPPDATA": r"C:\U\me\AppData\Local"})
        self.assertEqual(paths.config, Path(r"C:\U\me\AppData\Roaming") / EXE_NAME)
        self.assertEqual(paths.state, Path(r"C:\U\me\AppData\Local") / EXE_NAME / "state")
        self.assertEqual(paths.data, Path(r"C:\U\me\AppData\Local") / EXE_NAME / "data")
        self.assertEqual(paths.cache, Path(r"C:\U\me\AppData\Local") / EXE_NAME / "cache")

    def test_nearest_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(nearest_folder(os.path.join(tmp, "a", "b", "c.txt")), tmp)
            os.makedirs(os.path.join(tmp, "a"))
            self.assertEqual(nearest_folder(os.path.join(tmp, "a", "b", "c.txt")), os.path.join(tmp, "a"))


if __name__ == "__main__":
    unittest.main()
