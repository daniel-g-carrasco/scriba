"""Scriba: what every desktop app of mine starts from.

The package is in four layers, and the imports go one way only (tests/
test_strati.py holds them to it): core/ works things out and data/ reads
and writes formats, both plain Python that know nobody; app/ is the
services without a window (settings, paths, updates) and joins the two;
gui/ is the windows, the one place GTK comes in. The core does not know
a window exists, the window works nothing out.

The constants below are the one place the app's identity lives: the code
derives every name (the executable, the installer, the text domain, the
environment variables) from them.
"""

import sys
from pathlib import Path

__version__ = "0.1.2"

WINDOWS = sys.platform == "win32"

APP_ID = "io.github.daniel_g_carrasco.scriba"
APP_NAME = "Scriba"
SLUG = "scriba"                 # the repository, the binary on Linux, the file names of a release
EXE_NAME = "Scriba"              # the Windows executable and its folder (no spaces, as Setup likes it)
TEXTDOMAIN = SLUG
ENV_PREFIX = "SCRIBA"           # the developer aids: SCRIBA_SCREENSHOT and the like
GITHUB_REPO = "daniel-g-carrasco/scriba"
WEBSITE = f"https://github.com/{GITHUB_REPO}"
# The releases' signing key (minisign). The secret half is the CI's
# MINISIGN_SECRET_KEY; tests/test_updates.py checks that the CI verifies
# its signatures with this very key. The key below is the one shared by
# the author's apps (key id CEFFF9269F08F845): a new app may keep it or
# get a key of its own (docs/05-ci-rilascio.md of the template).
PUBLIC_KEY = "RWRF+AifJvn/zhz0o7MBObIYHgw9+sWoc4GaBK+qYnJY0AqeJm540y0a"

# the one anchor for files found from the code's own place (a checkout's
# data/, locale/): never a chain of .parent in a module, which a move breaks
PACKAGE_DIR = Path(__file__).resolve().parent
