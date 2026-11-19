#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the files that sit next to the published Flatpak repository:

  <pages>/<slug>.flatpakref     one-click install (GNOME Software, KDE Discover)
  <pages>/<slug>.flatpakrepo    adds the remote only
  <pages>/index.html            short landing page with the commands
  <pages>/.nojekyll             keep GitHub Pages from touching the ostree tree

Usage: flatpak_repo_files.py PAGES_DIR BASE_URL GPG_KEY_BASE64 VERSION
           [--comment TEXT] [--description TEXT] [--key-page PATH TITLE] [--sources]

The app's names (APP_ID, APP_NAME, SLUG, WEBSITE, GITHUB_REPO) are read
from the one package under src/, without importing it. The comment is the
metainfo's <summary>, the description the first sentence of its
<description>, unless they are given. --key-page names where the key's
fingerprint is written (a path in the repository and the link's text; the
README's Flatpak section by default); --sources adds the paragraph that
points to the release's corresponding sources of the Linux package.
"""

import argparse
import ast
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def package(root: Path) -> Path:
    """The app's package: the one folder under src/ with an __init__.py."""
    src = Path(root) / "src"
    found = sorted(p for p in src.iterdir() if (p / "__init__.py").is_file()) if src.is_dir() else []
    if len(found) != 1:
        raise SystemExit(f"{src}: one package expected, {len(found)} found")
    return found[0]


def identity(root: Path) -> dict[str, str]:
    """The names the package's __init__.py sets to a text (APP_ID, SLUG,
    TEXTDOMAIN...), read with ast: nothing of the app is imported."""
    tree = ast.parse((package(root) / "__init__.py").read_text(encoding="utf-8"))
    names: dict[str, str] = {}

    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return names.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = value(node.left), value(node.right)
            return left + right if left is not None and right is not None else None
        if isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                if isinstance(part, ast.FormattedValue) and part.conversion == -1 and part.format_spec is None:
                    part = value(part.value)
                elif isinstance(part, ast.Constant):
                    part = part.value
                else:
                    part = None
                if not isinstance(part, str):
                    return None
                parts.append(part)
            return "".join(parts)
        return None

    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, found = node.targets[0].id, value(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, found = node.target.id, value(node.value)
        else:
            continue
        if found is not None:
            names[target] = found
    return names


def metainfo_texts(root: Path, app_id: str) -> tuple[str, str]:
    """The metainfo's summary and the first sentence of its description,
    both in the source language ("" when there is no metainfo)."""
    path = Path(root) / "data" / f"{app_id}.metainfo.xml"
    if not path.is_file():
        return "", ""
    component = ET.parse(path).getroot()
    summary = next((" ".join(e.text.split()) for e in component.findall("summary")
                    if XML_LANG not in e.attrib and e.text), "")
    paragraph = next((" ".join("".join(e.itertext()).split()) for e in component.findall("description/p")
                      if XML_LANG not in e.attrib), "")
    first = re.match(r".*?[.!?](?=\s|$)", paragraph)
    return summary, first.group(0) if first else paragraph


def write(pages: Path, base: str, key: str, version: str, names: dict[str, str], comment: str, description: str,
          key_page: tuple[str, str] = ("#flatpak", "README"), sources: bool = False) -> None:
    app_id, app_name, slug = names["APP_ID"], names["APP_NAME"], names["SLUG"]
    website, github_repo = names["WEBSITE"], names["GITHUB_REPO"]
    base = base.rstrip("/")
    repo_url = f"{base}/repo"
    key_path, key_title = key_page
    key_url = f"https://github.com/{github_repo}" + (key_path if key_path.startswith("#") else "/" + key_path.lstrip("/"))

    (pages / f"{slug}.flatpakref").write_text(f"""[Flatpak Ref]
Name={app_id}
Branch=stable
Title={app_name}
Comment={comment}
Description={description}
Homepage={website}
Icon={base}/icon.png
Url={repo_url}
SuggestRemoteName={slug}
IsRuntime=false
RuntimeRepo=https://dl.flathub.org/repo/flathub.flatpakrepo
GPGKey={key}
""", encoding="utf-8")

    (pages / f"{slug}.flatpakrepo").write_text(f"""[Flatpak Repo]
Title={app_name}
Comment={comment}
Description={description}
Homepage={website}
Icon={base}/icon.png
Url={repo_url}
GPGKey={key}
""", encoding="utf-8")

    (pages / ".nojekyll").write_text("")

    corresponding = (f"""<p>{app_name} is free software under GPL-3.0-or-later. Download the
<a href="https://github.com/{github_repo}/releases/download/v{version}/{slug}-v{version}-linux-x86_64-sources.tar.xz">corresponding sources for this Linux package</a>,
including the collected dependency sources and build information, from the
<a href="https://github.com/{github_repo}/releases/tag/v{version}">signed release files</a>.</p>
""" if sources else "")
    (pages / "index.html").write_text(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{app_name}, Flatpak repository</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 17px/1.6 system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem; }}
  code, pre {{ background: rgba(127, 127, 127, .14); border-radius: 6px; padding: .1rem .35rem; }}
  pre {{ padding: .75rem .9rem; overflow-x: auto; }}
  a.btn {{ display: inline-block; background: #1a5fb4; color: #fff; padding: .7rem 1.2rem; border-radius: 8px; text-decoration: none; font-weight: 600; }}
  .dim {{ opacity: .75; font-size: .95rem; }}
</style>
</head>
<body>
<h1>{app_name}, Flatpak repository</h1>
<p>This is where the Flatpak of <a href="{website}">{app_name}</a> lives. Current version: <strong>{version}</strong>.</p>
<p><a class="btn" href="{base}/{slug}.flatpakref">Install {app_name}</a></p>
<p>Or from a terminal:</p>
<pre>flatpak install {base}/{slug}.flatpakref</pre>
<p>Updates arrive on their own, through GNOME Software or <code>flatpak update</code>. The GNOME runtime comes from Flathub.</p>
{corresponding}<p class="dim">The repository is signed. The key's fingerprint is in the
<a href="{key_url}">{key_title}</a>.
<a href="https://github.com/{github_repo}">Source and issues</a> are on GitHub.</p>
</body>
</html>
""", encoding="utf-8")
    print(f"written in {pages}: {slug}.flatpakref, {slug}.flatpakrepo, index.html, .nojekyll (url {repo_url})")


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description="The files next to the published Flatpak repository.")
    parser.add_argument("pages", type=Path)
    parser.add_argument("base")
    parser.add_argument("key")
    parser.add_argument("version")
    parser.add_argument("--comment", help="the one line under the name (default: the metainfo's summary)")
    parser.add_argument("--description", help="a sentence (default: the first of the metainfo's description)")
    parser.add_argument("--key-page", nargs=2, metavar=("PATH", "TITLE"), default=("#flatpak", "README"),
                        help="where the key's fingerprint is written, in the repository (default: #flatpak README)")
    parser.add_argument("--sources", action="store_true",
                        help="a paragraph with the link to the release's corresponding sources")
    args = parser.parse_args(argv)
    names = identity(root)
    summary, sentence = metainfo_texts(root, names["APP_ID"])
    write(args.pages, args.base, args.key, args.version, names,
          args.comment if args.comment is not None else summary,
          args.description if args.description is not None else sentence,
          tuple(args.key_page), args.sources)
    return 0


if __name__ == "__main__":
    sys.exit(main())
