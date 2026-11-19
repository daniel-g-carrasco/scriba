#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/compile_po.py: the catalogues compiled with msgfmt or, where there
is none, with its own compiler (the same catalogue); --check, which refuses
a broken catalogue and the messages src asks for that are missing; and
--update, which gives xgettext what it cannot see on its own. In apps made
for the purpose, in temporary folders."""

import gettext
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup

TOOL = _setup.ROOT / "tools" / "compile_po.py"
INIT = '"""An app made for the test."""\nAPP_ID = "org.example.my-app"\nSLUG = "my-app"\nTEXTDOMAIN = SLUG\n'
HEADER = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'


def load(path: Path = TOOL, name: str = "compile_po"):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class App(unittest.TestCase):
    """A temporary app: src/my_app, po/, and the tool copied into its tools/."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "src" / "my_app").mkdir(parents=True)
        (self.root / "src" / "my_app" / "__init__.py").write_text(INIT, encoding="utf-8")
        (self.root / "po").mkdir()
        (self.root / "tools").mkdir()
        shutil.copy(TOOL, self.root / "tools" / TOOL.name)

    def source(self, name: str, text: str) -> None:
        path = self.root / "src" / "my_app" / name
        path.write_text(text, encoding="utf-8")

    def po(self, text: str, name: str = "it.po") -> Path:
        path = self.root / "po" / name
        path.write_text(HEADER + text, encoding="utf-8")
        return path

    def run_tool(self, *args):
        # what the tool says has «» in it: UTF-8 whatever the console's code page
        return subprocess.run([sys.executable, str(self.root / "tools" / TOOL.name), *args], cwd=self.root,
                              capture_output=True, text=True, encoding="utf-8",
                              env=dict(os.environ, PYTHONIOENCODING="utf-8"))

    def tool(self):
        """The copy in the app's tools/, as the app loads it (with the app's po_messages.py, if any)."""
        return load(self.root / "tools" / TOOL.name, f"compile_po_{id(self)}")


class Domains(App):
    def test_required_catalogues_cannot_disappear_silently(self):
        self.source('main.py', '_("Application")\n')
        primary = self.po('msgid "Application"\nmsgstr "Applicazione"\n')
        (self.root / 'tools/po_messages.py').write_text(
            "REQUIRED_CATALOGS = ('po/it.po',)\n", encoding='utf8')
        self.assertEqual(self.run_tool('--check').returncode, 0)
        primary.unlink()
        for args in (('--check',), ('--python',)):
            result = self.run_tool(*args)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('po/it.po', result.stdout + result.stderr)

    def test_explicit_domains_compile_and_check_independently(self):
        self.source('main.py', '_("Application")\n')
        kit = self.root / 'src/my_app/kit'
        kit.mkdir()
        (kit / 'page.qml').write_text('Theme.tr("Library")', encoding='utf8')
        (self.root / 'po/library').mkdir()
        self.po('msgid "Application"\nmsgstr "Applicazione"\n')
        library = self.po('msgid "Library"\nmsgstr "Libreria"\n', 'library/it.po')
        (self.root / 'tools/po_messages.py').write_text(
            "DOMAINS = {'library': {'sources': ['src/my_app/kit'], 'po': 'po/library', "
            "'dest': 'src/my_app/kit/locale', 'qml_calls': ['Theme.tr']}}\n", encoding='utf8')
        result = self.run_tool('--check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.run_tool('--python').returncode, 0)
        self.assertTrue((kit / 'locale/it/LC_MESSAGES/library.mo').is_file())
        library.write_text(HEADER + 'msgid "Unrelated"\nmsgstr "Altro"\n', encoding='utf8')
        failed = self.run_tool('--check')
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Library', failed.stdout)
        self.assertIn('no line of src', failed.stdout)


class Compiler(unittest.TestCase):
    """The compiler of the tool: the same catalogue as GNU's for what the catalogues use."""

    @classmethod
    def setUpClass(cls):
        cls.tool = load()

    def catalogue(self, text: str):
        return gettext.GNUTranslations(io.BytesIO(self.tool.mo(self.tool.parse(text))))

    PO = ('msgid ""\nmsgstr ""\n"Language: it\\n"\n"POT-Creation-Date: 2026-10-22\\n"\n'
          '"Content-Type: text/plain; charset=UTF-8\\n"\n'
          '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
          '#: a.py:1\nmsgid "Open"\nmsgstr "Apri"\n\n'
          'msgctxt "menu"\nmsgid "Open"\nmsgstr "Apri…"\n\n'
          'msgid "{n} file"\nmsgid_plural "{n} files"\nmsgstr[0] "{n} documento"\nmsgstr[1] "{n} documenti"\n\n'
          'msgid ""\n"two\\n"\n"lines \\"quoted\\""\nmsgstr "due\\nrighe \\"tra virgolette\\""\n\n'
          '#, fuzzy\nmsgid "Close"\nmsgstr "Chiudi forse"\n\n'
          'msgid "Save"\nmsgstr ""\n\n'
          '#~ msgid "Gone"\n#~ msgstr "Andato"\n')

    def test_contexts_plurals_escapes_and_what_msgfmt_leaves_out(self):
        t = self.catalogue(self.PO)
        self.assertEqual(t.gettext("Open"), "Apri")
        self.assertEqual(t.pgettext("menu", "Open"), "Apri…")
        self.assertEqual(t.ngettext("{n} file", "{n} files", 1), "{n} documento")
        self.assertEqual(t.ngettext("{n} file", "{n} files", 3), "{n} documenti")
        self.assertEqual(t.gettext('two\nlines "quoted"'), 'due\nrighe "tra virgolette"')
        self.assertEqual(t.gettext("Close"), "Close")              # fuzzy: left out
        self.assertEqual(t.gettext("Save"), "Save")                # untranslated: left out
        self.assertEqual(t.gettext("Gone"), "Gone")                # obsolete
        self.assertEqual(t.info()["language"], "it")
        self.assertNotIn("pot-creation-date", t.info())            # as msgfmt: a build repeatable

    def test_a_translation_with_a_placeholder_its_source_has_not_is_refused(self):
        with self.assertRaises(self.tool.PoError):
            self.tool.check(self.tool.parse('msgid "{n} files"\nmsgstr "{count} file"\n'))
        self.tool.check(self.tool.parse('msgid "{n} files ({{x}})"\nmsgstr "{n:d} file"\n'))     # fine

    def test_a_broken_file_is_said_with_its_line(self):
        for text, said in (('msgid "a"\nmsgstr "b"\nmsgstr "c"\n', "msgstr twice"),
                           ('"loose"\n', "a string outside an entry"), ('msgid a\n', "not a quoted string"),
                           ('msgid "a"\nnonsense\n', "what is this?")):
            with self.subTest(said), self.assertRaisesRegex(self.tool.PoError, said):
                self.tool.parse(text, "it.po")

    @unittest.skipUnless(shutil.which("msgfmt"), "msgfmt is not on the PATH (GNU gettext: Linux, MSYS2)")
    def test_the_same_catalogue_as_msgfmt(self):
        with tempfile.TemporaryDirectory() as folder:
            po, out = Path(folder) / "it.po", Path(folder) / "it.mo"
            po.write_text(self.PO, encoding="utf-8")
            subprocess.run(["msgfmt", "-o", str(out), str(po)], check=True)
            with open(out, "rb") as f:
                self.assertEqual(gettext.GNUTranslations(f)._catalog, self.catalogue(self.PO)._catalog)


class Compiling(App):
    def test_the_domain_is_the_packages_and_python_compiles_where_there_is_no_msgfmt(self):
        self.po('msgid "Open"\nmsgstr "Apri"\n')
        run = self.run_tool("--python")
        self.assertEqual(run.returncode, 0, run.stderr)
        out = self.root / "locale" / "it" / "LC_MESSAGES" / "my-app.mo"
        self.assertEqual(run.stdout.strip(), str(out))
        with open(out, "rb") as f:
            self.assertEqual(gettext.GNUTranslations(f).gettext("Open"), "Apri")
        dest = self.root / "elsewhere"
        self.assertEqual(self.run_tool("--python", "--dest", str(dest)).returncode, 0)
        self.assertTrue((dest / "it" / "LC_MESSAGES" / "my-app.mo").is_file())

    def test_without_msgfmt_on_the_path_its_own_compiler(self):
        self.po('msgid "Open"\nmsgstr "Apri"\n')
        tool = self.tool()
        with mock.patch.object(tool.shutil, "which", return_value=None):
            written = tool.compile_all(self.root / "locale", root=self.root)
        self.assertEqual([p.relative_to(self.root).as_posix() for p in written], ["locale/it/LC_MESSAGES/my-app.mo"])

    def test_a_translation_that_adds_a_placeholder_stops_the_build(self):
        self.po('msgid "{n} files"\nmsgstr "{count} file"\n')
        run = self.run_tool("--python")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("{n} files", run.stderr)


class Check(App):
    def test_a_clean_catalogue_passes(self):
        self.source("window.py", 'from .i18n import _\n_("Open")\n')
        self.po('msgid "Open"\nmsgstr "Apri"\n')
        run = self.run_tool("--check")
        self.assertEqual((run.returncode, run.stdout.strip()), (0, "po: every catalogue is in order"), run.stderr)

    def test_the_check_finds_what_it_refuses(self):
        po = self.po('msgid "Import an image"\nmsgstr "Importa un?immagine"\n\n'
                     'msgid "Import an image"\nmsgstr "Importa un\'immagine"\n\n'
                     'msgid "{n} modules"\nmsgstr "{m} moduli"\n\n'
                     'msgid "Inv. %1"\nmsgstr "Inverter"\n\n'
                     'msgid "Nobody writes this anywhere"\nmsgstr "Nessuno"\n')
        tool = load()
        found = tool.problems(po, ({"Import an image", "{n} modules", "Inv. %1"}, ""), self.root)
        kinds = [next(k for k in ("broken encoding", "key twice", "placeholders differ", "no line of src") if k in f)
                 for f in found]
        self.assertEqual(sorted(kinds), ["broken encoding", "key twice", "no line of src",
                                         "placeholders differ", "placeholders differ"], found)
        broken = self.root / "po" / "broken.po"
        broken.write_bytes('msgid "a"\nmsgstr "è"\n'.encode("cp1252"))
        said = tool.problems(broken, root=self.root)
        self.assertTrue(said and "not UTF-8" in said[0], said)

    def test_missing_messages_and_a_second_catalogue_fail_the_check(self):
        (self.root / "src" / "my_app" / "page.qml").write_text('Text { text: qsTr("Keep me") }', encoding="utf-8")
        self.source("window.py", 'from .i18n import _\nmessage = "Variable message"\n_(message)\n')
        self.po("")
        (self.root / "po" / "more").mkdir()
        self.po("", "more/it.po")
        run = self.run_tool("--check")
        self.assertEqual(run.returncode, 1)
        self.assertIn("it.po: missing message: |Keep me", run.stdout)
        self.assertIn("it.po: missing message: |Variable message", run.stdout)
        self.assertIn("po/more/it.po: a second catalogue; its entries go in po/it.po", run.stdout)
        self.po('msgid "Keep me"\nmsgstr "Conservami"\n\nmsgid "Variable message"\nmsgstr "Messaggio"\n')
        (self.root / "po" / "more" / "it.po").unlink()
        self.assertEqual(self.tool().check_all(self.root), [])


class Messages(App):
    def test_python_and_qml_without_comment_or_identifier_noise(self):
        source = self.root / "src"
        (source / "page.qml").write_text('''
// qsTr("Comment only")
/* qsTr("Block comment") */
Text { text: qsTr("Rename object") + qsTr('It\\'s ready') }
Text { text: qsTr("Long " + "message") }
Text { text: qsTr("Rename object", "disambiguation") }
Text { text: qsTr(runtimeValue) }
''', encoding="utf-8")
        (source / "page.py").write_text('''
labels = {'internal_id': 'Chart title'}
copy = {key: _(value) for key, value in labels.items()}
def tile(key, title):
    return _(title)
tile('open_id', 'Open project')
message = 'Variable message'
_(message)
_('Direct message')
pgettext('menu', 'Roof')
ngettext('One file', 'Many files', 2)
TABLE = {'Local fallback': 'Traduzione'}
''', encoding="utf-8")
        literal: set = set()
        found = load().required_messages(self.root, literal)
        self.assertEqual(found, {(None, text) for text in (
            "Rename object", "It's ready", "Long message", "Chart title", "Open project", "Variable message",
            "Direct message", "One file")} | {("menu", "Roof")})
        self.assertEqual(literal, {(None, "Direct message"), (None, "One file"), ("menu", "Roof")},
                         "what xgettext finds on its own")

    def test_the_apps_own_rules_from_its_po_messages(self):
        (self.root / "tools" / "po_messages.py").write_text('''
TABLES = ("TABLE",)
CONTEXTS = {"short_locale": "short"}
SOURCES = ("extensions/*.py",)
def messages(root):
    import json
    return {(None, json.loads((root / "src" / "my_app" / "rules.json").read_text(encoding="utf-8"))["why"])}
''', encoding="utf-8")
        self.source("rules.json", '{"why": "From the app\'s rule"}')
        self.source("page.py", "TABLE = {'Local fallback': 'Traduzione'}\n")
        self.source("plan.py", 'from .short_locale import _\n_("Short text")\n')
        (self.root / "extensions").mkdir()
        (self.root / "extensions" / "files.py").write_text('_("From an extension")\n', encoding="utf-8")
        tool = self.tool()
        self.assertEqual(tool.required_messages(self.root), {
            (None, "Local fallback"), ("short", "Short text"), (None, "From an extension"),
            (None, "From the app's rule")})
        rest = ('msgid "Local fallback"\nmsgstr "Ripiego"\n\nmsgid "From an extension"\nmsgstr "Da un\'estensione"\n\n'
                'msgid "From the app\'s rule"\nmsgstr "Dalla regola"\n')
        for entry in ('msgid "Short text"\nmsgstr "Corto"\n\n', 'msgctxt "short"\nmsgid "Short text"\nmsgstr "Corto"\n\n'):
            with self.subTest(entry=entry):
                self.po(entry + rest)
                self.assertEqual(tool.check_all(self.root), [], "its context, or the message without one")
        self.assertEqual([p.relative_to(self.root).as_posix() for p in tool.sources(self.root)],
                         ["extensions/files.py", "src/my_app/__init__.py", "src/my_app/page.py", "src/my_app/plan.py"])

    def test_without_po_messages_a_table_is_not_a_source_of_messages(self):
        self.source("page.py", "TABLE = {'Local fallback': 'Traduzione'}\n")
        self.assertEqual(load().required_messages(self.root), set())


@unittest.skipUnless(shutil.which("xgettext") and shutil.which("msgmerge"),
                     "xgettext and msgmerge are not on the PATH (GNU gettext: Linux, MSYS2)")
class Update(App):
    def test_xgettext_gets_what_it_cannot_see_and_nothing_more(self):
        self.source("window.py", 'from .i18n import _\n_("Direct message")\n')
        self.po('msgid "Direct message"\nmsgstr "Messaggio diretto"\n')
        run = self.run_tool("--update", "--python")
        self.assertEqual(run.returncode, 0, run.stderr)
        pot = (self.root / "po" / "my-app.pot").read_text(encoding="utf-8")
        self.assertIn("#: src/my_app/window.py:2\nmsgid \"Direct message\"", pot)
        self.assertNotIn("qml_and_tables", pot, "every message literal: the .pot is xgettext's alone")
        self.source("tiles.py", 'from .i18n import _\nmessage = "Variable message"\n_(message)\n')
        self.assertEqual(self.run_tool("--update", "--python").returncode, 0)
        pot = (self.root / "po" / "my-app.pot").read_text(encoding="utf-8")
        self.assertIn('msgid "Variable message"', pot, "a message in a variable reaches the catalogue")
        self.assertIn('msgid "Variable message"', (self.root / "po" / "it.po").read_text(encoding="utf-8"))
        self.assertEqual(pot.count('msgid "Direct message"'), 1)


if __name__ == "__main__":
    unittest.main()
