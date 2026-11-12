"""The inventory follows build provenance, including Python's archive and GTK assets."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import _setup

spec = importlib.util.spec_from_file_location("finish_bundle", _setup.ROOT / "tools" / "finish_bundle.py")
finish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finish)


class InventoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / "private-project"
        self.prefix = self.home / "msys64" / "ucrt64"
        self.internal = self.root / "dist" / "Scriba" / "_internal"
        self.tocs = self.root / "build" / "scriba"
        self.tocs.mkdir(parents=True)
        self.internal.mkdir(parents=True)
        self.analysis = []
        self.pyz = []
        self.collect = []
        self.exe = []

    def write(self, path, content="bytes"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def package(self, short, files, version="1.0-2"):
        name = "mingw-w64-ucrt-x86_64-" + short
        folder = self.prefix.parent / "var" / "lib" / "pacman" / "local" / (name + "-" + version)
        self.write(folder / "desc", f"%NAME%\n{name}\n\n%VERSION%\n{version}\n\n%BASE%\nmingw-w64-{short}\n\n"
                   "%LICENSE%\nspdx:MIT\n\n%URL%\nhttps://example.org/\n")
        members = []
        for filename in files:
            self.write(self.prefix / filename)
            members.append("ucrt64/" + filename)
        self.write(folder / "files", "%FILES%\n" + "\n".join(members) + "\n")
        return name

    def build_inventory(self):
        for name, data in (("Analysis-00.toc", ([], self.analysis)), ("PYZ-00.toc", ("archive", self.pyz)),
                           ("COLLECT-00.toc", (self.collect,)),
                           ("EXE-00.toc", (str(self.exe_output), self.exe) if hasattr(self, "exe_output")
                            else (self.exe,))):
            self.write(self.tocs / name, repr(data))
        return finish.inventory(self.internal, prefix=self.prefix, root=self.root, toc_dir=self.tocs)

    def test_python_archive_assets_and_bootloader_are_owned_without_guessing_names(self):
        hidden = "lib/python3.14/site-packages/hidden_dependency/__init__.py"
        pure = self.package("python-hidden-dependency", [hidden], version="4.2-3")
        icons = self.package("adwaita-icon-theme", ["share/icons/Adwaita/scalable/a.svg"])
        bootstrap = "lib/python3.14/site-packages/PyInstaller/loader/pyiboot01_bootstrap.py"
        freezer = self.package("pyinstaller", [bootstrap])
        self.pyz.append(("hidden_dependency", str(self.prefix / hidden), "PYMODULE"))
        self.pyz.append(("namespace", "-", "PYMODULE"))
        self.exe.append(("pyiboot01_bootstrap", str(self.prefix / bootstrap), "PYSOURCE"))
        self.write(self.internal / "share/icons/Adwaita/scalable/a.svg")
        self.write(self.root / "src/scriba/private.py", "private text")
        self.pyz.append(("scriba.private", str(self.root / "src/scriba/private.py"), "PYMODULE"))
        data = self.build_inventory()
        packages = {p["package"]: p for p in data["packages"]}
        self.assertEqual(set(packages), {pure, icons, freezer})
        self.assertEqual(packages[pure]["version"], "4.2-3")
        self.assertEqual(packages[pure]["base"], "mingw-w64-python-hidden-dependency")
        self.assertEqual(packages[pure]["files"], [])
        self.assertEqual(packages[pure]["modules"], ["hidden_dependency"])
        self.assertEqual(packages[pure]["module_origins"]["hidden_dependency"]["package_file"], "ucrt64/" + hidden)
        asset = "_internal/share/icons/Adwaita/scalable/a.svg"
        self.assertEqual(packages[icons]["origins"][asset], {
            "package_file": "ucrt64/share/icons/Adwaita/scalable/a.svg",
            "sha256": "277089d91c0bdf4f2e6862ba7e4a07605119431f5d13f726dd352b06f1b206a9"})
        self.assertIn("pyiboot01_bootstrap", packages[freezer]["modules"])
        self.assertNotIn(str(self.root), json.dumps(data))
        self.assertNotIn("scriba.private", json.dumps(data))

    def test_removed_loader_and_own_ffmpeg_do_not_claim_a_pacman_package(self):
        self.package("ffmpeg", ["bin/ffmpeg.exe"])
        self.package("libheif", ["lib/gdk-pixbuf/loaders/libpixbufloader-heif.dll"])
        own = self.write(self.root / "build/ffmpeg-lgpl/bin/ffmpeg.exe", "own ffmpeg")
        self.write(self.internal / "ffmpeg.exe", "own ffmpeg")
        self.collect += [("ffmpeg.exe", str(own), "BINARY"),
                         ("lib/gdk-pixbuf/loaders/libpixbufloader-heif.dll",
                          str(self.prefix / "lib/gdk-pixbuf/loaders/libpixbufloader-heif.dll"), "BINARY")]
        self.assertEqual(self.build_inventory()["packages"], [])

    def test_a_package_file_changed_after_copy_is_rejected(self):
        self.package("library", ["bin/a.dll"])
        self.write(self.internal / "a.dll", "different bytes")
        self.collect.append(("a.dll", str(self.prefix / "bin/a.dll"), "BINARY"))
        with self.assertRaisesRegex(ValueError, "hash mismatch.*a.dll"):
            self.build_inventory()

    def test_case_insensitive_lookup_exports_the_canonical_archive_member(self):
        self.package("glib2", ["bin/gdbus.exe"])
        source = self.write(self.prefix / "bin/gdbus.EXE")
        self.write(self.internal / "gdbus.EXE")
        self.collect.append(("gdbus.EXE", str(source), "BINARY"))
        data = self.build_inventory()
        self.assertEqual(data["packages"][0]["origins"]["_internal/gdbus.EXE"]["package_file"],
                         "ucrt64/bin/gdbus.exe")

    def test_certificate_bundle_records_post_install_generation_inputs(self):
        self.package("ca-certificates", ["bin/update-ca-trust", "share/pki/ca-trust-source/ca-bundle.trust.crt"])
        self.package("p11-kit", ["bin/p11-kit.exe"])
        source = self.write(self.prefix / "etc/ssl/certs/ca-bundle.crt", "generated certificates")
        self.write(self.internal / "certs/ca-bundle.crt", "generated certificates")
        self.collect.append(("certs/ca-bundle.crt", str(source), "DATA"))
        data = self.build_inventory()
        generated = data["generated_files"][0]
        self.assertEqual(generated["file"], "_internal/certs/ca-bundle.crt")
        self.assertEqual({p["package_file"] for p in generated["inputs"]},
                         {"ucrt64/bin/update-ca-trust", "ucrt64/bin/p11-kit.exe",
                          "ucrt64/share/pki/ca-trust-source/ca-bundle.trust.crt"})
        self.assertFalse(any(p["origins"] for p in data["packages"]))

    def test_an_unowned_python_dependency_is_a_build_error(self):
        self.package("unrelated", ["bin/unrelated.dll"])
        unknown = self.write(self.prefix / "lib/python3.14/site-packages/missing.py")
        self.pyz.append(("missing", str(unknown), "PYMODULE"))
        with self.assertRaisesRegex(ValueError, "no pacman owner.*missing"):
            self.build_inventory()

    def test_an_untraced_binary_is_a_build_error(self):
        self.package("unrelated", ["bin/unrelated.dll"])
        self.write(self.internal / "mystery.dll")
        with self.assertRaisesRegex(ValueError, "no build origin.*mystery.dll"):
            self.build_inventory()

    def test_an_untraced_executable_at_the_bundle_root_is_a_build_error(self):
        self.package("unrelated", ["bin/unrelated.dll"])
        self.write(self.internal.parent / "mystery.exe")
        with self.assertRaisesRegex(ValueError, "no build origin.*mystery.exe"):
            self.build_inventory()

    def test_external_build_outputs_are_identified_by_toc_and_verified_byte_for_byte(self):
        self.package("unrelated", ["bin/unrelated.dll"])
        self.tocs = self.home / "external-build/scriba"
        self.exe_output = self.write(self.tocs / "Scriba.exe", "generated app")
        archive = self.write(self.tocs / "base_library.zip", "generated Python archive")
        self.write(self.internal.parent / "Scriba.exe", "generated app")
        self.write(self.internal / "base_library.zip", "generated Python archive")
        self.collect += [("Scriba.exe", str(self.exe_output), "EXECUTABLE"),
                         ("base_library.zip", str(archive), "DATA")]
        self.assertEqual(self.build_inventory()["packages"], [])
        self.write(self.internal.parent / "Scriba.exe", "different app")
        with self.assertRaisesRegex(ValueError, "hash mismatch.*Scriba.exe"):
            self.build_inventory()
        self.write(self.internal.parent / "Scriba.exe", "generated app")
        self.write(self.internal / "base_library.zip", "different archive")
        with self.assertRaisesRegex(ValueError, "hash mismatch.*base_library.zip"):
            self.build_inventory()

    def test_an_executable_not_declared_by_exe_toc_is_still_unresolved(self):
        self.package("unrelated", ["bin/unrelated.dll"])
        self.tocs = self.home / "external-build/scriba"
        arbitrary = self.write(self.tocs / "Scriba.exe")
        self.write(self.internal.parent / "Scriba.exe")
        self.collect.append(("Scriba.exe", str(arbitrary), "EXECUTABLE"))
        with self.assertRaisesRegex(ValueError, "unresolved build origin.*Scriba.exe"):
            self.build_inventory()

    def test_an_empty_optional_url_in_an_installed_package_is_supported(self):
        package = self.package("without-url", ["bin/a.dll"])
        desc = next((self.prefix.parent / "var/lib/pacman/local").glob("*/desc"))
        desc.write_text(desc.read_text().replace("https://example.org/", ""), encoding="utf-8")
        self.write(self.internal / "a.dll")
        self.collect.append(("a.dll", str(self.prefix / "bin/a.dll"), "BINARY"))
        record = self.build_inventory()["packages"][0]
        self.assertEqual(record["package"], package)
        self.assertEqual(record["url"], "")

    def test_msys_assets_outside_ucrt64_keep_their_real_package_owner(self):
        old = self.package("mintty", [])
        folder = next((self.prefix.parent / "var/lib/pacman/local").iterdir())
        desc = folder / "desc"
        desc.write_text(desc.read_text().replace(old, "mintty"), encoding="utf-8")
        source = self.write(self.prefix.parent / "usr/share/mintty/icon.png")
        self.write(folder / "files", "%FILES%\nusr/share/mintty/icon.png\n")
        self.write(self.internal / "share/icons/hicolor/16x16/apps/mintty.png")
        self.collect.append(("share/icons/hicolor/16x16/apps/mintty.png", str(source), "DATA"))
        package = self.build_inventory()["packages"][0]
        self.assertEqual(package["package"], "mintty")
        self.assertEqual(package["origins"]["_internal/share/icons/hicolor/16x16/apps/mintty.png"]["package_file"],
                         "usr/share/mintty/icon.png")

    def test_hook_directories_discover_the_actual_package_name(self):
        hook = "lib/python3.14/site-packages/_pyinstaller_hooks_contrib/stdhooks/hook-example.py"
        name = self.package("pyinstaller-hooks-contrib", [hook])
        self.analysis.append((str((self.prefix / hook).parent), -1000))
        self.assertEqual([p["package"] for p in self.build_inventory()["packages"]], [name])

    def test_compiled_bootstrap_modules_resolve_to_their_pyinstaller_source(self):
        loader = "lib/python3.14/site-packages/PyInstaller/loader/"
        name = self.package("pyinstaller", [loader + "pyiboot01_bootstrap.py", loader + "pyimod01_archive.py"])
        self.exe += [("pyiboot01_bootstrap", str(self.prefix / loader / "pyiboot01_bootstrap.py"), "PYSOURCE"),
                     ("pyimod01_archive", str(self.tocs / "localpycs/pyimod01_archive.pyc"), "PYMODULE")]
        self.write(self.tocs / "localpycs/pyimod01_archive.pyc", "compiled bootstrap")
        package = self.build_inventory()["packages"][0]
        self.assertEqual(package["package"], name)
        self.assertIn("pyimod01_archive", package["modules"])
        self.assertEqual(package["module_origins"]["pyimod01_archive"]["package_file"],
                         "ucrt64/" + loader + "pyimod01_archive.py")

    def test_compiled_stdlib_bootstrap_uses_the_analysis_origin_outside_the_project(self):
        source = "lib/python3.14/struct.py"
        name = self.package("python", [source], version="3.14-1")
        self.tocs = self.home / "external-build/scriba"
        compiled = self.write(self.tocs / "localpycs/struct.pyc", "compiled struct input")
        self.analysis.append(("struct", str(self.prefix / source), "PYMODULE"))
        self.exe.append(("struct", str(compiled), "PYMODULE"))
        package = self.build_inventory()["packages"][0]
        self.assertEqual(package["package"], name)
        self.assertEqual(package["modules"], ["struct"])
        self.assertEqual(package["module_origins"]["struct"], {
            "package_file": "ucrt64/" + source,
            "sha256": hashlib.sha256(b"bytes").hexdigest(),
            "compiled_sha256": hashlib.sha256(b"compiled struct input").hexdigest()})
        self.assertNotIn(str(self.home), json.dumps(package))

    def test_local_compiled_module_without_an_analysis_source_stays_an_error(self):
        self.package("python", ["lib/python3.14/struct.py"])
        compiled = self.write(self.tocs / "localpycs/struct.pyc")
        self.exe.append(("struct", str(compiled), "PYMODULE"))
        with self.assertRaisesRegex(ValueError, "unresolved compiled Python origin.*struct"):
            self.build_inventory()

    def test_compiled_module_rejects_ambiguous_analysis_origins(self):
        self.package("python", ["lib/python3.14/struct.py", "lib/python3.14/other/struct.py"])
        self.analysis += [("struct", str(self.prefix / path), "PYMODULE") for path in (
            "lib/python3.14/struct.py", "lib/python3.14/other/struct.py")]
        compiled = self.write(self.tocs / "localpycs/struct.pyc")
        self.exe.append(("struct", str(compiled), "PYMODULE"))
        with self.assertRaisesRegex(ValueError, "ambiguous compiled Python origin.*struct"):
            self.build_inventory()

    def test_a_missing_compiled_bootstrap_input_is_rejected(self):
        self.package("python", ["lib/python3.14/struct.py"])
        self.analysis.append(("struct", str(self.prefix / "lib/python3.14/struct.py"), "PYMODULE"))
        self.exe.append(("struct", str(self.tocs / "localpycs/struct.pyc"), "PYMODULE"))
        with self.assertRaisesRegex(ValueError, "missing compiled Python input.*struct"):
            self.build_inventory()

    def test_two_executables_cannot_silently_disagree_about_a_compiled_module(self):
        self.package("python", ["lib/python3.14/struct.py"])
        self.analysis.append(("struct", str(self.prefix / "lib/python3.14/struct.py"), "PYMODULE"))
        first = self.write(self.tocs / "localpycs/struct.pyc", "first compiled bytes")
        second = self.write(self.tocs / "localpycs/other/struct.pyc", "different compiled bytes")
        self.exe += [("struct", str(path), "PYMODULE") for path in (first, second)]
        with self.assertRaisesRegex(ValueError, "inconsistent compiled Python input.*struct"):
            self.build_inventory()

    def test_rebuilt_icon_cache_lists_assets_as_inputs_without_claiming_the_cache_bytes(self):
        self.package("icons", ["share/icons/Adwaita/index.theme", "share/icons/Adwaita/scalable/a.svg"])
        self.write(self.internal / "share/icons/Adwaita/index.theme")
        self.write(self.internal / "share/icons/Adwaita/scalable/a.svg")
        self.write(self.internal / "share/icons/Adwaita/icon-theme.cache", "rebuilt cache")
        cache = self.write(self.prefix / "share/icons/Adwaita/icon-theme.cache", "original installed cache")
        self.collect.append(("share/icons/Adwaita/icon-theme.cache", str(cache), "DATA"))
        data = self.build_inventory()
        cache = data["generated_files"][0]
        self.assertEqual(cache["file"], "_internal/share/icons/Adwaita/icon-theme.cache")
        self.assertEqual(len(cache["inputs"]), 2)
        self.assertFalse(any(name.endswith("icon-theme.cache") for name in data["packages"][0]["origins"]))

    def test_generated_gio_cache_uses_the_query_tool_and_retained_module_inputs(self):
        name = self.package("glib2", ["bin/gio-querymodules.exe", "lib/gio/modules/libgioexample.dll"])
        self.write(self.internal / "gio_modules/libgioexample.dll")
        cache = self.write(self.prefix / "lib/gio/modules/giomodule.cache", "libgioexample.dll: extension\n")
        self.write(self.internal / "gio_modules/giomodule.cache", "libgioexample.dll: extension\n")
        self.collect += [("gio_modules/libgioexample.dll", str(self.prefix / "lib/gio/modules/libgioexample.dll"),
                          "BINARY"), ("gio_modules/giomodule.cache", str(cache), "DATA")]
        data = self.build_inventory()
        self.assertEqual(data["packages"][0]["package"], name)
        cache = data["generated_files"][0]
        self.assertEqual(cache["file"], "_internal/gio_modules/giomodule.cache")
        self.assertEqual({p["package_file"] for p in cache["inputs"]},
                         {"ucrt64/bin/gio-querymodules.exe", "ucrt64/lib/gio/modules/libgioexample.dll"})

    def test_licences_copy_the_owned_directory_and_report_missing_texts_honestly(self):
        source = "lib/python3.14/site-packages/hidden.py"
        package = self.package("python-hidden", [source, "share/licenses/unexpected-name/COPYING"])
        missing = "lib/python3.14/site-packages/missing_licence.py"
        missing_package = self.package("python-missing-licence", [missing])
        self.pyz += [("hidden", str(self.prefix / source), "PYMODULE"),
                     ("missing_licence", str(self.prefix / missing), "PYMODULE")]
        self.build_inventory()
        self.write(self.root / "LICENSE", "GNU GPL version 3")
        self.write(self.root / "COPYRIGHT", "Scriba attribution")
        self.write(self.root / "LICENSES/third-party.txt", "third party attribution")
        finish.licences(self.internal, prefix=self.prefix, root=self.root, toc_dir=self.tocs)
        out = self.internal / "licenses"
        data = json.loads((out / "PACKAGES.json").read_text())
        self.assertIn(package, {p["package"] for p in data["packages"]})
        self.assertEqual(data["missing_licence_files"], [missing_package])
        self.assertTrue((out / "python-hidden/unexpected-name/COPYING").is_file())
        self.assertEqual((out / "Scriba-GPL-3.0-or-later.txt").read_text(), "GNU GPL version 3")
        self.assertEqual((out / "COPYRIGHT.txt").read_text(), "Scriba attribution")
        text = (out / "THIRD-PARTY.md").read_text()
        self.assertIn("No package licence files installed", text)
        self.assertNotIn("as its authors' licence allows", text)

    def test_verified_upstream_notices_are_copied_and_bound_to_the_package_version(self):
        source = "lib/python3.14/site-packages/example.py"
        package = self.package("python-example", [source])
        self.pyz.append(("example", str(self.prefix / source), "PYMODULE"))
        self.build_inventory()
        self.write(self.root / "LICENSE", "GNU GPL version 3")
        self.write(self.root / "COPYRIGHT", "Scriba attribution")
        folder = self.root / "LICENSES/upstream/python-example/versions/1.0-2"
        self.write(folder / "COPYING", "Exact upstream notice")
        evidence = {"schema_version": 1, "package": package, "version": "1.0-2", "files": [
            {"file": "COPYING", "source_member": "example-1.0/COPYING",
             "sha256": hashlib.sha256(b"Exact upstream notice").hexdigest()}]}
        self.write(folder / "PROVENANCE.json", json.dumps(evidence))
        finish.licences(self.internal, prefix=self.prefix, root=self.root, toc_dir=self.tocs)
        out = self.internal / "licenses"
        self.assertEqual((out / "upstream/python-example/versions/1.0-2/COPYING").read_text(), "Exact upstream notice")
        data = json.loads((out / "PACKAGES.json").read_text())
        self.assertEqual(data["missing_licence_files"], [])
        self.assertIn("upstream/python-example/versions/1.0-2/COPYING",
                      data["packages"][0]["upstream_licence_files"])
        self.assertIn("(upstream/python-example/versions/1.0-2/)", (out / "THIRD-PARTY.md").read_text())
        self.write(folder / "COPYING", "unverified replacement")
        with self.assertRaisesRegex(ValueError, "upstream notice hash mismatch"):
            finish.licences(self.internal, prefix=self.prefix, root=self.root, toc_dir=self.tocs)

    def test_notices_for_multiple_verified_versions_select_only_the_exact_input(self):
        package = "mingw-w64-ucrt-x86_64-example"
        folder = self.root / "LICENSES/upstream/example"
        def notice(destination, version, text):
            self.write(destination / "COPYING", text)
            self.write(destination / "PROVENANCE.json", json.dumps({
                "schema_version": 1, "package": package, "version": version,
                "files": [{"file": "COPYING", "sha256": hashlib.sha256(text.encode()).hexdigest()}]}))
        notice(folder, "1.0-1", "old verified text")
        current = folder / "versions/2.0-1"
        notice(current, "2.0-1", "current verified text")
        self.assertEqual(finish.upstream_notices({"package": package, "version": "1.0-1"}, self.root),
                         [folder / "PROVENANCE.json", folder / "COPYING"])
        self.assertEqual(finish.upstream_notices({"package": package, "version": "2.0-1"}, self.root),
                         [current / "PROVENANCE.json", current / "COPYING"])
        with self.assertRaisesRegex(ValueError, "package/version mismatch"):
            finish.upstream_notices({"package": package, "version": "3.0-1"}, self.root)
        self.assertEqual((folder / "COPYING").read_text(), "old verified text")
        self.write(current / "COPYING", "different unverified text")
        with self.assertRaisesRegex(ValueError, "notice hash mismatch"):
            finish.upstream_notices({"package": package, "version": "2.0-1"}, self.root)

    def test_generated_schema_cache_records_xml_inputs_instead_of_a_false_owner(self):
        xml = "share/glib-2.0/schemas/example.gschema.xml"
        package = self.package("schemas", [xml])
        generated = self.write(self.tocs / "_pyi_gschema_compilation" / "gschemas.compiled", "compiled")
        self.write(generated.parent / "example.gschema.xml")
        self.write(self.internal / "share/glib-2.0/schemas/gschemas.compiled", "compiled")
        self.collect.append(("share/glib-2.0/schemas/gschemas.compiled", str(generated), "DATA"))
        data = self.build_inventory()
        self.assertEqual(data["packages"][0]["package"], package)
        self.assertEqual(data["packages"][0]["files"], [])
        cache = data["generated_files"][0]
        self.assertEqual(cache["file"], "_internal/share/glib-2.0/schemas/gschemas.compiled")
        self.assertEqual(cache["inputs"], [{"package": package, "package_file": "ucrt64/" + xml,
                                           "sha256": hashlib.sha256(b"bytes").hexdigest()}])

    def test_toc_input_is_literal_data_and_never_executed(self):
        marker = self.home / "executed"
        self.write(self.tocs / "Analysis-00.toc", f"__import__('pathlib').Path({str(marker)!r}).touch()")
        with self.assertRaisesRegex(ValueError, "literal TOC"):
            finish.inventory(self.internal, prefix=self.prefix, root=self.root, toc_dir=self.tocs)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
