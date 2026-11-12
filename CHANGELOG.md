# Changelog

What changed in Scriba, version by version, the newest first. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
are `0.x` while the app evolves. Stable Windows and Linux packages are
available from GitHub Releases. Linux Flatpak builds and runtime checks use GNOME Platform 50. The app's metainfo carries a shorter description.

## [Unreleased]

## [0.1.1] - 2026-11-12

### Fixed
- Folder selection buttons now open the folder picker from Recording,
  Transcription and both folder settings in Preferences.

## [0.1.0] - 2026-11-12

### Changed
- Scriba uses GPL-3.0-or-later. Distribution tooling collects dependency
  licences and corresponding sources for Windows and Linux packages.
- Required speech models download automatically with visible progress in
  one Models page; recording remains available during the download.
- Recording controls keep their positions as devices and recording states
  change. Windows Preferences can be resized and scrolled on small screens.
- Closing the window keeps Scriba in the notification area when available,
  with activity icons and controls. Preferences can disable this behaviour;
  quitting waits for recording finalisation and outstanding file writes.
- CI reuses Flatpak dependency builds and skips packaging for documentation-only
  changes while still running tests. Release tags retain the full build checks.
- Voice names are configurable and kept in the meeting JSON. The microphone
  uses My Name from the first transcript; saved meetings reopen for name
  corrections without transcribing again, with previous names suggested.
- Listening balance makes new audio and meeting files from the original
  microphone/output tracks, preserving earlier versions and transcripts.
- Update proposals and installation wait until recording and transcription
  finish, including paused recordings and file finalisation.
- Voice previews use short WAV excerpts, avoiding a native tagged-MP3 crash;
  subtitle timestamps round milliseconds with carry into minutes and hours.
- The app icon is a centred, upright pen nib.
- Windows and Linux CI check pull requests and build development artifacts.
  Release signing and the Flatpak repository require a version tag.
- Linux packages include CPU and Vulkan whisper.cpp, ONNX Runtime API 29,
  numerical dependencies and Italian translations; recording sees PulseAudio
  sources and monitors on PipeWire desktops.
- The graphics cards are tested as soon as the speech models are there,
  on a sample of speech Scriba carries (27 seconds of Collodi's
  Pinocchio, read for LibriVox, in the public domain), not at the first
  recording opened. The test also checks the words: a card that is
  faster but writes the sample wrong (more than one word in ten) is left
  out for a slower one that writes it right, and the window says so. The
  preferences show the outcome and run the test again (Test Again);
  `scriba benchmark` runs it without a window.

### Added
- The Windows build carries everything it works with: whisper.cpp (the
  Vulkan back end for every graphics card and the processor's), ffmpeg
  built from FFmpeg's own sources under the LGPL, onnxruntime, and
  GStreamer with the plugins the recorder and Listen need; nothing has to
  be installed besides it, and nothing else on the computer is used. The
  speech models are downloaded at the first start (about 600 MB).
  `scriba-cli.exe` is the same program for a console.
- `scriba record SECONDS --out FOLDER` records without a window, with the
  devices the window would choose; `scriba check [FILE...]` says where
  each engine is, which models are there, whether the recorder has what
  it needs, and tests each file with the native diagnostic player, muted.

### Initial capabilities
- Initial version: a meeting recorded (the microphone and the
  computer's sound on separate tracks, with automatic device selection
  that the user checks against the meters, and warnings while recording; FLAC
  files that stay readable after a crash, joined at the end in one .mka
  with a mix to listen to) and transcribed on the computer (whisper.cpp,
  each track on its own, the microphone as the user's voice), or any
  recording transcribed, with the voices told apart and named, the
  subtitles and a copy of a video with them inside; the models downloaded
  once and checked; the graphics cards tested at the first recording and
  the time said before the work starts; `scriba transcribe` for the same
  without a window.
