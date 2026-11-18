#!/usr/bin/env python3
"""Scriba's own catalogue: compiled from the translations it has, read in
Italian, its plurals. How the language is chosen is the shared module's
test (test_i18n.py, a copy of the model's, with a catalogue of its own)."""

import os
import unittest

import _setup

from scriba import TEXTDOMAIN, i18n

VARS = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")
CATALOGUE = _setup.ROOT / "locale" / "it" / "LC_MESSAGES" / f"{TEXTDOMAIN}.mo"


@unittest.skipUnless(CATALOGUE.exists(), "tools/compile_po.py first")
class CatalogueTest(unittest.TestCase):
    def setUp(self):
        saved = {var: os.environ.pop(var, None) for var in VARS}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) for k, v in saved.items() if v is not None])
        before = i18n._translation, i18n._domain, i18n._checkout       # the other tests read the source strings
        self.addCleanup(lambda: (setattr(i18n, "_translation", before[0]), setattr(i18n, "_domain", before[1]),
                                 setattr(i18n, "_checkout", before[2])))

    def test_the_checkout_s_catalogue_is_read(self):
        i18n.setup(TEXTDOMAIN, _setup.ROOT, "it")
        self.assertEqual(i18n._("Preferences"), "Preferenze")
        self.assertEqual(i18n.language(), "it")

    def test_the_compiled_catalogue_is_not_older_than_its_translations(self):
        # 13/11/2026: a catalogue compiled before the strings of the echo were added left the note of a
        # transcript made from the sources in English, under an Italian interface
        source = _setup.ROOT / "po" / "it.po"
        self.assertGreaterEqual(CATALOGUE.stat().st_mtime, source.stat().st_mtime,
                                "po/it.po is newer than its catalogue: run tools/compile_po.py")
        i18n.setup(TEXTDOMAIN, _setup.ROOT, "it")
        self.assertTrue(i18n._("The microphone heard the computer's sound (loudspeakers) in {share} of the "
                               "recording ({time}): there it was taken out of your track before transcribing, so "
                               "the others' words come from the computer's track only.").startswith("Il microfono"))

    def test_plurals(self):
        i18n.setup(TEXTDOMAIN, _setup.ROOT, "it")
        self.assertEqual(i18n.ngettext("{n} doubtful line", "{n} doubtful lines", 1), "{n} battuta dubbia")
        self.assertEqual(i18n.ngettext("{n} doubtful line", "{n} doubtful lines", 2), "{n} battute dubbie")


if __name__ == "__main__":
    unittest.main()
