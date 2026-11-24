# Meeting commands

Use `scriba-cli.exe` on Windows, or
`flatpak run --command=scriba io.github.daniel_g_carrasco.scriba` with the Flatpak.
In a checkout, use `python bin/scriba`.

| Command | Arguments |
|---|---|
| `list` | `--since YYYY-MM-DD` filters the recording date inclusively. |
| `show MEETING` | `--format md\|txt\|srt\|json` selects the output. The default is Markdown. |
| `voices MEETING` | Displays speaker labels, saved names, speaking time and Teams proposals. |
| `search TEXT` | `--meeting MEETING` limits a case-insensitive substring search. |
| `names MEETING` | Repeat `--set "Speaker 1=Ada"` and `--clear "Speaker 2"` as needed. `--me "Speaker 1"` sets your voice. Stable `Speaker N` and Italian `Parlante N` labels both work. |

Pass a meeting folder, meeting JSON, recording MKA, date, or unique part
of the title as `MEETING`. If several meetings match, the command lists the
candidates on standard error. Use `--folder` to override the recordings folder
from Preferences.

The meeting commands use saved files and MKA metadata. They run without a
window or speech models. `names` rewrites the existing transcript formats,
using the saved original labels. Giving your voice a name also saves it in
Preferences when the default name is still in use. An unfinished edit in the
window prevents `names` from changing that meeting.

## JSON schema, version 1

`--json` prints one object on standard output. `show --format json` has the same
output as `show --json`. Every object has integer `schema: 1`. Dates are local
ISO 8601 strings. Durations and line times are seconds unless the field says
`minutes`. The file paths are absolute. Optional scalar values are `null`.

| Command | Other top-level fields |
|---|---|
| `list` | `meetings`, an array of meeting objects in ascending date order. |
| `show` | `meeting` and `lines`, an array of line objects in transcript order. |
| `voices` | `voices`, an array of voice objects. |
| `search` | `matches`, an array of line objects with an additional `meeting` field. |
| `names` | `meeting`, `names` (map of stable labels to strings), and `me` (stable label or empty string). |

- A meeting has `date`, `title`, `duration`, `transcribed` (boolean), `voices`
  (integer), `names` (map), and `path` (the meeting folder).
- A line has `start`, `end`, `speaker` (stable label or `null`), `name` (saved
  name or `null`), `text`, and `doubts` (array of diagnostic keys).
- A voice has `label`, `name`, `me` (boolean), `lines` (integer), `first`,
  `minutes`, and `proposal`. A proposal has `name`, `origin` (`teams` or
  `voice`), `heard_seconds`, and `coverage` (fraction of the voice heard in
  the Teams observation). A voice match has zero observed time and coverage.

Exit status 0 is success. Exit status 1 is a meeting or file error. Exit
status 2 is a usage error. Errors go to standard error and have no JSON body.
