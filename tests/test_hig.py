#!/usr/bin/env python3
"""The interface follows GNOME's guidelines and libadwaita's own pieces
(the template's docs/01-filosofia.md, "Come si presenta"). What can be read
from the source is held here, with the AST: nothing is imported.

An inner window is an Adw.Dialog (or one of its kinds), never a bare
Gtk.Window or a Gtk.Dialog built by hand: P7M Extractor's first window of
signatures was a Gtk.Window with a label of lines in it, with another title
bar and another look than every other window of the app (10/11/2026).

ALLOWED lists what is there for a reason, one line each: a new one is not
accepted without its reason, and one that is gone must leave the list.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BARE = ("Gtk.Window", "Gtk.Dialog", "Gtk.MessageDialog", "Gtk.AboutDialog", "Gtk.ApplicationWindow")
# "file: what": "why"
ALLOWED: dict[str, str] = {
    "gui/window.py: Gtk.ApplicationWindow": "the main window on Windows: a plain GTK window keeps the system's "
                                            "title bar (and with it Windows' snapping); its content is libadwaita's",
}


def gui_dir(root: Path = ROOT) -> Path:
    found = [p for p in sorted((root / "src").iterdir()) if (p / "__init__.py").is_file()]
    assert len(found) == 1, found
    return Path(found[0].as_posix()) / "gui"


def dotted(node) -> str:
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return node.id if isinstance(node, ast.Name) else ""


def bare_windows(source: str) -> list[tuple[int, str]]:
    """(line, name) of every bare GTK window the source builds or takes as
    the base of a class (also through a choice between two bases). A name
    in an annotation, in an isinstance() or before a further attribute
    (Gtk.Window.list_toplevels) is no window made."""
    tree = ast.parse(source)
    skipped = set()
    for node in ast.walk(tree):
        notes = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args
            notes = [a.annotation for a in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                                            arguments.vararg, arguments.kwarg) if a is not None]
            notes.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            notes = [node.annotation]
        elif isinstance(node, ast.Attribute):
            notes = [node.value]                    # Gtk.Window.something: the class is only named
        elif isinstance(node, ast.Call) and dotted(node.func) == "isinstance":
            notes = node.args[1:]
        for note in notes:
            if note is not None:
                skipped.update(id(inner) for inner in ast.walk(note))
    found = {(node.lineno, dotted(node)) for node in ast.walk(tree)
             if isinstance(node, ast.Attribute) and dotted(node) in BARE and id(node) not in skipped}
    return sorted(found)


class InnerWindowsAreDialogs(unittest.TestCase):
    def test_no_bare_gtk_window_in_the_gui(self):
        gui = gui_dir()
        found = {}
        for path in sorted(gui.rglob("*.py")):
            for line, name in bare_windows(path.read_text(encoding="utf-8")):
                found.setdefault(f"gui/{path.relative_to(gui).as_posix()}: {name}", f"{path.name}:{line}")
        new = sorted(f"{where} ({key.split(': ')[1]})" for key, where in found.items() if key not in ALLOWED)
        self.assertEqual(new, [], "an inner window is an Adw.Dialog (Adw.PreferencesDialog, Adw.AboutDialog, "
                                  "Adw.AlertDialog), with boxed lists and libadwaita's rows inside:\n" + "\n".join(new))
        gone = sorted(set(ALLOWED) - set(found))
        self.assertEqual(gone, [], "no longer there: take these lines out of ALLOWED")

    def test_the_search_sees_what_it_should(self):
        source = ("class A(Adw.Dialog):\n    pass\n"
                  "class B(Gtk.Window):\n    pass\n"
                  "Base = Gtk.ApplicationWindow if WINDOWS else Adw.ApplicationWindow\n"
                  "def f(parent):\n    return Gtk.Window(transient_for=parent)\n"
                  "note = 'Gtk.Window in a string is no window'\n"
                  "def g(window: Gtk.Window) -> Gtk.Window:\n    return window\n"
                  "everyone = Gtk.Window.list_toplevels()\n"
                  "ok = isinstance(everyone[0], Gtk.Window)\n")
        self.assertEqual(bare_windows(source), [(3, "Gtk.Window"), (5, "Gtk.ApplicationWindow"), (7, "Gtk.Window")])


def icon_names(source: str) -> set[str]:
    """Every symbolic icon a source names: its strings ending in -symbolic."""
    tree = ast.parse(source)
    dynamic_parts = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
                     for part in ast.walk(node)}
    return {node.value for node in ast.walk(tree)
            if id(node) not in dynamic_parts and isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value.endswith("-symbolic") and " " not in node.value}


def adwaita() -> Path | None:
    """The Adwaita icon theme this machine's GTK draws with (MSYS2's on
    Windows, the system's or the runtime's on Linux), or None."""
    import sys
    for prefix in (Path(sys.prefix), Path("/usr"), Path("/app"), Path("/usr/local")):
        theme = prefix / "share" / "icons" / "Adwaita"
        if (theme / "index.theme").is_file():
            return theme
    return None


class IconsAreTheThemes(unittest.TestCase):
    """Only icons the bundled Adwaita has: a missing one is a broken image,
    seen only on the user's computer (docs/01-filosofia.md of the template)."""

    def test_every_symbolic_icon_named_in_the_gui_is_in_adwaita(self):
        theme = adwaita()
        if theme is None:
            self.skipTest("no Adwaita icon theme on this machine")
        there = {p.stem for p in theme.rglob("*-symbolic.svg")}
        own = {"io.github.daniel_g_carrasco.scriba-symbolic"}
        missing = sorted(f"{path.name}: {name}" for path in sorted(gui_dir().rglob("*.py"))
                         for name in icon_names(path.read_text(encoding="utf-8")) - own if name not in there)
        self.assertEqual(missing, [], "not in Adwaita " + str(theme))

    def test_dynamic_suffixes_are_not_mistaken_for_complete_icon_names(self):
        self.assertEqual(icon_names('name = f"{app}-symbolic"\nicon = "folder-symbolic"'), {"folder-symbolic"})

    def test_the_search_sees_the_names(self):
        self.assertEqual(icon_names('Gtk.Image(icon_name="folder-symbolic")\nx = "a b-symbolic"\n'),
                         {"folder-symbolic"})


if __name__ == "__main__":
    unittest.main()
