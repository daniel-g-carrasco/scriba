<p align="center">
  <img src="data/icons/hicolor/128x128/apps/io.github.daniel_g_carrasco.scriba.png" alt="Scriba" width="128" height="128">
</p>

<h1 align="center">Scriba</h1>

Record meetings and transcribe them on your own computer, with speakers and subtitles.

Scriba is in early development. Windows and Linux packages are being verified; no public installer, portable download or Flatpak release is available yet. The Linux Flatpak uses GNOME Platform 50 with CPU and Vulkan transcription.

Scriba records the microphone and computer sound on separate tracks, or opens an existing audio or video recording. It writes Markdown, plain text and subtitles, and can add subtitles to a copy of a video. Audio and text are processed locally. Network access is used to download speech models and check for updates.

Device selection uses the system's call-device information and sound levels. Check the selected microphone and output and their meters before recording. A real call with a Bluetooth headset still needs validation; automatic selection is not a guarantee that every call setup will be captured correctly.

The microphone's voice uses the configurable **My Name** preference, initially **Io**. Other voices can be named, and a meeting JSON can be reopened to correct names without transcribing again. Names, transcripts and listening adjustments are saved as new files. Doubtful lines stay in the text, marked `(?)`.

The listening balance can favour the microphone or the other participants when their original tracks are available. Voice previews decode only a short WAV excerpt before playing it. Recording uses progressive FLAC tracks; after the joined recording is checked, those intermediate files are removed and their original tracks remain in the MKA.

## Try the sources on Windows

Development uses Python 3.14 in MSYS2 UCRT64, GTK 4 and libadwaita. The package list and build steps are in [.github/workflows/windows.yml](.github/workflows/windows.yml).

From a UCRT64 shell with the dependencies installed:

```bash
python tools/compile_po.py
env -u MSYSTEM python bin/scriba
```

The first start downloads the checked speech models: about 600 MB for the fast transcription model, speech filter and voice embedder. A larger model is optional. The app benchmarks the available graphics cards and processor on a bundled public-domain speech sample, checking words as well as speed. Actual processing time depends on the computer and recording.

The command line is available from the same sources:

```bash
python bin/scriba transcribe recording.mkv --speakers
python bin/scriba record 60 --out RECORDINGS_FOLDER
python bin/scriba check
```

FFmpeg and whisper.cpp are driven as child processes. A local LGPL build made with `tools/build_ffmpeg.sh` is preferred to FFmpeg on the PATH. User models are stored outside the installed program, or in the folder specified by `SCRIBA_MODELS_DIR`.

## Development checks

On Windows, launch MSYS2 from PowerShell so its environment is preserved:

```powershell
$env:MSYSTEM='UCRT64'
& C:\msys64\usr\bin\bash.exe -lc 'cd /c/path/to/scriba && ruff check --select F,E9,B . && env -u MSYSTEM python -W default -W ignore::DeprecationWarning:gi.events -m unittest discover -s tests'
```

Window tests require a display. Optional engine and recording tests need their explicit test variables. Screenshots use invented data:

```bash
python tools/screenshots.py --out screenshots
python tools/make_icons.py
python tools/make_ico.py
```

Windows and Linux CI check pushes and pull requests and build development artifacts. Pull requests do not run signing or publishing jobs. A separately authorised version tag runs release signing and the signed Flatpak repository workflow. The complete-release check requires every platform's files and signatures before publishing a draft. See `docs/linux.md` for Linux dependencies, permissions and local builds.

## Licence

The application code is MIT, see [LICENSE](LICENSE). Third-party engines, models and libraries retain their own licences and credits. The intended signing process for a future release is described in [CODE_SIGNING.md](CODE_SIGNING.md).
