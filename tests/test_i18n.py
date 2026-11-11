#!/usr/bin/env python3
"""The language of the interface: the preference first, then the variables
of the desktop, or on Windows (which sets none) the language of its own
interface."""

import os
import unittest
from unittest import mock

import _setup

from scriba import TEXTDOMAIN, i18n

VARS = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")
CATALOGUE = _setup.ROOT / "locale" / "it" / "LC_MESSAGES" / f"{TEXTDOMAIN}.mo"


@unittest.skipUnless(CATALOGUE.exists(), "tools/compile_po.py first")
class LanguageTest(unittest.TestCase):
    def setUp(self):
        saved = {var: os.environ.pop(var, None) for var in VARS}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) for k, v in saved.items() if v is not None])
        before = i18n._translation, i18n._domain, i18n._checkout       # the other tests read the source strings
        self.addCleanup(lambda: (setattr(i18n, "_translation", before[0]), setattr(i18n, "_domain", before[1]),
                                 setattr(i18n, "_checkout", before[2])))

    def test_the_variables_first(self):
        os.environ["LANG"] = "it_IT.UTF-8"
        with mock.patch.object(i18n, "_system_languages", return_value=["en_US"]):
            i18n.setup(TEXTDOMAIN, _setup.ROOT)
        self.assertEqual(i18n._("Preferences"), "Preferenze")

    def test_the_preference_over_the_variables(self):
        os.environ["LANG"] = "it_IT.UTF-8"
        i18n.setup(TEXTDOMAIN, _setup.ROOT, "en")
        self.assertEqual(i18n._("Preferences"), "Preferences")
        i18n.use("it")
        self.assertEqual(i18n._("Preferences"), "Preferenze")

    def test_without_variables_the_system_language(self):
        with mock.patch.object(i18n, "_system_languages", return_value=["it_IT"]):
            i18n.setup(TEXTDOMAIN, _setup.ROOT)
        self.assertEqual(i18n._("Preferences"), "Preferenze")
        with mock.patch.object(i18n, "_system_languages", return_value=["en_US"]):
            i18n.use()
        self.assertEqual(i18n._("Preferences"), "Preferences")

    def test_plurals(self):
        i18n.setup(TEXTDOMAIN, _setup.ROOT, "it")
        self.assertEqual(i18n.ngettext("{n} doubtful line", "{n} doubtful lines", 1), "{n} battuta dubbia")
        self.assertEqual(i18n.ngettext("{n} doubtful line", "{n} doubtful lines", 2), "{n} battute dubbie")


class WindowsLanguageTest(unittest.TestCase):
    @unittest.skipUnless(_setup.WINDOWS, "Windows only")
    def test_windows_interface_language(self):
        languages = i18n._system_languages()
        self.assertEqual(len(languages), 1)
        self.assertRegex(languages[0], r"^[a-z]{2,3}(_[A-Z]{2})?")

    def test_counts_as_the_locale_writes_them(self):
        self.assertIn(i18n.count(1200), ("1200", "1,200", "1.200", "1 200"))


if __name__ == "__main__":
    unittest.main()
