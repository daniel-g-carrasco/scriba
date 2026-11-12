#!/usr/bin/env python3
"""What a release needs to be right, checked before the tag: the version
in the code, the changelog and the metainfo agree; the texts shown to the
user and the documents have no em dash; the identity constants agree with
the files named after them."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _setup

import scriba

ROOT = _setup.ROOT
EM_DASH = chr(0x2014)       # (never written here as itself: the rule applies to this file too)


class VersionTest(unittest.TestCase):
    def test_the_changelog_and_the_metainfo_know_this_version(self):
        version = scriba.__version__
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{version}] - ", changelog, "CHANGELOG.md has no entry for this version")
        metainfo = (ROOT / "data" / f"{scriba.APP_ID}.metainfo.xml").read_text(encoding="utf-8")
        self.assertIn(f'<release version="{version}" date="', metainfo, "the metainfo has no release for this version")
        # the newest release first, in both
        self.assertEqual(re.search(r"^## \[(\d[^\]]*)\]", changelog, re.M).group(1), version)
        self.assertEqual(re.search(r'<release version="([^"]+)"', metainfo).group(1), version)


class IdentityTest(unittest.TestCase):
    def test_the_files_are_named_after_the_app_id(self):
        for name in (f"{scriba.APP_ID}.desktop", f"{scriba.APP_ID}.metainfo.xml"):
            self.assertTrue((ROOT / "data" / name).is_file(), name)
        self.assertTrue((ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps" / f"{scriba.APP_ID}.svg").is_file())
        self.assertTrue((ROOT / "build-aux" / "flatpak" / f"{scriba.APP_ID}.yaml").is_file())
        self.assertTrue((ROOT / "build-aux" / "windows" / f"{scriba.SLUG}.spec").is_file())
        self.assertTrue((ROOT / "build-aux" / "windows" / f"{scriba.SLUG}.iss").is_file())
        self.assertTrue((ROOT / "bin" / scriba.SLUG).is_file())
        desktop = (ROOT / "data" / f"{scriba.APP_ID}.desktop").read_text(encoding="utf-8")
        self.assertIn(f"Exec={scriba.SLUG}", desktop)
        self.assertIn(f"Icon={scriba.APP_ID}\n", desktop)
        iss = (ROOT / "build-aux" / "windows" / f"{scriba.SLUG}.iss").read_text(encoding="utf-8")
        self.assertIn(f"{scriba.EXE_NAME}.exe", iss)
        self.assertIn(f"OutputBaseFilename={scriba.SLUG}-setup-v", iss)

    def test_the_identity_is_derived_not_repeated(self):
        self.assertEqual(scriba.TEXTDOMAIN, scriba.SLUG)
        self.assertEqual(scriba.ENV_PREFIX, scriba.SLUG.upper().replace("-", "_"))
        self.assertTrue(scriba.APP_ID.endswith("." + scriba.SLUG))
        self.assertNotIn(" ", scriba.EXE_NAME)

    def test_a_silent_install_can_skip_start_menu_shortcuts(self):
        iss = (ROOT / "build-aux" / "windows" / f"{scriba.SLUG}.iss").read_text(encoding="utf-8")
        self.assertIn("AllowNoIcons=yes", iss, "/NOICONS must work even when the group page is hidden")


class CompleteReleaseTest(unittest.TestCase):
    """A release is published only when every file is there: the installed
    app asks GitHub for the latest release, and one with the Flatpak alone
    (the Windows build failed) offered a version nobody on Windows could
    install. The step is the same in both workflows and is run here against
    a stand-in for gh."""

    FILES = ("{slug}.flatpak", "{slug}.flatpak.minisig", "{slug}-setup-v1.2.3-windows-x64.exe",
             "{slug}-setup-v1.2.3-windows-x64.exe.minisig", "{slug}-v1.2.3-windows-x64-portable.zip",
             "{slug}-v1.2.3-windows-x64-portable.zip.minisig",
             "{slug}-v1.2.3-windows-x64-sources.tar.xz", "{slug}-v1.2.3-windows-x64-sources.tar.xz.minisig",
             "{slug}-v1.2.3-linux-x86_64-sources.tar.xz", "{slug}-v1.2.3-linux-x86_64-sources.tar.xz.minisig")

    def step(self, workflow: str) -> str:
        text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        found = re.search(r"- name: Published when every file is there\n(.*?)(?=\n      - name: |\n  \w|\Z)", text, re.S)
        self.assertIsNotNone(found, f"{workflow} has no step that publishes the release when it is complete")
        return found.group(1)

    def script(self) -> str:
        step = self.step("build.yml")
        lines = step.split("        run: |\n", 1)[1].splitlines()
        return "\n".join(line[10:] for line in lines) + "\n"

    def test_the_step_is_the_same_in_both_workflows(self):
        self.assertEqual(self.step("build.yml").rstrip(), self.step("windows.yml").rstrip())

    def test_the_release_is_made_as_a_draft(self):
        build = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        create = re.search(r"gh release create .*?(?=\n          gh release upload)", build, re.S).group()
        self.assertIn("--draft", create)
        self.assertNotIn("--draft=false", create)

    def run_with(self, names) -> tuple[str, str]:
        """(what the step printed, what it asked gh to do) with those files in the release."""
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("no bash to run the workflow's step with")
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / "names.txt").write_text("".join(name + "\n" for name in names), encoding="utf-8", newline="\n")
        # the stand-in: "release view" gives the names, anything else is written down
        (tmp / "gh").write_text('#!/bin/sh\n'
                                'if [ "$2" = view ]; then cat "$GH_STAND_IN/names.txt"; '
                                'else echo "$@" >> "$GH_STAND_IN/asked.txt"; fi\n',
                                encoding="utf-8", newline="\n")
        # relative names, and the folder as the working one: the bash found
        # may be Git's, MSYS2's or WSL's, and each spells a Windows path its
        # own way. The tag and the repository are set in the script, not in
        # the environment: WSL's bash does not take Windows' variables
        (tmp / "step.sh").write_text('export GH_STAND_IN="$PWD" GITHUB_REF_NAME=v1.2.3 GITHUB_REPOSITORY=me/app\n'
                                     'PATH="$PWD:$PATH"\nchmod +x gh\n' + self.script(),
                                     encoding="utf-8", newline="\n")
        done = subprocess.run([bash, "step.sh"], capture_output=True, text=True, cwd=tmp)
        self.assertEqual(done.returncode, 0, done.stderr)
        asked = (tmp / "asked.txt").read_text(encoding="utf-8") if (tmp / "asked.txt").exists() else ""
        return done.stdout, asked

    def test_every_file_there_and_it_is_published(self):
        out, asked = self.run_with([name.format(slug=scriba.SLUG) for name in self.FILES])
        self.assertIn("published", out)
        self.assertEqual(asked.split(), ["release", "edit", "v1.2.3", "--repo", "me/app", "--draft=false", "--latest"])

    def test_one_file_missing_and_it_stays_a_draft(self):
        every = [name.format(slug=scriba.SLUG) for name in self.FILES]
        for missing in every:
            with self.subTest(missing):
                out, asked = self.run_with([name for name in every if name != missing])
                self.assertIn("stays a draft", out)
                self.assertEqual(asked, "")

    def test_another_versions_sources_do_not_count(self):
        every = [name.format(slug=scriba.SLUG).replace("v1.2.3-linux", "v1.2.2-linux")
                 for name in self.FILES]
        out, asked = self.run_with(every)
        self.assertIn("stays a draft", out)
        self.assertEqual(asked, "")

    def test_another_versions_installer_does_not_count(self):
        every = [name.format(slug=scriba.SLUG).replace("v1.2.3-windows-x64.exe", "v1.2.2-windows-x64.exe")
                 for name in self.FILES]
        out, asked = self.run_with(every)
        self.assertIn("stays a draft", out)
        self.assertEqual(asked, "")


class IconsTest(unittest.TestCase):
    def test_the_svg_files_are_recognised_on_linux_too(self):
        # GdkPixbuf on Linux tells an SVG by "<svg" within its first 256 bytes,
        # not by the name; on Windows the name is enough, so a comment before
        # the root breaks make_icons.py and make_ico.py on Linux only
        svgs = [path for folder in ("data", "build-aux", "site") for path in (ROOT / folder).rglob("*.svg")]
        self.assertTrue(svgs)
        for path in svgs:
            self.assertIn(b"<svg", path.read_bytes()[:256], f"{path.name}: the comments go inside <svg>")


class TextsTest(unittest.TestCase):
    def test_no_em_dash_anywhere_the_user_reads(self):
        # a rule of the house: a colon, a comma or a full stop instead
        offenders = []
        for folder, pattern in (("src", "*.py"), ("po", "*.po"), ("data", "*.xml"), ("data", "*.desktop"),
                                ("docs", "*.md"), (".", "*.md")):
            for path in (ROOT / folder).glob(pattern):
                if EM_DASH in path.read_text(encoding="utf-8"):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
