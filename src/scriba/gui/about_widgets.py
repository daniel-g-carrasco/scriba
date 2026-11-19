# SPDX-License-Identifier: GPL-3.0-or-later
"""Common About and troubleshooting actions."""

from gi.repository import Adw, Gio

from ..app import diagnostics, errorlog
from ..i18n import _


def troubleshooting_menu(app_name):
    menu = Gio.Menu()
    menu.append(_("Open the Error Log"), "app.error-log")
    menu.append(_("About {app}").format(app=app_name), "app.about")
    return menu


def open_error_log(paths, open_path, toast):
    path = errorlog.log_file(paths.state)
    if path.is_file():
        open_path(str(path))
    else:
        toast(_("No errors have been recorded."))


def about_dialog(metadata, cfg, paths, toolkit, run_in_thread, *, debug_filename, add_credits=None):
    dialog = Adw.AboutDialog(**metadata)
    if add_credits is not None:
        add_credits(dialog)
    head = diagnostics.summary(dialog.get_application_name(), dialog.get_version(), cfg, paths, toolkit)
    dialog.set_debug_info_filename(debug_filename)
    dialog.set_debug_info(head + "\n" + _("Measuring the space taken by the app's files…"))
    run_in_thread(lambda: diagnostics.storage(paths),
                  lambda text, error: dialog.set_debug_info(head + "\n" + (text if error is None else str(error))))
    return dialog
