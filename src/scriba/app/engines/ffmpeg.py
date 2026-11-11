"""ffmpeg, driven: what a recording holds, its sound as the engines want it,
and a copy of a video with the subtitles inside.

Whatever the user brings (OBS's mkv, Teams' mp4, a phone's m4a, mp3, flac,
webm) is decoded to WAV 16 kHz mono in a temporary folder (audio.py), the
one format every engine reads; the user's file is only read. The copy with
the subtitles is trascrivi.py's video_sottotitolato: every stream of the
video copied as it is (no re-encoding: seconds for an hour), the srt added
as a subtitle track with its language, in Matroska (the one container that
takes SubRip as it is), under a free name next to the transcript.

Only the first sound track is transcribed, as trascrivi.py did (OBS
writes the mix there by default), unless the file says what its tracks
are: Scriba's own recordings carry a role on each track (ROLE_TAG: the
microphone, the user, and each output of the computer), and then every
track is decoded on its own (decode's `stream`). A file that has a
picture only as cover art (an mp3 or m4a with its album image) is not a
video.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from scriba import ENV_PREFIX
from scriba.app.task import Failure, Progress, tell
from scriba.core import loudness
from scriba.data import files
from scriba.i18n import _

from . import audio, process

FFMPEG_VARIABLE = f"{ENV_PREFIX}_FFMPEG"        # another ffmpeg (ffprobe is looked for next to it)
# ISO 639-2, as Matroska wants a track's language
LANGUAGE_CODES = {"it": "ita", "en": "eng"}


# the tag of a sound track that says whose it is, in Scriba's recordings
# (app/recorder.py writes it): "me" the user's microphone, "others" the
# computer's output (the other people in a call)
ROLE_TAG = "SCRIBA_ROLE"
ROLES = ("me", "others")


@dataclass(frozen=True)
class Track:
    index: int              # among the sound tracks: 0 is the first (ffmpeg's -map 0:a:<index>)
    title: str = ""
    role: str = ""          # one of ROLES, or "" when the file does not say


@dataclass(frozen=True)
class Media:
    duration: float         # seconds; 0 when the file does not say
    sound_tracks: int
    video: bool             # a picture that moves (cover art does not count)
    subtitle_tracks: int
    tracks: tuple[Track, ...] = ()      # the sound tracks, in order

    @property
    def roles(self) -> tuple[Track, ...]:
        """The tracks, when every one says whose it is (a recording of
        Scriba's); () for any other file."""
        if self.tracks and all(t.role in ROLES for t in self.tracks):
            return self.tracks
        return ()


def find(program: str = "ffmpeg") -> Path | None:
    """ffmpeg or ffprobe: the frozen build's own; from the sources next to
    the variable's ffmpeg, the one built in the checkout, or on the PATH
    (bundle.py)."""
    from . import bundle
    return bundle.program(program, FFMPEG_VARIABLE, sibling_of="ffmpeg" if program != "ffmpeg" else "")


def _need(program: str) -> Path:
    path = find(program)
    if path is None:
        raise Failure(_("{program} is missing: Scriba needs it to read audio and video files.").format(
            program=program), f"no {program} on the PATH")
    return path


need = _need        # for the recorder's finishing (app/recording.py)


def decoded_seconds(source: str | os.PathLike, stream: int = 0, cancelled: threading.Event | None = None) -> float:
    """How long the sound track really is, decoding it all (fast for FLAC):
    a file cut short by a crash has no length in its header. Failure when
    it cannot be decoded."""
    reached = [0.0]

    def heard(line: str) -> None:
        seconds = progress_seconds(line)
        if seconds is not None:
            reached[0] = max(reached[0], seconds)

    try:
        done = process.run([str(_need("ffmpeg")), "-nostdin", "-hide_banner", "-loglevel", "error", "-nostats",
                            "-progress", "pipe:1", "-i", os.fspath(source), "-map", f"0:a:{stream}", "-f", "null",
                            "-"], on_stdout=heard, cancelled=cancelled)
    except OSError as e:
        raise Failure(_("ffmpeg could not be started."), str(e)) from e
    if done.returncode != 0:
        raise Failure(_("ffmpeg could not read the sound of {name}.").format(name=Path(source).name), done.detail)
    return reached[0]


def parse_probe(text: str) -> Media:
    """What ffprobe's JSON (-show_entries format=duration:stream=codec_type
    :stream_disposition=attached_pic) says about the file."""
    data = json.loads(text or "{}")
    streams = data.get("streams") or []
    kinds = [(s.get("codec_type"), (s.get("disposition") or {}).get("attached_pic", 0)) for s in streams]
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    tracks = []
    for stream in streams:
        if stream.get("codec_type") != "audio":
            continue
        # Matroska's tags come back in capitals, the others as written
        tags = {str(k).upper(): str(v) for k, v in (stream.get("tags") or {}).items()}
        role = tags.get(ROLE_TAG, "").lower()
        tracks.append(Track(len(tracks), tags.get("TITLE", ""), role if role in ROLES else ""))
    return Media(duration=duration, sound_tracks=len(tracks),
                 video=any(k == "video" and not p for k, p in kinds),
                 subtitle_tracks=sum(1 for k, _p in kinds if k == "subtitle"), tracks=tuple(tracks))


def probe(source: str | os.PathLike, cancelled: threading.Event | None = None) -> Media:
    """What the file holds. Failure when ffprobe cannot read it."""
    lines: list[str] = []
    try:
        done = process.run([str(_need("ffprobe")), "-v", "error", "-show_entries",
                            "format=duration:stream=codec_type:stream_disposition=attached_pic:stream_tags", "-of", "json",
                            os.fspath(source)], on_stdout=lines.append, cancelled=cancelled)
    except OSError as e:
        raise Failure(_("ffprobe could not be started."), str(e)) from e
    if done.returncode != 0:
        raise Failure(_("{name} cannot be read as audio or video.").format(name=Path(source).name), done.detail)
    try:
        return parse_probe("\n".join(lines))
    except ValueError as e:
        raise Failure(_("{name} cannot be read as audio or video.").format(name=Path(source).name), str(e)) from e


def decode_arguments(ffmpeg: str, source: str, dest: str, start: float = 0.0, length: float = 0.0,
                     stream: int = 0) -> list[str]:
    """`start` and `length` (seconds; 0: from the beginning, to the end)
    cut a piece: the test of the graphics cards wants 30 s of an hour, and
    -ss before -i seeks without decoding what comes before. `stream` is
    the sound track, 0 the first."""
    cut = ["-ss", f"{start:.3f}"] if start > 0 else []
    until = ["-t", f"{length:.3f}"] if length > 0 else []
    return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
            *cut, "-i", source, *until, "-map", f"0:a:{stream}", "-vn", "-ac", "1", "-ar", str(audio.RATE),
            "-c:a", "pcm_s16le", "-f", "wav", "-y", dest]


def progress_seconds(line: str) -> float | None:
    """The time reached, from a line of ffmpeg's -progress ("out_time_us=",
    also written "out_time_ms=" with microseconds all the same)."""
    key, _sep, value = line.partition("=")
    if key in ("out_time_us", "out_time_ms") and value.strip().lstrip("-").isdigit():
        return max(int(value) / 1e6, 0.0)
    return None


def decode(source: str | os.PathLike, dest: str | os.PathLike, duration: float = 0.0,
           progress: Progress | None = None, cancelled: threading.Event | None = None,
           start: float = 0.0, length: float = 0.0, stream: int = 0) -> Path:
    """The sound track `stream` (0: the first) of `source` as WAV 16 kHz
    mono in `dest` (a file in a temporary folder of the caller's); with
    `start` and `length` only that piece of it."""
    name = Path(source).name

    def heard(line: str) -> None:
        reached = progress_seconds(line)
        if reached is not None and duration > 0:
            tell(progress, min(reached, duration), duration)

    try:
        done = process.run(decode_arguments(str(_need("ffmpeg")), os.fspath(source), os.fspath(dest), start, length,
                                            stream), on_stdout=heard, cancelled=cancelled)
    except OSError as e:
        raise Failure(_("ffmpeg could not be started."), str(e)) from e
    if done.returncode != 0 or not Path(dest).is_file():
        if any("matches no streams" in line for line in done.stderr_tail):
            raise Failure(_("{name} has no sound track.").format(name=name), done.detail)
        raise Failure(_("ffmpeg could not read the sound of {name}.").format(name=name), done.detail)
    return Path(dest)


def loudness_arguments(ffmpeg: str, source: str, stream: int = 0) -> list[str]:
    """EBU R128's measure (ebur128, the true peak too): nothing written."""
    return [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", source, "-map", f"0:a:{stream}", "-vn",
            "-af", "ebur128=peak=true", "-f", "null", "-"]


def measure_loudness(source: str | os.PathLike, stream: int = 0,
                     cancelled: threading.Event | None = None) -> loudness.Loudness:
    """How loud the sound track `stream` of `source` is (EBU R128, at the
    file's own rate). Failure when ffmpeg cannot measure it."""
    lines: list[str] = []
    try:
        done = process.run(loudness_arguments(str(_need("ffmpeg")), os.fspath(source), stream),
                           on_stderr=lines.append, cancelled=cancelled)
    except OSError as e:
        raise Failure(_("ffmpeg could not be started."), str(e)) from e
    try:
        if done.returncode != 0:
            raise ValueError(done.detail)
        return loudness.parse_ebur128("\n".join(lines[-40:]))
    except (ValueError, KeyError) as e:
        raise Failure(_("ffmpeg could not measure the loudness of {name}.").format(name=Path(source).name),
                      str(e)) from e


def subtitle_arguments(ffmpeg: str, video: str, srt: str, dest: str, subtitle_index: int, language: str,
                       title: str) -> list[str]:
    """trascrivi.py's command: every stream copied, the srt added as the
    track after the subtitles the video already has."""
    track = f"-metadata:s:s:{subtitle_index}"
    return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", video, "-i", srt,
            "-map", "0", "-map", "1", "-c", "copy", "-c:s", "srt",
            track, f"language={LANGUAGE_CODES.get(language, 'und')}", track, f"title={title}",
            "-f", "matroska", "-y", dest]


def subtitled_copy(video: str | os.PathLike, srt: str | os.PathLike, folder: str | os.PathLike, stem: str,
                   language: str, media: Media, cancelled: threading.Event | None = None) -> Path:
    """A copy of `video` with the subtitles inside, as `stem.mkv` in
    `folder` (or `stem_R01.mkv`: never over a file that is there); its
    path. ffmpeg writes to a temporary file of that folder, which takes
    its name only when ffmpeg is done: a cancelled or failed copy leaves
    nothing behind."""
    tmp = files.temporary_name(folder, ".mkv")
    try:
        done = process.run(subtitle_arguments(str(_need("ffmpeg")), os.fspath(video), os.fspath(srt), str(tmp),
                                              media.subtitle_tracks, language, _("Transcript")),
                           cancelled=cancelled)
        if done.returncode != 0:
            raise Failure(_("ffmpeg could not make the video with the subtitles."), done.detail)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise Failure(_("ffmpeg could not be started."), str(e)) from e
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return files.claim(tmp, folder, stem, ".mkv")
