#!/usr/bin/env bash
# Build the app's Flatpak from this tree without installing it, start it
# inside the build sandbox on a private Broadway display with a private
# session bus and throw-away XDG folders, and save the window as a PNG.
# Nothing is installed, nothing touches the real session: the answer to
# "does the package start as the Flatpak ships it" after a change that
# moves files (the layers, the manifest).
#
#   tools/flatpak_try.sh [OUT.png]        from the repository's root, on Linux
#
# The names come from the manifest (the one .yaml in build-aux/flatpak): its
# "command:" is the binary, and the developer aid <COMMAND>_SCREENSHOT (the
# command in capitals, "-" as "_") makes the app save its window and quit.
# The work folder is under the home's cache: the sandbox of
# org.flatpak.Builder (a host without flatpak-builder) does not see /tmp.
set -euo pipefail
cd "$(dirname "$0")/.."
MANIFEST="$(ls build-aux/flatpak/*.yaml | head -1)"
COMMAND="$(sed -n 's/^command: *//p' "$MANIFEST" | head -1)"
PREFIX="$(echo "$COMMAND" | tr 'a-z-' 'A-Z_')"
OUT="$(realpath -m "${1:-flatpak-window.png}")"
WORK="${XDG_CACHE_HOME:-$HOME/.cache}/$COMMAND-flatpak-try"
rm -rf "$WORK"
mkdir -p "$WORK"

if command -v flatpak-builder >/dev/null; then BUILDER=(flatpak-builder); else BUILDER=(flatpak run org.flatpak.Builder); fi
"${BUILDER[@]}" --force-clean --disable-rofiles-fuse --state-dir="$WORK/state" "$WORK/build" "$PWD/$MANIFEST" \
    > "$WORK/build.log" 2>&1 || { tail -30 "$WORK/build.log"; echo "the build failed: $WORK/build.log"; exit 1; }
echo "built: $MANIFEST"
echo "version: $(flatpak build "$WORK/build" "$COMMAND" --version)"
# A private runtime folder too: a private bus that shared the real one would
# unmount the session's document portal when it ends, and every Flatpak of
# the session would fail from then on.
flatpak build "$WORK/build" sh -c "
  export XDG_RUNTIME_DIR=\$(mktemp -d) HOME=\$(mktemp -d)
  export XDG_CONFIG_HOME=\$HOME/config XDG_STATE_HOME=\$HOME/state XDG_DATA_HOME=\$HOME/data
  gtk4-broadwayd :7 >/dev/null 2>&1 &
  sleep 1
  export GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 GSK_RENDERER=cairo LANGUAGE=\${LANGUAGE:-it}
  export ${PREFIX}_SCREENSHOT=/app/window.png
  timeout 60 dbus-run-session -- $COMMAND; status=\$?
  kill %1 2>/dev/null || true
  exit \$status
"
cp "$WORK/build/files/window.png" "$OUT"
echo "window: $OUT"
rm -rf "$WORK"
