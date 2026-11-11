"""The Linux package includes the engines and the runtime's own dependencies."""

import json
import re
import unittest

import _setup

FOLDER = _setup.ROOT / "build-aux" / "flatpak"


class FlatpakPackageTest(unittest.TestCase):
    def test_linux_launcher_desktop_entry_and_shell_scripts_have_lf_line_endings(self):
        paths = [_setup.ROOT / "bin" / "scriba",
                 _setup.ROOT / "data" / "io.github.daniel_g_carrasco.scriba.desktop",
                 *(_setup.ROOT / "tools").glob("*.sh")]
        for path in paths:
            with self.subTest(file=path.name):
                self.assertNotIn(b"\r", path.read_bytes())
        attributes = (_setup.ROOT / ".gitattributes").read_text(encoding="utf-8")
        for rule in ("*.sh text eol=lf", "bin/* text eol=lf", "*.desktop text eol=lf"):
            self.assertIn(rule, attributes)

    def test_pinned_engines_and_their_runtime_check_are_part_of_the_build(self):
        text = (FOLDER / "io.github.daniel_g_carrasco.scriba.yaml").read_text(encoding="utf-8")
        for setting in ("separate-locales: false", "-DGGML_VULKAN=ON", "-DGGML_NATIVE=OFF", "-DGGML_CPU=ON",
                        "onnxruntime-linux-x64-1.29.0.tgz", "python-numerical.json",
                        "python3 tools/check_flatpak.py"):
            self.assertIn(setting, text)
        self.assertGreaterEqual(len(re.findall(r"sha256: [0-9a-f]{64}", text)), 2)
        for permission in ("--socket=pulseaudio", "--share=network", "--filesystem=host"):
            self.assertIn(permission, text)
        self.assertNotIn("--filesystem=home:ro", text)

    def test_numerical_wheels_are_fixed_and_installed_even_when_present_in_sdk(self):
        module = json.loads((FOLDER / "python-numerical.json").read_text(encoding="utf-8"))
        command = " ".join(module["build-commands"])
        for flag in ("--no-index", "--no-deps", "--ignore-installed", "--prefix=/app"):
            self.assertIn(flag, command)
        for source in module["sources"]:
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(source["url"].startswith("https://files.pythonhosted.org/"))
            self.assertTrue(source["url"].endswith(".whl"))
        for package in ("numpy-", "scipy-", "scikit_learn-"):
            self.assertTrue(any(package in source["url"] and "cp313" in source["url"]
                                for source in module["sources"]))
