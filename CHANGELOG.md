# Changelog

What changed in Scriba, version by version, the newest first. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
are `0.x` while the app evolves. Stable Windows and Linux packages are
available from GitHub Releases. Linux Flatpak builds and runtime checks use GNOME Platform 50. The app's metainfo carries a shorter description.

## [0.1.3] - 2026-11-14

### Added
- Files of terms: in Preferences, under Terms, choose one or more plain
  text files with one term or phrase a line (lines starting with `#` are
  left out). Scriba reads them at every transcription, after the terms
  typed for the meeting, each term once. Each file shows how many terms it
  has, or why it cannot be read; a missing file does not stop the work and
  is said on the Result page. When there are more terms than the engine
  can read, the meeting's come first and the Result page says how many
  were used. The command line takes `--terms-file`, more than once.
- A line with far more text than could be said in its time, as when the
  engine writes the same word over and over, is now marked `(?)` with the
  reason "too much text for its length".
- When more than a quarter of the text is doubtful, the transcript files
  say so at the top and advise listening to the recording again, and the
  Result page shows the same warning in a banner.

### Changed
- The whole transcript opens on a page of its own from "Read the Whole
  Transcript", with a single scroll bar as tall as the window; the Result
  page shows only its first lines instead of a small scrolling box inside
  the scrolling page. The text can be selected and copied across
  paragraphs, and nothing is cut any more for long meetings.
- Updates are looked for at start and whenever the window is opened again,
  at most every 4 hours, instead of once a day (Windows). They are still
  offered with a banner, never with a dialog in the middle of a recording
  or a transcription.
- The icon is the same everywhere again, a dark disc with a light nib, on
  a light or dark Windows taskbar, in the notification area and on Linux.

### Fixed
- With the window minimized, or in the tray after a recording that could
  not start or be finished, the level meters kept the microphone and the
  computer's sound open: now no sound device stays open while the window
  is out of sight and nothing is being recorded.
- Saving the names of the voices no longer fails when another program
  (an antivirus, the search index) holds the meeting's file for an instant.

## [0.1.2] - 2026-11-13

### Added
- Echo removal: where the microphone picked up the computer's
  loudspeakers, Scriba takes that sound out of the microphone's track
  before transcribing and in the copy to listen to (WebRTC audio
  processing), so remote voices are no longer heard twice or written as
  yours. The Record page warns while the microphone hears the computer;
  with headphones nothing changes.
- With automatic device selection, when a call program opens another
  microphone after recording has started, Scriba switches to it and says
  so (Windows).
- On Windows 11 Scriba follows the call's mute while recording: what the
  microphone hears while you are muted in the call is still recorded but
  not transcribed as yours. It can be switched off in Preferences.
- The microphone and the computer's sound can be switched off and on while
  recording; the track stays aligned and records silence meanwhile.
- The listening balance can be heard as a 20-second preview before it is
  saved.
- Every recording gets a folder of its own. A file opened from elsewhere
  gets a folder in the recordings folder with a link to the original, or
  its results go beside the original (a new preference). A new
  transcription of the same audio keeps the previous transcripts in a
  folder of previous revisions.
- Unexpected errors are written to a local `errors.log` (kept under 1 MB),
  which the main menu opens; nothing is sent anywhere.

### Changed
- Transcripts (Markdown, text and the Result page) are written in
  paragraphs per speaker instead of one line per recognised fragment.
- Subtitles follow common conventions: at most two lines of 42 characters
  and 7 seconds, cut at the end of a phrase where possible, timed from the
  words; the speaker's name only where the speaker changes.
- Each voice's sample is its longest stretch heard alone, up to 15 seconds.
- After Stop, the recording's clock stops and each finishing step is named.
- The number of people speaking has its own group on the Transcribe page.
- The Precise quality's first time estimate starts from the Fast quality's
  speed on the same device and is marked as a first estimate.
- Windows: text stays sharp and at the right size on high-resolution
  screens, following each screen's scale; the taskbar icon follows a light
  or dark taskbar.

### Fixed
- The Fast quality no longer repeats one sentence for the rest of a long
  recording.
- The faint microphone warning no longer appears for silence.
- A line that the speech engine joined to a short scrap heard minutes
  earlier now gets the time of the speech it is.
- The models' download bar no longer touches the buttons under it.
- The tray icon's status mark is no longer cut on Windows.

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
