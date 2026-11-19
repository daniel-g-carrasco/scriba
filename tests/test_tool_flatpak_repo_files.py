#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/flatpak_repo_files.py: the files next to the published Flatpak
repository, with the app's names read from its package (without importing
it) and its texts from the metainfo, in an app made for the purpose."""

import configparser
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _setup

TOOL = _setup.ROOT / "tools" / "flatpak_repo_files.py"
INIT = '''"""My App: an app made for the test."""
import sys
from pathlib import Path

__version__ = "1.2.3"
WINDOWS = sys.platform == "win32"
APP_ID = "org.example.my-app"
APP_NAME = "My App"
SLUG = "my-app"
TEXTDOMAIN = SLUG
GITHUB_REPO = "someone/my-app"
WEBSITE = f"https://github.com/{GITHUB_REPO}"
PACKAGE_DIR = Path(__file__).resolve().parent
'''
METAINFO = """<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>org.example.my-app</id>
  <name>My App</name>
  <summary>Does one thing</summary>
  <summary xml:lang="it">Fa una cosa</summary>
  <description>
    <p xml:lang="it">My App fa una cosa. Bene.</p>
    <p>
      My App does one thing, and does it well. It does nothing else.
    </p>
  </description>
</component>
"""


class Files(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "src" / "my_app").mkdir(parents=True)
        (self.root / "src" / "my_app" / "__init__.py").write_text(INIT, encoding="utf-8")
        (self.root / "data").mkdir()
        (self.root / "data" / "org.example.my-app.metainfo.xml").write_text(METAINFO, encoding="utf-8")
        (self.root / "tools").mkdir()
        shutil.copy(TOOL, self.root / "tools" / TOOL.name)
        self.pages = self.root / "pages"
        self.pages.mkdir()

    def run_tool(self, *more):
        run = subprocess.run([sys.executable, str(self.root / "tools" / TOOL.name), str(self.pages),
                              "https://someone.github.io/my-app/", "S0VZ", "1.2.3", *more],
                             capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def read(self, name: str) -> str:
        return (self.pages / name).read_text(encoding="utf-8")

    def test_the_four_files_from_the_apps_names_and_its_metainfo(self):
        out = self.run_tool()
        self.assertIn("my-app.flatpakref, my-app.flatpakrepo, index.html, .nojekyll", out)
        ref = configparser.ConfigParser(interpolation=None)
        ref.optionxform = str
        ref.read_string(self.read("my-app.flatpakref"))
        self.assertEqual(dict(ref["Flatpak Ref"]), {
            "Name": "org.example.my-app", "Branch": "stable", "Title": "My App", "Comment": "Does one thing",
            "Description": "My App does one thing, and does it well.", "Homepage": "https://github.com/someone/my-app",
            "Icon": "https://someone.github.io/my-app/icon.png", "Url": "https://someone.github.io/my-app/repo",
            "SuggestRemoteName": "my-app", "IsRuntime": "false",
            "RuntimeRepo": "https://dl.flathub.org/repo/flathub.flatpakrepo", "GPGKey": "S0VZ"})
        repo = configparser.ConfigParser(interpolation=None)
        repo.optionxform = str
        repo.read_string(self.read("my-app.flatpakrepo"))
        self.assertEqual(repo["Flatpak Repo"]["Url"], "https://someone.github.io/my-app/repo")
        self.assertEqual(self.read(".nojekyll"), "")
        page = self.read("index.html")
        self.assertIn("<title>My App, Flatpak repository</title>", page)
        self.assertIn("Current version: <strong>1.2.3</strong>", page)
        self.assertIn('<a class="btn" href="https://someone.github.io/my-app/my-app.flatpakref">Install My App</a>', page)
        self.assertIn('<a href="https://github.com/someone/my-app#flatpak">README</a>', page)
        self.assertNotIn("corresponding sources", page)

    def test_the_texts_and_the_keys_page_given_and_the_sources(self):
        self.run_tool("--comment", "Another line", "--description", "Another sentence.",
                      "--key-page", "blob/main/docs/linux.md#key", "Linux documentation", "--sources")
        self.assertIn("Comment=Another line\nDescription=Another sentence.\n", self.read("my-app.flatpakrepo"))
        page = self.read("index.html")
        self.assertIn('<a href="https://github.com/someone/my-app/blob/main/docs/linux.md#key">Linux documentation</a>',
                      page)
        self.assertIn("https://github.com/someone/my-app/releases/download/v1.2.3/"
                      "my-app-v1.2.3-linux-x86_64-sources.tar.xz", page)
        self.assertIn("My App is free software under GPL-3.0-or-later.", page)

    def test_an_app_without_one_package_is_refused(self):
        (self.root / "src" / "other").mkdir()
        (self.root / "src" / "other" / "__init__.py").write_text("", encoding="utf-8")
        run = subprocess.run([sys.executable, str(self.root / "tools" / TOOL.name), str(self.pages), "b", "k", "1"],
                             capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("one package expected, 2 found", run.stderr)


if __name__ == "__main__":
    unittest.main()
