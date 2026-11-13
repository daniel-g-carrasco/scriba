"""Entry point: `scriba --version` and `--help` answer from the command
line, a command of cli.py (`scriba transcribe <file>`...) works without a
window, anything else starts the app (a file given on the command line is
opened in the window)."""

import os
import sys

from . import PACKAGE_DIR, SLUG, TEXTDOMAIN, __version__, i18n


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        # a file name the console's code page cannot show must not crash a print
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    if os.environ.get(f"{__package__.upper()}_DEV_STACKS") and not getattr(sys, "frozen", False):
        # developer aid: every thread's stack written to that file every
        # 30 s, to see where an app that seems stuck is (never the built app)
        import faulthandler
        faulthandler.dump_traceback_later(30, repeat=True,
                                          file=open(os.environ[f"{__package__.upper()}_DEV_STACKS"], "w"))
    i18n.setup(TEXTDOMAIN, PACKAGE_DIR.parent.parent)
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        print(__version__)
        return 0
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        from .i18n import _
        command = __package__.replace("_", "-")
        print(_("usage: {command} [RECORDING]").format(command=command))
        print(_("without a window: {command} transcribe FILE, {command} models, {command} benchmark [FILE] "
                "(each with --help)").format(command=command))
        return 0
    from .cli import COMMANDS                       # the commands without a window: no toolkit loaded
    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS:
        from .cli import main as cli_main
        return cli_main(sys.argv[1:])
    from scriba.app import errorlog, log
    from scriba.app.services import app_paths
    log.setup(SLUG)
    # the window's errors kept in a file too: started from the desktop
    # nobody reads stderr (app/errorlog.py)
    errorlog.install(errorlog.log_file(app_paths().state))
    from scriba.gui.application import main as app_main
    return app_main(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
