#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The notes of one version, from CHANGELOG.md: what its release page says
and what the update window shows (GitHub's own "generated notes" are one
line with a link).

    python3 tools/release_notes.py 0.3.45 [CHANGELOG.md] [--italian-output PATH]
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def notes(version: str, text: str) -> str:
    """The section of `version`, without its heading; "" if it has none."""
    found = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: |\Z)", text, re.S | re.M)
    return found.group(1).strip() if found else ""


ITALIAN_HEADINGS = {
    "aggiunte": "added", "modifiche": "changed", "correzioni": "fixed",
    "rimozioni": "removed", "deprecazioni": "deprecated", "sicurezza": "security",
}


def paired_notes(version: str, english: str, italian: str) -> tuple[str, str]:
    """Verify both sections and add the release identity to the Italian asset."""
    header = rf"^## \[{re.escape(version)}\]([^\n]*)$"
    en_headers = re.findall(header, english, re.M)
    it_headers = re.findall(header, italian, re.M)
    en, it = notes(version, english), notes(version, italian)
    if len(en_headers) != 1 or en_headers != it_headers:
        raise ValueError(f"the English and Italian headings must match for {version}")
    if not all(re.search(r"^[-*] +\S", body, re.M) for body in (en, it)):
        raise ValueError("both release notes need at least one item")
    en_groups = [group.casefold() for group in re.findall(r"^### (.+)$", en, re.M)]
    it_groups = [ITALIAN_HEADINGS.get(group.casefold()) for group in re.findall(r"^### (.+)$", it, re.M)]
    if en_groups != it_groups:
        raise ValueError("the English and Italian categories differ")
    return en, f"<!-- release-notes: it v{version} -->\n\n{it}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("changelog", type=Path, nargs="?", default=ROOT / "CHANGELOG.md")
    parser.add_argument("--italian-input", type=Path, default=ROOT / "CHANGELOG.it.md")
    parser.add_argument("--italian-output", type=Path)
    args = parser.parse_args()
    version = args.version.lstrip("v")
    try:
        english = args.changelog.read_text(encoding="utf-8")
        found = notes(version, english)
        if not found:
            raise ValueError(f"CHANGELOG.md has no section for {args.version}")
        if args.italian_output:
            found, italian = paired_notes(version, english, args.italian_input.read_text(encoding="utf-8"))
            with args.italian_output.open("x", encoding="utf-8", newline="\n") as output:
                output.write(italian + "\n")
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
