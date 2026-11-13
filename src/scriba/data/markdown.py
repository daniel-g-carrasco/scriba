"""The transcript in Markdown (.md): a header that says what the file is
and how far to trust it, then one row per paragraph (core/paragraphs.py,
given by app) or, without one, per segment, `[mm:ss] text`, and a title
`**Speaker 2** [mm:ss]` wherever the voice changes.

The lines are the ones trascrivi.py wrote, to the character; the header
says the same things in the app's language (English in the source, the
user's through the catalogue). The warning that the transcript is automatic
is always there: a transcript read as if it were minutes is the plan's
second risk. A doubtful line (core/segments.py, flag_doubtful) keeps its
words, since dropping them could lose what was said, and is marked `(?)`
with the reason after it in italics; the header explains the mark when the
file has one.
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from scriba.i18n import _, ngettext

from . import files
from .lines import UNSPECIFIED, Cue, Line
from .timing import clock

DOUBT_MARK = "(?)"
UNKNOWN_SPEAKER = "?"       # a line with no voice in a transcript that has voices (trascrivi.py's "?")


def reasons() -> dict[str, str]:
    """The words for each reason core/segments.py gives (the same codes: a
    test holds it), in the language of the moment."""
    return {
        "no_speech": _("probably no speech"),
        "low_confidence": _("words uncertain"),
        "repeated": _("repeated many times"),
        "quiet": _("voice very faint"),
        UNSPECIFIED: _("doubtful"),
    }


@dataclass(frozen=True)
class Header:
    """What the header tells, as plain values (the app fills it)."""
    source: str                         # the recording, as a full path
    duration: float                     # seconds of audio
    language: str = ""                  # "it", "en"; "" when not known
    language_probability: float | None = None   # how sure the engine was of the language, 0..1
    model: str = ""                     # "large-v3-turbo"
    device: str = ""                    # "cpu", "cuda"
    when: datetime.datetime | None = None       # when it was transcribed
    terms: Sequence[str] = field(default_factory=tuple)     # the terms given to the engine
    speakers_engine: str = ""           # "sherpa-onnx", when voices were told apart
    named: bool = False                 # the voices carry names the user gave


def _flat(text: str) -> str:
    """One row per segment: a text on several lines is joined with spaces."""
    return " ".join(part.strip() for part in text.strip().splitlines() if part.strip())


def _voices(lines: Sequence[Line]) -> list[str]:
    return list(dict.fromkeys(line.speaker for line in lines if line.speaker and line.text.strip()))


def facts(header: Header, lines: Sequence[Line], code: bool = False) -> list[str]:
    """The header's sentences, the title first; the .txt has the same. With
    `code` the path is written as Markdown code, so that a name with an
    underscore or a star in it is not read as emphasis."""
    name = os.path.basename(header.source) or header.source
    run = [_("Duration: {duration}").format(duration=clock(header.duration))]
    if header.language:
        language = header.language
        if header.language_probability is not None:
            language += f" ({header.language_probability:.0%})"
        run.append(_("language: {language}").format(language=language))
    if header.model:
        run.append(_("model: {model} on {device}").format(model=header.model, device=header.device)
                   if header.device else _("model: {model}").format(model=header.model))
    if header.when is not None:
        run.append(f"{header.when:%d/%m/%Y %H:%M}")
    terms = ", ".join(header.terms) if header.terms else _("none")
    out = [_("Transcript: {name}").format(name=name),
           _("File: {path}").format(path=f"`{header.source}`" if code else header.source),
           " | ".join(run),
           _("Terms given: {terms}").format(terms=terms)]
    voices = _voices(lines)
    if voices:
        count = ngettext("{n} voice", "{n} voices", len(voices)).format(n=len(voices))
        if header.named:
            out.append(_("Speakers: {count} told apart automatically, with the names given by hand; the "
                         "attributions are to be checked against the audio.").format(count=count))
        else:
            engine = f" ({header.speakers_engine})" if header.speakers_engine else ""
            out.append(_("Speakers: {count} told apart automatically{engine}, anonymous labels in order of "
                         "appearance; the names are to be matched by listening. The attributions are to be "
                         "checked against the audio.").format(count=count, engine=engine))
        out.append(_("Automatic transcription: names, acronyms and numbers are to be checked against the audio."))
    else:
        out.append(_("Automatic transcription: who speaks is not told apart; names, acronyms and numbers are to be "
                     "checked against the audio."))
    if any(line.doubts for line in lines if line.text.strip()):
        out.append(_("Lines marked {mark} are doubtful (the engine heard no clear speech, was unsure of the words, "
                     "wrote the same words over and over, or the voice was very faint): check them against the "
                     "audio.").format(mark=DOUBT_MARK))
    return out


def _marked(line: Line) -> str:
    """The text, with the doubt's mark and its reasons."""
    text = _flat(line.text)
    if not line.doubts:
        return text
    words = reasons()
    why = ", ".join(words.get(code, code) for code in line.doubts)
    return f"{DOUBT_MARK} {text} _({why})_"


def row(line: Line) -> str:
    """`[mm:ss] text`, with the doubt's mark and its reasons."""
    return f"[{clock(line.start)}] {_marked(line)}"


Group = Callable[[Sequence[Line]], Sequence[Sequence[Line]]]


def body(lines: Sequence[Line], bold: bool = True, group: Group | None = None) -> list[str]:
    """The rows, with a voice's title where the voice changes and an empty
    row before every title but the first, as trascrivi.py had them.
    `group` gathers the lines into paragraphs (core/paragraphs.py: data
    does not know core, app gives it): a paragraph is one row with the time
    of its first line, and an empty row parts the paragraphs. Without it
    each line is a row of its own."""
    out: list[str] = []
    voiced = bool(_voices(lines))
    spoken = [line for line in lines if line.text.strip()]
    previous = None
    for paragraph in (group(spoken) if group else [[line] for line in spoken]):
        first = paragraph[0]
        if voiced and (first.speaker or UNKNOWN_SPEAKER) != previous:
            previous = first.speaker or UNKNOWN_SPEAKER
            title = f"**{previous}**" if bold else previous
            out += ([] if not out else [""]) + [f"{title} [{clock(first.start)}]"]
        elif group and out:
            out.append("")
        out.append(f"[{clock(first.start)}] " + " ".join(_marked(line) for line in paragraph))
    return out


def shown(lines: Iterable[Line], names: Mapping[str, str] | None) -> list[Line]:
    """The lines with each voice as it is to be shown (`names`: label ->
    shown name; core/speakers.py, display_names, gives "Parlante 2" for
    "Speaker 2" in Italian). The lines keep their labels: the subtitles of
    the same meeting still write `P2: `."""
    if not names:
        return list(lines)
    return [Cue(line.start, line.end, line.text, names.get(line.speaker, line.speaker) if line.speaker else None,
                line.doubts) for line in lines]


def render(header: Header, lines: Iterable[Line], names: Mapping[str, str] | None = None,
           group: Group | None = None) -> str:
    lines = shown(lines, names)
    title, *rest = facts(header, lines, code=True)
    return "\n".join([f"# {title}", "", *("- " + s for s in rest), "", "---", "", *body(lines, group=group)]) + "\n"


def write(folder: str | os.PathLike, stem: str, header: Header, lines: Iterable[Line],
          names: Mapping[str, str] | None = None, group: Group | None = None) -> Path:
    """The transcript in a new file `stem.md` in `folder` (or `stem_R01.md`:
    never over a file that is there); its path."""
    return files.write_new(folder, stem, ".md", render(header, lines, names, group))
