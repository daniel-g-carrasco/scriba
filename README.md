<p align="center">
  <img src="data/icons/hicolor/128x128/apps/io.github.daniel_g_carrasco.scriba.png" alt="Scriba" width="128" height="128">
</p>

<h1 align="center">Scriba</h1>

Record meetings and transcribe them on your own computer, with speakers and subtitles.

**[Download Scriba 0.1.3 for Windows](https://github.com/daniel-g-carrasco/scriba/releases/download/v0.1.3/scriba-setup-v0.1.3-windows-x64.exe)**. Install it once; Scriba checks for newer stable releases and offers signed updates. Windows may show a SmartScreen warning because the installer has no Authenticode certificate.

[All downloads and corresponding sources](https://github.com/daniel-g-carrasco/scriba/releases/tag/v0.1.3) include the Windows portable ZIP and Linux Flatpak. Extract the entire portable ZIP before opening `Scriba.exe`. The Linux Flatpak uses GNOME Platform 50 with CPU and Vulkan transcription.

Scriba records the microphone and computer sound on separate tracks, or opens an existing audio or video recording. It writes Markdown, plain text and subtitles, and can add subtitles to a copy of a video. Audio and text are processed locally. Network access is used to download speech models and check for updates.

Device selection uses the system's call-device information and sound levels. Check the selected microphone and output and their meters before recording. On Windows, with automatic selection, Scriba switches to the microphone a call program opens after recording has started; on Windows 11 it also follows the call's mute (speech while muted is recorded but not transcribed as yours; this can be switched off in Preferences). A real call with a Bluetooth headset still needs validation; automatic selection is not a guarantee that every call setup will be captured correctly.

When the microphone picks up the computer's loudspeakers, Scriba removes that echo from the microphone's track before transcribing and in the copy to listen to; the original recording is kept unchanged. Headphones still give the best transcript.

Every recording has a folder of its own. Transcripts are written in paragraphs per speaker; subtitles are cut to at most two lines of 42 characters and 7 seconds. The microphone's voice uses the configurable **My Name** preference, initially **Io**. Other voices can be named, and a meeting JSON can be reopened to correct names without transcribing again. A new transcription of the same audio keeps the previous transcripts in a folder of previous revisions. Doubtful lines stay in the text, marked `(?)`; when more than a quarter of the text is doubtful, the files and the Result page say so and advise listening again. Files of terms chosen in Preferences (one term a line) are read at every transcription, after the terms typed for the meeting.

The listening balance can favour the microphone or the other participants when their original tracks are available, with a short preview before saving. Voice previews decode only a short WAV excerpt before playing it. Recording uses progressive FLAC tracks; after the joined recording is checked, those intermediate files are removed and their original tracks remain in the MKA.

## Try the sources on Windows

Development uses Python 3.14 in MSYS2 UCRT64, GTK 4 and libadwaita. The package list and build steps are in [.github/workflows/windows.yml](.github/workflows/windows.yml).

From a UCRT64 shell with the dependencies installed:

```bash
python tools/compile_po.py
env -u MSYSTEM python bin/scriba
```

The first start automatically downloads the checked speech models: about 600 MB for the fast transcription model, speech filter and voice embedder. Download progress and any errors appear in the Models page. These models are needed to transcribe; recording is available while they download. A larger model is optional and is downloaded only when requested from the same page. The app benchmarks the available graphics cards and processor on a bundled public-domain speech sample, checking words as well as speed. Actual processing time depends on the computer and recording.

By default, closing the window keeps Scriba running in the notification area when the desktop provides one. Its icon shows recording, pause, transcription, download and error states. Reopen the window from the icon, or choose Quit to finish saving and exit. This behaviour can be disabled in Preferences. On Linux desktops without a supported notification area, closing the window uses the normal exit procedure.

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

Scriba is licensed under GPL-3.0-or-later; see [COPYRIGHT](COPYRIGHT), [LICENSE](LICENSE) and [distribution notes](docs/licensing.md). Third-party engines, models and libraries retain their own licences and credits. Release signatures and verification are described in [CODE_SIGNING.md](CODE_SIGNING.md).
