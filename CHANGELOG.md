# Changelog

What changed in Scriba, version by version, the newest first. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
are `0.x` while the app evolves. Stable Windows and Linux packages are
available from GitHub Releases. Linux Flatpak builds and runtime checks use GNOME Platform 50. The app's metainfo carries a shorter description.

## [0.1.14] - 2026-11-24

### Added
- List, open, search and name speakers in saved meetings from the command line, with JSON output for other tools.
- The highest participant count seen in Teams, including your own tile, is saved and suggested in Transcribe.
- The recording keeps minute-by-minute Teams reading counts. The Names from Teams row reports when no speaker border has been recognised for five minutes.
- A name read in one part of a Teams call can be proposed for the whole voice and for matching voices heard elsewhere. The result shows where each proposal came from.

### Changed
- Choose the recording cut on one timeline at stop. Transcribe shows the saved selection and lets you edit it from the complete recording.
- Saving speaker names updates the transcript in place. Saving the listening balance updates the same listening copy.
- The listening balance preview plays up to 25 seconds, favouring a clear exchange between you and the other speakers.
- Recordings and transcripts share one meeting folder. Scriba adds a known title when recording stops and updates the folder when you edit the title.
- Recording shows short audio warnings at the top. Echo removal details appear with the results.
- When the call ends, the visible window shows a stop countdown. The Windows notification also offers Stop and Continue when Scriba is in the tray.

### Fixed
- Fast speech is less likely to be marked doubtful just because its transcript has a lot of text.
- Microphone replies stay on their own side of a long gap in the recording.
- Editing your microphone voice's name saves it in Preferences when you still use the default name. The confirmation offers Undo.
- Call mute silences the microphone in listening copies and transcripts. Echo removal and its percentage use only unmuted time. The original track retains the sound.
- Speaker names are read from Teams on high-resolution screens, where the speaking border is one pixel thin.

## [0.1.13] - 2026-11-23

### Added
- When a call ends (a Teams meeting, or a call program that releases the
  microphone), Scriba offers to stop recording, and stops by itself after
  five minutes without an answer. The wait can be changed in Preferences;
  0 turns the automatic stop off. A meeting in person never stops by
  itself.
- After stopping, choose **Only the Call** or **Whole Recording**
  before echo removal and mixing. Without an answer, after two minutes
  Scriba keeps the call; the complete recording is kept in Previous
  revisions.
- Trim either end of a recording without re-encoding, with the original
  kept in Previous revisions.
- Recordings interrupted by a crash, a power cut or a forced exit are
  offered for completion when Scriba opens again, resuming from the step
  they reached.
- **Skip Finishing** leaves sooner without losing anything; the listening
  copy can be created later from the Transcribe tab.

### Changed
- After stopping, one progress bar shows the estimated time left and the
  finish time, also in the notification area and in the quit dialog.
- Saving a recording is faster: echo is looked for and removed only where
  the microphone and the outputs are active.

### Fixed
- After a call, a microphone opened by the call program (for example a
  webcam's) no longer replaces the one that was recording.

## [0.1.12] - 2026-11-22

The 0.1.11 tag was never published as a release: its builds stopped on a
missing translation. 0.1.12 carries the same changes.

### Fixed
- When one output passes into another (on Linux an effects program such as
  EasyEffects sends its sound on to the speakers; elsewhere a loopback or a
  virtual cable), Scriba recorded the same voices on two tracks: every line
  was transcribed twice and the voices were split into too many speakers.
  Scriba now recognises tracks that carry the same sound, transcribes one
  of them and leaves the copy out of the listening copy, and says so. The
  speaker count you choose applies again. Earlier recordings are corrected
  when they are transcribed again.
- On Linux, recording no longer forces the rest of the system's audio into
  very short blocks: effects that could not keep up crackled while windows
  moved.

## [0.1.10] - 2026-11-20

### Changed
- Opening Scriba shows a notification inside the window and a Windows
  notification when a new version is available. Click either notification
  or the update banner to open the update dialog.

### Fixed
- Windows dialogs display a single border above their controls.

## [0.1.8] - 2026-11-19

### Added
- While making the listening copy, Scriba shows the current track and how
  much audio it has processed.
- When Teams observations are enabled, Scriba can show a notice when a
  previously detected meeting appears to end.
- Strong, unambiguous Teams matches can name voices in the first transcript.
- Scriba proposes an editable meeting title from Teams or the transcript.
  New exports use the date and title, including their folder when Scriba
  chooses the destination automatically.

### Changed
- The balance slider changes the listening preview during playback. The
  play and stop button keeps the same width.

### Fixed
- At the end of a recording, intervals muted in the call or switched off in Scriba
  are grouped in one notice per track and reason.
- Recording banners and other interface text display apostrophes, ampersands and
  angle brackets correctly, including in device names and paths.
- Tracks that run far past the recording clock are shortened in the joined
  recording and its listening copy. Scriba keeps the original tracks separately.
- Choosing My Voice can save your name in Preferences from the result page.
- The Windows installer uses the Scriba icon in its header.

## [0.1.7] - 2026-11-18

### Changed
- Scriba opens the update dialog once per new version, after any active work ends.
  Choose Later to keep the update available in the banner.

### Fixed
- If GitHub limits update requests, Scriba checks the release page and still
  verifies the installer's checksum and signature before running it.
- On Windows, opening or restoring the window checks for updates after 30 minutes.
  A busy Scriba runs the check when work ends and records the time only on success.
- Manual update checks work during graphics card testing and preview playback.
  When a check has to wait, Scriba explains why and shows the result once it runs.
- Meetings reopen when a transcript line has reversed times. Scriba repairs the
  line, marks it as doubtful and explains the repair in the Result page.

## [0.1.6] - 2026-11-17

### Added
- More accurate voice recognition with community-1. The four voice models download
  automatically on first start and when upgrading, about 33 MB in total.
- Voice processing uses the graphics card through Vulkan when available, with
  automatic processor fallback. The previous voice method remains available.
- Editable proposals distinguish several voices behind the same Teams participant
  tile and suggest a shared name for very similar voices. Older meetings reopen offline.

## [0.1.5] - 2026-11-17

### Added
- On Windows, Names from Teams observes speaker borders during recording
  and proposes editable names for the transcript's voices, with the share
  of speech that matched. Names enter exported files with Save with the Names.
  The recording page shows when Teams cannot be read.

### Changed
- A new icon, in the style now shared by the apps of the same family (Meridiana, Lanterna...): the gold nib with its red neck, cut on its axis
  with the light on the left and the shadow on the right, on an ivory disc with a little thickness below it. The
  tray's states keep their marks on the new icon; the symbolic icons stay as they were. Windows gets a drawing of
  its own for 16-24 px, to the edge of its square.

### Fixed
- The Result heading keeps its icon aligned with the title. Successful echo
  removal and speech excluded while muted are collected in collapsed Recording
  Notes; warnings that need checking stay visible.
- The listening balance moves in small, equal steps with the wheel and arrow
  keys. Scrolling over it changes the balance only while the slider has focus.
- Voice name fields no longer suggest previously used names. Editable proposals
  from Teams are still shown.
- A microphone muted in the call is clearly marked below the recording
  timer and beside the microphone. Its meters stay at zero and dim until
  it is unmuted; the computer sound meters continue normally.

## [0.1.4] - 2026-11-16

### Changed
- The update window is laid out as GNOME's own: "Later" and "Install
  Update" at the top, the app's icon with the new version, the one in use
  and the size of the download, what is new in rows under its headings
  (in the app's language), the download as a progress bar and a failure
  in a bar at the top, from where it can be tried again (Windows).
- The notes of each release are this changelog's entry for the version,
  so the update window shows what really changed instead of a single line
  with a link.

### Fixed
- The app's icon is drawn again where the app shows it at a size of its
  own, as at the top of the update window: there it came out empty.

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
