# SPDX-License-Identifier: GPL-3.0-or-later
"""GTK update preferences and announcements."""

from html import escape

from gi.repository import Adw, Gtk

from ..app.updates import CHECK_EVERY_HOURS
from ..i18n import _


def update_preferences(channel, enabled, set_enabled, check=None):
    if channel == "flatpak":
        return None
    group = Adw.PreferencesGroup(title=escape(_("Updates")))
    updates = Adw.SwitchRow(use_markup=False, title=_("Check for updates automatically"),
                            subtitle=_("At start-up and when the window is opened again, at most every "
                                       "{hours} hours, from the project's releases on GitHub; nothing "
                                       "about you or your files is sent").format(hours=CHECK_EVERY_HOURS),
                            active=enabled)
    updates.connect("notify::active", lambda row, _prop: set_enabled(row.get_active()))
    group.add(updates)
    if check is not None:
        row = Adw.ActionRow(use_markup=False, title=_("Check for Updates…"), activatable=True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_args: check())
        group.add(row)
    return group


def update_banner(activate):
    banner = Adw.Banner(use_markup=False, button_label=_("Update…"), revealed=False)
    banner.connect("button-clicked", lambda *_args: activate())
    return banner


def update_title(app_name, version):
    return _("{app} {version} is available").format(app=app_name, version=version)


def announce_update(app_name, version, *, requested, active, banner, show, notify):
    banner(version, show)
    if requested:
        show()
    elif not active:
        notify(update_title(app_name, version), _("Click to see what is new and update."),
               tag="update", on_click=show)
