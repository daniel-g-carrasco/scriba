#!/bin/bash
# The README's pictures (tools/readme_pictures.py), each scene in a throwaway home
# (/home/anna, an empty tmpfs: the pictures show the folders, never the real ones)
# and on a Broadway display of its own, never in the desktop session:
#   gtk4-broadwayd --address 127.0.0.1 :15 &
#   tools/readme_pictures.sh OUT_FOLDER record [--scale 2] [--dark]
set -e
repo="$(cd "$(dirname "$0")/.." && pwd)"
out="$(mkdir -p "$1" && cd "$1" && pwd)"; shift
home=/home/anna
real_home="$(readlink -f /home)/anna"
exec bwrap --dev-bind / / --tmpfs "$(readlink -f /home)" --bind "$repo" "$real_home/scriba" --bind "$out" "$real_home/out" \
  --chdir "$home/scriba" --unsetenv WAYLAND_DISPLAY --unsetenv DISPLAY --setenv HOME "$home" \
  --setenv XDG_CONFIG_HOME "$home/.config" --setenv XDG_DATA_HOME "$home/.local/share" --setenv XDG_CACHE_HOME "$home/.cache" \
  --setenv XDG_MUSIC_DIR "$home/Music" --setenv GDK_BACKEND broadway --setenv BROADWAY_DISPLAY "${BROADWAY_DISPLAY:-:15}" \
  "${PYTHON:-python3}" tools/readme_pictures.py --out "$home/out" --scene "$@"
