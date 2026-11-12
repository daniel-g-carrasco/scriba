"""Desktop operations finish their async calls and distinguish cancellation from failure."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup

Gtk, Adw = _setup.gtk_or_skip()
from gi.repository import Gio, GLib  # noqa: E402

from scriba.gui import ui  # noqa: E402


class Window:
    def __init__(self):
        self.messages = []

    def toast(self, message):
        self.messages.append(message)


class FileDialogTest(unittest.TestCase):
    def test_user_dismissal_and_application_cancellation_are_silent(self):
        for domain, code in ((Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED),
                             (Gtk.DialogError.quark(), Gtk.DialogError.CANCELLED),
                             (Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)):
            with self.subTest(domain=domain, code=code):
                window, chosen = Window(), []
                dialog = mock.Mock()
                dialog.select_folder_finish.side_effect = GLib.Error.new_literal(domain, "cancelled", code)
                with contextlib.redirect_stderr(io.StringIO()) as stderr:
                    ui.file_dialog_done(window, "select_folder_finish", chosen.append)(dialog, object())
                self.assertEqual(window.messages, [])
                self.assertEqual(chosen, [])
                self.assertEqual(stderr.getvalue(), "")

    def test_a_real_error_is_reported_to_the_user_and_the_log(self):
        window, chosen = Window(), []
        dialog = mock.Mock()
        dialog.select_folder_finish.side_effect = GLib.Error.new_literal(
            Gio.io_error_quark(), "Access denied by the shell", Gio.IOErrorEnum.PERMISSION_DENIED)
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            ui.file_dialog_done(window, "select_folder_finish", chosen.append)(dialog, object())
        self.assertEqual(chosen, [])
        self.assertEqual(len(window.messages), 1)
        self.assertIn("Access denied", window.messages[0])
        self.assertNotIn("g-io-error-quark", window.messages[0])
        self.assertIn("Access denied", stderr.getvalue())

    def test_a_chosen_unicode_folder_is_passed_as_its_complete_native_path(self):
        window, chosen = Window(), []
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Call con spazi e caffè"
            path.mkdir()
            dialog = mock.Mock()
            dialog.select_folder_finish.return_value = Gio.File.new_for_path(str(path))
            ui.file_dialog_done(window, "select_folder_finish", chosen.append)(dialog, object())
            self.assertEqual([Path(selected).resolve() for selected in chosen], [path.resolve()])
            self.assertEqual(Path(chosen[0]).name, "Call con spazi e caffè")
        self.assertEqual(window.messages, [])


class DesktopLaunchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "Spazi e caffè"
        self.folder.mkdir()
        self.file = self.folder / "riunione.flac"
        self.file.write_bytes(b"temporary test file")
        self.window = Window()

    def factory(self, error=None):
        launched = []

        def create(file):
            class Launcher:
                def launch(self, window, cancellable, callback, user_data):
                    launched.append(("launch", file.get_path()))
                    callback(self, object(), user_data)

                def open_containing_folder(self, window, cancellable, callback, user_data):
                    launched.append(("reveal", file.get_path()))
                    callback(self, object(), user_data)

                def launch_finish(self, result):
                    if error is not None:
                        raise error
                    return True

                open_containing_folder_finish = launch_finish
            return Launcher()
        return create, launched

    def test_missing_files_are_reported_before_asking_the_desktop_to_open_them(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            ui.open_path(self.window, str(self.folder / "missing.txt"))
            self.assertTrue(_setup.spin(lambda: bool(self.window.messages)))
        self.assertIn("missing.txt", self.window.messages[0])
        self.assertIn("missing.txt", stderr.getvalue())

    def test_a_desktop_error_is_completed_and_reported(self):
        failure = GLib.Error.new_literal(Gio.io_error_quark(), "Desktop refused the file", Gio.IOErrorEnum.FAILED)
        factory, launched = self.factory(failure)
        with mock.patch.object(Gtk.FileLauncher, "new", side_effect=factory), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            ui.open_path(self.window, str(self.file))
            self.assertTrue(_setup.spin(lambda: bool(self.window.messages)))
        self.assertEqual([(action, Path(path).resolve()) for action, path in launched],
                         [("launch", self.file.resolve())])
        self.assertIn("Desktop refused", self.window.messages[0])
        self.assertIn("Desktop refused", stderr.getvalue())

    def test_a_desktop_error_before_launching_is_also_reported(self):
        failure = GLib.Error.new_literal(Gio.io_error_quark(), "Desktop unavailable", Gio.IOErrorEnum.FAILED)
        factory, _launched = self.factory()
        launcher = factory(Gio.File.new_for_path(str(self.file)))
        launcher.launch = mock.Mock(side_effect=failure)
        with mock.patch.object(Gtk.FileLauncher, "new", return_value=launcher), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            ui.open_path(self.window, str(self.file))
            self.assertTrue(_setup.spin(lambda: bool(self.window.messages)))
        self.assertIn("Desktop unavailable", self.window.messages[0])
        self.assertIn("Desktop unavailable", stderr.getvalue())

    def test_reveal_selects_an_existing_file_and_opens_the_nearest_folder_for_a_missing_one(self):
        factory, launched = self.factory()
        with mock.patch.object(Gtk.FileLauncher, "new", side_effect=factory):
            ui.reveal(self.window, str(self.file))
            self.assertTrue(_setup.spin(lambda: len(launched) == 1))
            ui.reveal(self.window, str(self.folder / "gone" / "missing.flac"))
            self.assertTrue(_setup.spin(lambda: len(launched) == 2))
        self.assertEqual([(action, Path(path).resolve()) for action, path in launched],
                         [("reveal", self.file.resolve()), ("launch", self.folder.resolve())])
        self.assertEqual(Path(launched[1][1]).name, "Spazi e caffè")
        self.assertEqual(self.window.messages, [])


if __name__ == "__main__":
    unittest.main()
