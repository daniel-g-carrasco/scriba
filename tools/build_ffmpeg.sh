#!/bin/bash
# The ffmpeg and ffprobe the Windows build carries: FFmpeg's release
# sources, configured with no GPL and no non-free part (so under the
# LGPL 2.1 or later), with only what Scriba uses, linked statically (two
# programs and no DLLs). Daniel's decision of 11/11/2026: MSYS2's ffmpeg
# is GPL-3.0-or-later and brings 150 libraries along; Scriba is MIT.
#
#   tools/build_ffmpeg.sh            (under MSYS2 UCRT64, from anywhere)
#
# Needs: make diffutils mingw-w64-ucrt-x86_64-{gcc,nasm,pkgconf,zlib}.
# Writes only under build/ (ignored by git): the tarball and its sources in
# build/ffmpeg-src, the programs in build/ffmpeg-lgpl/bin, and
# build/ffmpeg-lgpl/BUILD.txt with the version, the tarball's address and
# SHA-256 and the configure line: what the LGPL asks to be able to give
# with the programs (the build puts it in licenses/ffmpeg). A second run
# with the same version and configuration does nothing.
#
# What Scriba asks of ffmpeg (app/engines/ffmpeg.py, app/recording.py):
# - read the recordings people have: Matroska/WebM (OBS), MP4/M4A/MOV
#   (Teams, phones), MP3, WAV, FLAC, Ogg, raw AAC and AC-3, and the srt
#   Scriba writes; decode their sound (AAC, MP3, Opus, Vorbis, FLAC, PCM,
#   AC-3/E-AC-3, ALAC); the video is only copied, but ffprobe looks at it
#   (the H.264, HEVC, VP8 and VP9 decoders and parsers, so that it finds
#   their sizes without complaining);
# - write WAV 16 kHz (pcm_s16le) for the engines, Matroska (the recording
#   joined, the video with the subtitles: the srt encoder), the mix as
#   M4A (the native AAC encoder, the ipod muxer), and nothing (null);
# - the filters of the mix and of the measure: aresample, aformat, volume,
#   amix, alimiter, ebur128 (and anull, the graph's own, and the buffers);
# - read files and write the progress on a pipe.
set -euo pipefail

VERSION="9.0.2"
# the release tarball's SHA-256, read on 11/11/2026 from the file whose
# signature (ffmpeg-9.0.2.tar.xz.asc) is good for FFmpeg's release key
# FCF9 86EA 15E6 E293 A564 4F10 B432 2F04 D676 58D8
SHA256="8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"
URL="https://ffmpeg.org/releases/ffmpeg-${VERSION}.tar.xz"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/build/ffmpeg-src"
OUT="$ROOT/build/ffmpeg-lgpl"
JOBS="$(nproc 2>/dev/null || echo 4)"

DEMUXERS="matroska,mov,mp3,wav,w64,flac,ogg,aac,ac3,eac3,srt,webm_dash_manifest"
DECODERS="aac,aac_latm,mp3,mp3float,mp2,mp2float,opus,vorbis,flac,alac,ac3,eac3,pcm_s16le,pcm_s16be,pcm_s24le,\
pcm_s24be,pcm_s32le,pcm_f32le,pcm_f64le,pcm_u8,pcm_alaw,pcm_mulaw,subrip,srt,h264,hevc,vp8,vp9"
PARSERS="aac,aac_latm,ac3,flac,mpegaudio,opus,vorbis,h264,hevc,vp8,vp9,av1"
ENCODERS="pcm_s16le,aac,srt,subrip"
MUXERS="wav,matroska,webm,mov,mp4,ipod,null,srt"
FILTERS="aresample,aformat,anull,volume,amix,alimiter,ebur128,abuffer,abuffersink,buffer,buffersink,null,format,\
copy,anullsrc,atrim,trim,asetpts,setpts"
BSFS="aac_adtstoasc,h264_mp4toannexb,hevc_mp4toannexb,extract_extradata,vp9_superframe"

CONFIGURE=(
  --disable-everything --disable-autodetect --disable-doc --disable-ffplay --disable-network
  --disable-debug --disable-shared --enable-static --enable-small
  --enable-ffmpeg --enable-ffprobe --enable-zlib --enable-swresample
  --disable-avdevice --disable-swscale
  --enable-protocol=file,pipe
  --enable-demuxer="$DEMUXERS" --enable-decoder="$DECODERS" --enable-parser="$PARSERS"
  --enable-encoder="$ENCODERS" --enable-muxer="$MUXERS" --enable-filter="$FILTERS" --enable-bsf="$BSFS"
  --extra-ldflags=-static --pkg-config-flags=--static
)

if [ -f "$OUT/BUILD.txt" ] && grep -qF "configure ${CONFIGURE[*]}" "$OUT/BUILD.txt" \
   && [ -x "$OUT/bin/ffmpeg.exe" ] && [ -x "$OUT/bin/ffprobe.exe" ]; then
  echo "ffmpeg $VERSION (LGPL) already built in $OUT"
  exit 0
fi

mkdir -p "$SRC"
TARBALL="$SRC/ffmpeg-${VERSION}.tar.xz"
if [ ! -f "$TARBALL" ]; then
  curl -fL --retry 4 --retry-all-errors -o "$TARBALL.part" "$URL"
  mv "$TARBALL.part" "$TARBALL"
fi
GOT="$(sha256sum "$TARBALL" | cut -d' ' -f1)"
if [ "$GOT" != "$SHA256" ]; then
  echo "ffmpeg-${VERSION}.tar.xz: SHA-256 $GOT, not $SHA256: not used" >&2
  exit 1
fi
rm -rf "$SRC/ffmpeg-${VERSION}"
tar -xJf "$TARBALL" -C "$SRC"
cd "$SRC/ffmpeg-${VERSION}"

./configure "${CONFIGURE[@]}"
# the configuration must not have turned GPL or non-free on by any road
if grep -Eq '^#define CONFIG_(GPL|NONFREE) 1' config.h; then
  echo "the configuration is not LGPL: refused" >&2
  exit 1
fi
make -j"$JOBS"
# the two programs only (no install: its prefix would be written into the build)
rm -rf "$OUT"
mkdir -p "$OUT/bin"
cp ffmpeg.exe ffprobe.exe "$OUT/bin/"
strip "$OUT/bin/ffmpeg.exe" "$OUT/bin/ffprobe.exe"

LICENCE="$("$OUT/bin/ffmpeg.exe" -hide_banner -L | head -3 | tr '\n' ' ')"
case "$LICENCE" in
  *"Lesser General Public"*) ;;
  *) echo "ffmpeg says it is not LGPL: $LICENCE" >&2; exit 1 ;;
esac
cp COPYING.LGPLv2.1 "$OUT/COPYING.LGPLv2.1"
{
  echo "FFmpeg $VERSION, built for Scriba under the GNU Lesser General Public License 2.1 or later"
  echo
  echo "Sources: $URL"
  echo "SHA-256 of the sources: $SHA256"
  echo "Built on $(date -u +%Y-%m-%d) with MSYS2 UCRT64, $(gcc --version | head -1)"
  echo "by tools/build_ffmpeg.sh of https://github.com/daniel-g-carrasco/scriba"
  echo
  echo "configure ${CONFIGURE[*]}"
  echo
  echo "ffmpeg -L says: $LICENCE"
  echo
  echo "To build it again, or a changed version: the same sources, the same configure line, then"
  echo "make && make install. The licence's text is COPYING.LGPLv2.1 beside this file."
} > "$OUT/BUILD.txt"
ls -la "$OUT/bin"
echo "ffmpeg $VERSION (LGPL) in $OUT"
