"""Keep Pango escaping at the widget properties that actually parse markup."""

import ast
import re
import unittest

import _setup

PLAIN_WIDGETS = {
    "Adw.ActionRow", "Adw.Banner", "Adw.ButtonRow", "Adw.ComboRow", "Adw.EntryRow",
    "Adw.ExpanderRow", "Adw.SpinRow", "Adw.SwitchRow", "Adw.Toast",
}
MARKUP_PROPERTIES = {
    "Adw.AboutDialog": {"comments"},
    "Adw.PreferencesGroup": {"title", "description"},
    "Adw.StatusPage": {"description"},
}


def is_escape(node):
    return isinstance(node, ast.Call) and ast.unparse(node.func) in {"escape", "GLib.markup_escape_text"}


def markup_issues(source):
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    allowed = {statement.value for function in tree.body
               if isinstance(function, ast.FunctionDef) and function.name == "escape"
               for statement in function.body if isinstance(statement, ast.Return)}
    for call in calls:
        name = ast.unparse(call.func)
        properties = {kw.arg: kw.value for kw in call.keywords}
        if name in PLAIN_WIDGETS:
            mode = properties.get("use_markup")
            if not isinstance(mode, ast.Constant) or mode.value is not False:
                yield call.lineno, f"{name} needs use_markup=False for plain text"
        markup = [value for key, value in properties.items() if key in MARKUP_PROPERTIES.get(name, ())]
        if name.endswith((".set_description", ".set_comments")):
            markup.extend(call.args)
        if name.endswith(".add_legal_section"):
            markup.append(call.args[1])
            # The custom licence is an intentional Pango link with escaped values.
            allowed.update(part for part in ast.walk(call.args[3]) if is_escape(part))
        for value in markup:
            if is_escape(value):
                allowed.add(value)
            else:
                yield value.lineno, f"{name} needs escaped text for its markup property"
    for call in calls:
        if is_escape(call) and call not in allowed:
            yield call.lineno, "escape() used outside a markup property"


class GuiMarkupTest(unittest.TestCase):
    def test_gui_escapes_only_where_markup_is_parsed(self):
        gui = _setup.ROOT / "src" / _setup.PACKAGE / "gui"
        issues = [f"{path.name}:{line}: {message}" for path in gui.rglob("*.py")
                  for line, message in markup_issues(path.read_text(encoding="utf-8"))]
        self.assertEqual(issues, [])

    def test_checker_rejects_escaped_plain_text_and_unescaped_markup(self):
        source = """Adw.Toast(use_markup=False, title=escape(text))
self.banner.set_title(escape(text))
Gtk.Label(label=escape(text))
Adw.AlertDialog(heading=escape(text))
Adw.PreferencesGroup(title=text)
Adw.ActionRow(title=text)
dialog.set_comments(text)
"""
        self.assertEqual(sorted(line for line, _ in markup_issues(source)), [1, 2, 3, 4, 5, 6, 7])

    def test_italian_catalogue_contains_literal_punctuation(self):
        catalogue = (_setup.ROOT / "po" / "it.po").read_text(encoding="utf-8")
        entities = re.findall(r"&(?:apos|quot|amp|lt|gt|#\d+|#x[0-9a-fA-F]+);", catalogue)
        self.assertEqual(entities, [])
