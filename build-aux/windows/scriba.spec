# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for Windows, under MSYS2 UCRT64 (the only MSYS2
# environment with libadwaita). Run from the repository root:
#   python tools/compile_po.py && python tools/make_ico.py && pyinstaller --noconfirm build-aux/windows/scriba.spec
#   python tools/finish_bundle.py dist/Scriba
# A spec file, not flags: the gi hook collects GTK 3 unless told otherwise.
#
# Besides the app, the build carries its engines, beside its own libraries
# in _internal (app/engines/bundle.py looks there and only there):
# whisper.cpp's whisper-cli and whisper-bench with ggml's back ends (they
# are loaded at run time from the program's own folder, so the analysis
# does not see them), ffmpeg and ffprobe (the LGPL build of
# tools/build_ffmpeg.sh), onnxruntime's library, numpy, scipy and
# scikit-learn, and GStreamer with the plugins the recorder and GTK's
# player need, and those only. The speech models are not in it: they are
# downloaded at the first start (app/models.py).
import os
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent.parent
prefix = Path(sys.prefix)              # MSYS2: C:/msys64/ucrt64
BIN = prefix / 'bin'

datas = [(str(ROOT / 'data' / 'icons'), 'share/icons')]
# the style sheets stand beside the gui modules, where gui/ui.py looks for
# them: PyInstaller takes the modules by itself, not the files next to them
datas += [(str(sheet), 'scriba/gui') for sheet in sorted((ROOT / 'src' / 'scriba' / 'gui').glob('*.css'))]
# the sample of speech the cards are tested on, its text and its origin
# (app/jobs.py finds them from PACKAGE_DIR, as the style sheets)
datas.append((str(ROOT / 'src' / 'scriba' / 'samples'), 'scriba/samples'))
binaries = []

if (ROOT / 'locale').is_dir():
    datas.append((str(ROOT / 'locale'), 'share/locale'))
else:
    print('WARNING: locale/ missing: run tools/compile_po.py first (English only)')

# gdbus.exe lets GLib start a session bus, which GApplication needs to keep
# one instance running (a second launch hands its files to the first)
gdbus = shutil.which('gdbus') or str(BIN / 'gdbus.exe')
if Path(gdbus).is_file():
    binaries.append((gdbus, '.'))
else:
    print('WARNING: gdbus.exe not found: every launch would open another window')

# CA bundle for the app's own HTTPS calls (the update check, the models):
# MSYS2's OpenSSL does not always see the Windows certificate store
for ca in (prefix / 'etc' / 'ssl' / 'certs' / 'ca-bundle.crt', prefix / 'etc' / 'ssl' / 'cert.pem'):
    if ca.is_file():
        datas.append((str(ca), 'certs'))
        break
else:
    print('WARNING: no CA bundle found: the update check may fail on TLS')


def engine(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f'ERROR: {path} is missing (see .github/workflows/windows.yml for the packages)')
    binaries.append((str(path), '.'))


# whisper.cpp: the two programs; their DLLs (libwhisper, ggml, ggml-base,
# the GCC runtime) come with them, found by the analysis
engine(BIN / 'whisper-cli.exe')
engine(BIN / 'whisper-bench.exe')
# ggml's back ends, which ggml loads by itself from the program's folder:
# Vulkan (every card: NVIDIA, AMD, Intel), and the processor's, one per
# family of processors (ggml picks the best this computer runs), and BLAS:
# 44 kB over OpenBLAS, which numpy brings anyway, and without it the
# processor's run of the test went from 1.8 to 1.4 times real time
# on reference audio. Not OpenCL nor RPC: not used
backends = ['ggml-vulkan.dll', 'ggml-blas.dll', *sorted(p.name for p in BIN.glob('ggml-cpu-*.dll'))]
for name in backends:
    engine(BIN / name)

# ffmpeg and ffprobe: the minimal static build (tools/build_ffmpeg.sh).
# Scriba is GPL-3.0-or-later; keeping this smaller LGPL build is a size and
# dependency choice. The MSYS2 alternative remains experimental: release
# checks and source collection currently expect our minimal build.
LGPL_FFMPEG = ROOT / 'build' / 'ffmpeg-lgpl' / 'bin'
if (LGPL_FFMPEG / 'ffmpeg.exe').is_file():
    engine(LGPL_FFMPEG / 'ffmpeg.exe')
    engine(LGPL_FFMPEG / 'ffprobe.exe')       # tools/finish_bundle.py adds its BUILD.txt and licence
elif os.environ.get('SCRIBA_GPL_FFMPEG') == '1':
    print('WARNING: experimental MSYS2 ffmpeg: release checks expect the minimal static build')
    engine(BIN / 'ffmpeg.exe')
    engine(BIN / 'ffprobe.exe')
else:
    raise SystemExit('ERROR: no LGPL ffmpeg in build/ffmpeg-lgpl: run tools/build_ffmpeg.sh first '
                     '(or SCRIBA_GPL_FFMPEG=1 to experiment with MSYS2\'s build)')

# The disposal backport is built separately, without replacing the MSYS2 installation.
GSTPLAY = ROOT / 'build/gstplay/bin/libgstplay-1.0-0.dll'
engine(GSTPLAY)

# onnxruntime's C library (the speech filter, the voices), called with
# ctypes: the analysis cannot see it
engine(BIN / 'onnxruntime.dll')

# the licences of the Python distributions given away with the build
for dist in ('numpy', 'scipy', 'scikit-learn', 'joblib', 'threadpoolctl', 'PyGObject', 'pycairo'):
    try:
        datas += copy_metadata(dist)
    except Exception as e:      # a distribution the build lacks is said, not fatal
        print(f'WARNING: no metadata for {dist}: {e}')

# GStreamer's plugins: what the recorder makes (app/recorder.py, NEEDED) and
# what GTK's player opens for Listen (playbin3: the containers, the parsers
# and the decoders of the recordings Scriba opens, the sink). The AAC
# decoder is Windows' own (mediafoundation): faad is GPL, fdk-aac has a
# licence of its own; the video's H.264 the same (d3d11, through the card).
# GTK's player itself asks for three more (seen in the frozen build with
# GST_DEBUG, 11/11/2026: without them nothing plays): giostreamsrc (gio:
# it reads the file through GIO), d3d12convert (d3d12: its video path on
# Windows) and scaletempo (audiofx). The echo canceller (app/engines/echo.py,
# ELEMENTS) adds webrtcdsp (bad: webrtcdsp and webrtcechoprobe, on
# libwebrtc-audio-processing-1-3.dll, BSD-3-Clause, and libgstbadaudio),
# interleave (deinterleave) and wavenc (good); concat, queue and the file
# elements are coreelements', wavparse is there already.
GST_PLUGINS = [
    'coreelements', 'wasapi2', 'audioconvert', 'audioresample', 'audiorate', 'level', 'flac',
    'playback', 'typefindfunctions', 'autodetect', 'volume', 'gio', 'audiofx',
    'isomp4', 'matroska', 'wavparse', 'audioparsers', 'ogg',
    'mpg123', 'opus', 'vorbis', 'mediafoundation',
    'd3d11', 'd3d12', 'videoparsersbad', 'videoconvertscale',
    'webrtcdsp', 'interleave', 'wavenc',
]

a = Analysis(
    [str(Path(SPECPATH) / 'scriba_main.py')],
    pathex=[str(ROOT / 'src')],
    binaries=binaries,
    datas=datas,
    # every module of the app: some are imported only on Windows, or inside
    # a function, or through an engine the analysis does not follow
    hiddenimports=collect_submodules('scriba'),
    hooksconfig={
        'gi': {'module-versions': {'Gtk': '4.0', 'Gdk': '4.0', 'Adw': '1', 'GdkPixbuf': '2.0',
                                   'Graphene': '1.0', 'Gsk': '4.0', 'Gst': '1.0'},
               'languages': ['it', 'en']},
        'gstreamer': {'include_plugins': GST_PLUGINS},
    },
    # not numpy.f2py: scipy's array API layer copies every name of numpy,
    # f2py's too, and without it the voices fail at their first import
    excludes=['tkinter', 'pytest', 'IPython', 'matplotlib', 'pandas', 'PIL', 'sympy'],
)

pyz = PYZ(a.pure)

# Dependency discovery can also find the stock DLL. Select the verified custom
# origin explicitly before COLLECT, so its TOC never claims a pacman input.
a.binaries = [(name, str(GSTPLAY), kind) if name == GSTPLAY.name else (name, source, kind)
              for name, source, kind in a.binaries]
if sum(name == GSTPLAY.name for name, _source, _kind in a.binaries) != 1:
    raise SystemExit('ERROR: GstPlay backport was not collected exactly once')

common = dict(exclude_binaries=True, icon=str(ROOT / 'build-aux' / 'windows' / 'scriba.ico'),
              manifest=str(Path(SPECPATH) / 'scriba.manifest'))
exe = EXE(pyz, a.scripts, name='Scriba', console=False, **common)
# the same program with a console: for scripts that wait for the end of a
# command and read what it says (scriba-cli transcribe ...)
cli = EXE(pyz, a.scripts, name='scriba-cli', console=True, **common)

coll = COLLECT(exe, cli, a.binaries, a.datas, name='Scriba')
