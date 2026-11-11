#!/usr/bin/env python3
"""Write the files that sit next to the published Flatpak repository:

  <pages>/<slug>.flatpakref     one-click install (GNOME Software, KDE Discover)
  <pages>/<slug>.flatpakrepo    adds the remote only
  <pages>/index.html            short landing page with the commands
  <pages>/.nojekyll             keep GitHub Pages from touching the ostree tree

Usage: flatpak_repo_files.py PAGES_DIR BASE_URL GPG_KEY_BASE64 VERSION
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from scriba import APP_ID, APP_NAME, GITHUB_REPO, SLUG, WEBSITE  # noqa: E402

COMMENT = "Record a meeting and transcribe it on your own computer, with speakers and subtitles"
DESCRIPTION = "Scriba records microphone and computer sound, transcribes locally and names speakers."

pages, base, key, version = Path(sys.argv[1]), sys.argv[2].rstrip("/"), sys.argv[3], sys.argv[4]
repo_url = f"{base}/repo"

(pages / f"{SLUG}.flatpakref").write_text(f"""[Flatpak Ref]
Name={APP_ID}
Branch=stable
Title={APP_NAME}
Comment={COMMENT}
Description={DESCRIPTION}
Homepage={WEBSITE}
Icon={base}/icon.png
Url={repo_url}
SuggestRemoteName={SLUG}
IsRuntime=false
RuntimeRepo=https://dl.flathub.org/repo/flathub.flatpakrepo
GPGKey={key}
""", encoding="utf-8")

(pages / f"{SLUG}.flatpakrepo").write_text(f"""[Flatpak Repo]
Title={APP_NAME}
Comment={COMMENT}
Description={DESCRIPTION}
Homepage={WEBSITE}
Icon={base}/icon.png
Url={repo_url}
GPGKey={key}
""", encoding="utf-8")

(pages / ".nojekyll").write_text("")

(pages / "index.html").write_text(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{APP_NAME}, Flatpak repository</title>
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
<h1>{APP_NAME}, Flatpak repository</h1>
<p>This is where the Flatpak of <a href="{WEBSITE}">{APP_NAME}</a> lives. Current version: <strong>{version}</strong>.</p>
<p><a class="btn" href="{base}/{SLUG}.flatpakref">Install {APP_NAME}</a></p>
<p>Or from a terminal:</p>
<pre>flatpak install {base}/{SLUG}.flatpakref</pre>
<p>Updates arrive on their own, through GNOME Software or <code>flatpak update</code>. The GNOME runtime comes from Flathub.</p>
<p class="dim">The repository is signed. The key's fingerprint is in the
<a href="https://github.com/{GITHUB_REPO}/blob/main/docs/linux.md#repository-signing-key">Linux documentation</a>.
<a href="https://github.com/{GITHUB_REPO}">Source and issues</a> are on GitHub.</p>
</body>
</html>
""", encoding="utf-8")
print(f"written in {pages}: {SLUG}.flatpakref, {SLUG}.flatpakrepo, index.html, .nojekyll (url {repo_url})")
