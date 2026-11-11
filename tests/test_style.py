#!/usr/bin/env python3
"""The style's values are written once, in style/tokens.toml: the files
made from them are what the tool makes today (not edited by hand, not left
behind by a change of the tokens), the tokens are in the format, and the
gui writes no measure or colour of its own. The same file in every app of
mine; the format is in the template's docs/10-stile-proposta.md.

DEBTS lists what is still written by hand, one line each with the reason:
a new one is not accepted, and one that is gone must leave the list, so
the list can only get shorter.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# "gui/window.py spacing=12": "why it is still there, and when it goes"
# (no line number: a line added above must not make a debt look new)
DEBTS: dict[str, str] = {}


def tool():
    """tools/make_style.py, loaded from its file: tools/ is no package."""
    spec = importlib.util.spec_from_file_location("make_style", ROOT / "tools" / "make_style.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_style = tool()


class TheStyleIsWhatTheTokensGive(unittest.TestCase):
    def test_the_generated_files_are_what_the_tool_makes(self):
        for path, made in make_style.outputs(make_style.read()).items():
            name = path.relative_to(ROOT).as_posix()
            self.assertTrue(path.is_file(), f"{name} is missing: run tools/make_style.py")
            # read as text: a checkout on Windows may have turned the line
            # ends, and that is not a change by hand
            on_disk = path.read_text(encoding="utf-8")
            if on_disk != made:
                self.fail(f"{name} is not what style/tokens.toml gives ({make_style.first_difference(made, on_disk)}). "
                          "Either it was edited by hand (change the tokens instead), or the tokens changed and "
                          "the tool was not run: python3 tools/make_style.py")

    def test_every_value_set_reaches_both_files(self):
        tokens = make_style.read()
        css, values = make_style.css(tokens), make_style.values(tokens)
        entries = make_style.entries(tokens)
        self.assertTrue(entries)
        for css_name, python_name, value, unit in entries:
            self.assertIn(f"  {css_name}: ", css)
            self.assertIn(f"{python_name} = {value!r}\n", values)
            self.assertIn(unit, ("", "px", "ms"))

    def test_the_gui_writes_no_measure_of_its_own(self):
        found = {line.split(":", 1)[0] + " " + line.split(" ", 1)[1]: line for line in make_style.by_hand()}
        new = sorted(line for key, line in found.items() if key not in DEBTS)
        self.assertEqual(new, [], "written by hand, take them from the style's values (gui/style_values.py in the "
                                  "code, var(--...) in gui/app.css):\n" + "\n".join(new))
        paid = sorted(set(DEBTS) - set(found))
        self.assertEqual(paid, [], "debts paid: take these lines out of DEBTS")


class TheFormat(unittest.TestCase):
    """What the tool refuses: a value that silently never arrives is worse
    than a tool that stops."""

    def good(self) -> dict:
        return {"format": 1, "space": {"s": 6, "m": 8}, "radius": {"card": 12},
                "type": {"family": "", "body": 13, "semibold": 600},
                "color": {"warning": "#e5a50a", "light": {"accent": ""}, "dark": {"accent": ""}}}

    def refused(self, change) -> str:
        tokens = self.good()
        change(tokens)
        with self.assertRaises(make_style.TokenError) as caught:
            make_style.check(tokens)
        return str(caught.exception)

    def test_the_example_is_accepted(self):
        make_style.check(self.good())

    def test_a_newer_format_stops_the_tool(self):
        self.assertIn("format", self.refused(lambda t: t.update(format=2)))

    def test_an_unknown_section_is_not_ignored(self):
        self.assertIn("[shadow]", self.refused(lambda t: t.update(shadow={"card": 2})))

    def test_the_spacing_is_a_closed_scale_that_grows(self):
        self.assertIn("space.huge", self.refused(lambda t: t["space"].update(huge=64)))
        self.assertIn("grows", self.refused(lambda t: t["space"].update(s=8, m=6)))
        self.assertIn("grows", self.refused(lambda t: t["space"].update(s=8, m=8)))

    def test_a_key_says_a_role_not_a_number(self):
        self.assertIn("radius.8", self.refused(lambda t: t["radius"].update({"8": 8})))

    def test_measures_are_whole_numbers(self):
        self.assertIn("radius.card", self.refused(lambda t: t["radius"].update(card="12px")))
        self.assertIn("radius.card", self.refused(lambda t: t["radius"].update(card=1.5)))
        self.assertIn("radius.card", self.refused(lambda t: t["radius"].update(card=True)))
        self.assertIn("type.semibold", self.refused(lambda t: t["type"].update(semibold=6)))

    def test_a_colour_is_hex_or_left_to_the_toolkit(self):
        self.assertIn("color.warning", self.refused(lambda t: t["color"].update(warning="red")))
        self.assertIn("color.dark.accent", self.refused(lambda t: t["color"]["dark"].update(accent="#FFF")))

    def test_an_empty_string_gives_nothing(self):
        names = [python for _css, python, _value, _unit in make_style.entries(self.good())]
        self.assertEqual(names, ["SPACE_S", "SPACE_M", "RADIUS_CARD", "TEXT_BODY", "WEIGHT_SEMIBOLD", "APP_WARNING"])

    def test_the_two_files_as_written(self):
        tokens = self.good()
        self.assertIn("  --space-m: 8px;\n", make_style.css(tokens))
        self.assertIn("  --weight-semibold: 600;\n", make_style.css(tokens))
        self.assertIn("  --app-warning: #e5a50a;\n", make_style.css(tokens))
        self.assertIn("SPACE_M = 8\n", make_style.values(tokens))
        self.assertIn("APP_WARNING = '#e5a50a'\n", make_style.values(tokens))
        tokens["type"]["family"] = "Inter"
        self.assertIn('  --text-family: "Inter";\n', make_style.css(tokens))
        self.assertIn("TEXT_FAMILY = 'Inter'\n", make_style.values(tokens))

    def test_a_colour_by_scheme_is_not_made_for_gtk_yet(self):
        tokens = self.good()
        tokens["color"]["dark"]["accent"] = "#3584e4"
        make_style.check(tokens)                    # the format has it
        with self.assertRaises(make_style.TokenError):
            make_style.css(tokens)                  # this generator says it cannot, and does not drop it


class WhatIsWrittenByHand(unittest.TestCase):
    """The two searches tried on text written for the purpose."""

    def test_a_style_sheet(self):
        sheet = ("/* 12px in a comment is no rule */\n"
                 ".zone { border-radius: var(--radius-card); margin: 0; }\n"
                 ".zone { border: 2px dashed alpha(currentColor, 0.25); }\n"
                 ".note {\n  color: #c01c28;\n  padding: 0 var(--space-m) 6px;\n  background: rgba(0, 0, 0, 0.1);\n}\n")
        self.assertEqual(make_style.hand_written(sheet), [(3, "2px"), (5, "#c01c28"), (6, "6px"), (7, "rgba(")])

    def test_a_module(self):
        source = ("box = Box(spacing=12, margin_top=SPACE_L, margin_end=0, homogeneous=True)\n"
                  "box.set_margin_start(6)\n"
                  "box.set_spacing(SPACE_M)\n"
                  "window = Window(default_width=520)\n")
        self.assertEqual(make_style.literal_measures(source), [(1, "spacing=12"), (2, "set_margin_start(6)")])


if __name__ == "__main__":
    unittest.main()
