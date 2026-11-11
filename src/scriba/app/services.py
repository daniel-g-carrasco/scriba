"""This app's own instances of the services that know no app.

paths, log, updates, minisign, win32, wintoast, diagnostics (and i18n at
the root) take a name, an id, a version as arguments, so that the same
modules serve every app of mine, and can one day live in one package
shared with the Qt apps. This is the one place of the app layer where
they meet the names in the package's __init__."""

from __future__ import annotations

from scriba import EXE_NAME, GITHUB_REPO, PUBLIC_KEY, SLUG, __version__

from .paths import Paths


def app_paths(environ=None) -> Paths:
    """Where this app keeps its files."""
    return Paths.from_env(SLUG, EXE_NAME, environ)


def project():
    """Whose releases an update comes from (updates.py is loaded only when
    asked: on Linux the Flatpak brings the updates)."""
    from .updates import Project
    return Project(repo=GITHUB_REPO, slug=SLUG, exe_name=EXE_NAME, version=__version__, public_key=PUBLIC_KEY)
