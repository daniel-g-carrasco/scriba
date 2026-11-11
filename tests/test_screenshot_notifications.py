"""Developer screenshots leave Windows notification registration untouched."""

import os
import types
import unittest
from unittest import mock

import _setup


@unittest.skipUnless(_setup.WINDOWS, "Windows notification registration")
class ScreenshotNotificationsTest(unittest.TestCase):
    def check_mode(self, screenshot):
        _setup.gtk_or_skip()
        from scriba import ENV_PREFIX
        from scriba.app import wintoast
        from scriba.gui.application import App
        app = types.SimpleNamespace(toasts=None)
        environ = dict(os.environ)
        environ.pop(f"{ENV_PREFIX}_SCREENSHOT", None)
        if screenshot:
            environ[f"{ENV_PREFIX}_SCREENSHOT"] = "window.png"
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch.object(wintoast, "register_app") as register, \
                mock.patch.object(wintoast, "Toasts") as toasts:
            App._windows_notifications(app)
            if screenshot:
                register.assert_not_called()
                toasts.assert_not_called()
                self.assertIsNone(app.toasts)
            else:
                register.assert_called_once()
                toasts.assert_called_once()
                self.assertIs(app.toasts, toasts.return_value)

    def test_screenshot_mode_does_not_register_or_create_notifications(self):
        self.check_mode(True)

    def test_normal_startup_still_registers_and_creates_notifications(self):
        self.check_mode(False)
