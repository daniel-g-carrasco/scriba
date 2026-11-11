#!/usr/bin/env python3
"""How far a text heard is from the one expected, word by word."""

import unittest

import _setup  # noqa: F401

from scriba.core.words import difference, edits, normalised

SAMPLE = ("C'era una volta... Un re! diranno subito i miei piccoli lettori. No, ragazzi, avete sbagliato.")
# what turbo wrote on the sample on 11/11/2026: no capitals, no punctuation, no apostrophes
HEARD = "cera una volta un re diranno subito i miei piccoli lettori no ragazzi avete sbagliato"


class WordsTest(unittest.TestCase):
    def test_case_punctuation_apostrophes_and_accents_do_not_count(self):
        self.assertEqual(normalised("C'era una volta... Un re!"), ["cera", "una", "volta", "un", "re"])
        self.assertEqual(normalised("d’inverno, perché È così"), ["dinverno", "perche", "e", "cosi"])
        self.assertEqual(normalised("  -- ... "), [])
        self.assertEqual(difference(SAMPLE, HEARD), 0.0)

    def test_a_word_changed_put_in_or_left_out_is_one_error_each(self):
        self.assertEqual(edits(["a", "b", "c"], ["a", "x", "c"]), 1)
        self.assertEqual(edits(["a", "b", "c"], ["a", "b", "y", "c"]), 1)
        self.assertEqual(edits(["a", "b", "c"], ["a", "c"]), 1)
        self.assertEqual(edits(["a", "b"], ["b", "a"]), 2)
        self.assertAlmostEqual(difference("un pezzo da catasta", "un pezzo di catasta"), 0.25)

    def test_nothing_heard_is_all_wrong_and_words_made_up_can_go_past_it(self):
        self.assertEqual(difference(SAMPLE, ""), 1.0)
        self.assertGreater(difference("sì", "sì grazie a tutti e arrivederci"), 1.0)
        self.assertEqual(difference("", ""), 0.0)
        self.assertEqual(difference("", "qualcosa"), 1.0)


if __name__ == "__main__":
    unittest.main()
