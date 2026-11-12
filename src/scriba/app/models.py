"""The models Scriba needs: which, where they live, and how they arrive.

Three files do the work: whisper's weights in
ggml (large-v3-turbo by default, large-v3 as the "precise" choice),
Silero's speech filter and WeSpeaker's voice embedder, both in ONNX. They
are downloaded once into the user's cache (paths.py: they can always be
downloaded again) and never leave the computer; a developer, or a test,
points SCRIBA_MODELS_DIR at a folder that has them already.

A download is the one network traffic of the app besides the update check
(the plan: privacy), and nothing that arrives is trusted before it is
checked. Each address is fixed to a revision of the publisher's own
repository (a commit on Hugging Face, a tag on GitHub), its size and
SHA-256 are written here (measured on 11/11/2026: the files served are
those the engine tests ran on), a redirect is followed only to the hosts
that publisher serves from (Hugging Face sends every file to its CDN,
*.hf.co), nothing is read past the declared size, and the file takes its
name (os.replace) only once its hash is right. Until then it is
`<name>.part`: a download that was cut or cancelled resumes from there
with an HTTP Range request, or starts again from nothing when the server
answers with the whole file.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from scriba import ENV_PREFIX, EXE_NAME, __version__
from scriba.i18n import _

from .task import Cancelled, Failure, Progress, check, tell

MODELS_DIR_VARIABLE = f"{ENV_PREFIX}_MODELS_DIR"
PART_SUFFIX = ".part"
CHUNK = 1 << 20
TIMEOUT = 30.0              # seconds a server may stay silent before the download counts as cut
SPACE_MARGIN = 64 * 2**20   # left free on the disk past the model, so the download never fills it

# Hugging Face's revision of ggerganov/whisper.cpp and of Wespeaker's own
# repository the hashes below were read from (11/11/2026)
_WHISPER_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
_WESPEAKER_REVISION = "f0c48c298fd835726c27956a5d617bad7115627e"
_HUGGING_FACE = ("huggingface.co", ".hf.co", ".huggingface.co")


class ModelError(Failure):
    """A model could not be had: missing, cut short, not the one declared."""


@dataclass(frozen=True)
class Model:
    key: str                # the catalogue's name for it
    filename: str
    url: str                # the publisher's own file, at a fixed revision
    sha256: str
    size: int               # bytes, exactly: also the most a download reads
    licence: str            # SPDX identifier of the weights' licence
    publisher: str          # whose it is, for the credits
    page: str               # where the publisher describes it
    hosts: tuple[str, ...]  # where the download may go; ".x.y" stands for any host under x.y


WHISPER_TURBO = Model(
    "whisper-large-v3-turbo", "ggml-large-v3-turbo-q5_0.bin",
    f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{_WHISPER_REVISION}/ggml-large-v3-turbo-q5_0.bin",
    "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2", 574_041_195, "MIT",
    "OpenAI (weights), ggml conversion by the whisper.cpp project", "https://huggingface.co/ggerganov/whisper.cpp",
    _HUGGING_FACE)
WHISPER_LARGE = Model(
    "whisper-large-v3", "ggml-large-v3-q5_0.bin",
    f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{_WHISPER_REVISION}/ggml-large-v3-q5_0.bin",
    "d75795ecff3f83b5faa89d1900604ad8c780abd5739fae406de19f23ecd98ad1", 1_081_140_203, "MIT",
    "OpenAI (weights), ggml conversion by the whisper.cpp project", "https://huggingface.co/ggerganov/whisper.cpp",
    _HUGGING_FACE)
SILERO_VAD = Model(
    "silero-vad", "silero_vad_16k_sequence.onnx",
    "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.3/src/silero_vad/data/silero_vad_16k_sequence.onnx",
    "9ccdacc4719d8aa7e45a77536bfabec45a03ba1f2fad5e241ab4060b24238a85", 1_246_165, "MIT",
    "Silero Team", "https://github.com/snakers4/silero-vad", ("raw.githubusercontent.com",))
WESPEAKER = Model(
    "wespeaker-resnet34-lm", "wespeaker_voxceleb_resnet34_LM.onnx",
    f"https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/{_WESPEAKER_REVISION}/"
    "voxceleb_resnet34_LM.onnx",
    "7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068", 26_530_309, "CC-BY-4.0",
    "WeSpeaker (trained on VoxCeleb)", "https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM",
    _HUGGING_FACE)

CATALOGUE = (WHISPER_TURBO, WHISPER_LARGE, SILERO_VAD, WESPEAKER)
# the transcription model by the name the command line and the settings use
WHISPER = {"turbo": WHISPER_TURBO, "large": WHISPER_LARGE}


def purpose(model: Model) -> str:
    """What the model is for, in the language of the moment."""
    return {
        WHISPER_TURBO.key: _("Speech to text, fast (whisper large-v3-turbo)"),
        WHISPER_LARGE.key: _("Speech to text, precise and slower (whisper large-v3)"),
        SILERO_VAD.key: _("Identifies speech segments in the recording (Silero)"),
        WESPEAKER.key: _("Tells the voices apart (WeSpeaker)"),
    }.get(model.key, model.key)


def megabytes(size: float) -> str:
    return _("{n} MB").format(n=f"{size / 1e6:.0f}" if size >= 1e7 else f"{size / 1e6:.1f}")


def by_key(key: str) -> Model:
    for model in CATALOGUE:
        if model.key == key:
            return model
    raise KeyError(key)


def folder(environ=None) -> Path:
    """Where the models are: SCRIBA_MODELS_DIR when given (a folder that
    has them already: the tests, a developer's machine), else the user's
    cache."""
    environ = os.environ if environ is None else environ
    given = environ.get(MODELS_DIR_VARIABLE)
    if given:
        return Path(given)
    from .services import app_paths
    return app_paths(environ).cache_folder / "models"


def path_of(model: Model, where: str | os.PathLike | None = None) -> Path:
    return (folder() if where is None else Path(where)) / model.filename


def present(model: Model, where: str | os.PathLike | None = None) -> bool:
    """The file is there with its size. Quick (a model weighs up to 1 GB):
    the hash was checked when it took its name, and verify() checks it
    again when asked."""
    path = path_of(model, where)
    try:
        return path.is_file() and path.stat().st_size == model.size
    except OSError:
        return False


def missing(models, where: str | os.PathLike | None = None) -> list[Model]:
    """Those of `models` that are not there, in their order."""
    return [model for model in models if not present(model, where)]


def _hash_file(path: Path, digest, cancelled: threading.Event | None, progress: Progress | None = None,
               total: int = 0) -> int:
    done = 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            check(cancelled)
            digest.update(chunk)
            done += len(chunk)
            tell(progress, done, total)
    return done


def verify(model: Model, where: str | os.PathLike | None = None, progress: Progress | None = None,
           cancelled: threading.Event | None = None) -> bool:
    """The file is there and is the one declared (its SHA-256)."""
    if not present(model, where):
        return False
    digest = hashlib.sha256()
    _hash_file(path_of(model, where), digest, cancelled, progress, model.size)
    return digest.hexdigest() == model.sha256


def allowed(model: Model, url: str) -> bool:
    """Whether a download of `model` may read from `url`: one of its hosts,
    and the scheme of its own address (an https download never goes on in
    clear)."""
    from urllib.parse import urlsplit
    parts, own = urlsplit(url), urlsplit(model.url)
    host = (parts.hostname or "").lower()
    if parts.scheme != own.scheme or not host:
        return False
    return any(host == h or (h.startswith(".") and host.endswith(h)) for h in model.hosts)


def _opener(model: Model):
    """urllib's opener, whose redirects stay on the model's hosts."""
    import urllib.request

    from .updates import ssl_context

    class Guarded(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not allowed(model, newurl):
                from urllib.parse import urlsplit
                raise ModelError(_("The download of {name} was sent to another site ({host}): refused.").format(
                    name=model.filename, host=urlsplit(newurl).hostname or newurl), f"redirect {code} to {newurl}")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(Guarded, urllib.request.HTTPSHandler(context=ssl_context()))


def _range_start(response) -> int | None:
    """Where a partial answer starts (Content-Range: bytes 100-199/200)."""
    value = response.headers.get("Content-Range") or ""
    try:
        unit, _sep, rest = value.partition(" ")
        return int(rest.split("-", 1)[0]) if unit == "bytes" else None
    except ValueError:
        return None


def _cut(model: Model, done: int, detail: str) -> ModelError:
    return ModelError(_("The download of {name} stopped at {done} of {total}: try again, it goes on from there.").format(
        name=model.filename, done=megabytes(done), total=megabytes(model.size)), detail)


def _phase(progress, name):
    callback = getattr(progress, "phase", None)
    if callable(callback):
        callback(name)


def download(model: Model, where: str | os.PathLike | None = None, progress: Progress | None = None,
             cancelled: threading.Event | None = None, opener=None) -> Path:
    """The model in `where` (the models' folder), checked; its path. A file
    that is there and right is not downloaded again. ModelError, with a
    sentence to show, for a download that was cut (the part is kept and the
    next call resumes it), refused (another site, more than declared, not
    the file declared: the part is thrown away) or that has no room on the
    disk; Cancelled when `cancelled` is set (the part is kept)."""
    import http.client
    import urllib.error
    import urllib.request

    where = folder() if where is None else Path(where)
    where.mkdir(parents=True, exist_ok=True)
    final = where / model.filename
    _phase(progress, "checking")
    if verify(model, where, progress=progress, cancelled=cancelled):
        return final
    if not allowed(model, model.url):
        raise ModelError(_("The address of {name} is not one of its publisher's: refused.").format(
            name=model.filename), model.url)
    part = final.with_name(final.name + PART_SUFFIX)
    have = part.stat().st_size if part.is_file() else 0
    if have > model.size:
        part.unlink()
        have = 0
    free = shutil.disk_usage(where).free
    if free < model.size - have + SPACE_MARGIN:
        raise ModelError(_("There is not enough room on the disk for {name}: it needs {needed}, {free} are free.").format(
            name=model.filename, needed=megabytes(model.size - have + SPACE_MARGIN), free=megabytes(free)),
            str(where))
    digest = hashlib.sha256()
    if have:
        _phase(progress, "checking")
        _hash_file(part, digest, cancelled, progress, model.size)
    _phase(progress, "connecting")
    headers = {"User-Agent": f"{EXE_NAME}/{__version__}"}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(model.url, headers=headers)
    done = have
    try:
        with (opener or _opener(model)).open(request, timeout=TIMEOUT) as response:
            if have and response.status == 206 and _range_start(response) == have:
                mode = "ab"
            elif response.status == 200:
                mode, have, done, digest = "wb", 0, 0, hashlib.sha256()     # the server starts again: so do we
            else:
                raise ModelError(_("The server answered in a way that cannot be read ({code}).").format(
                    code=response.status), f"{response.status} {response.headers.get('Content-Range')}")
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and have + int(length) > model.size:
                part.unlink(missing_ok=True)
                raise _too_big(model, f"Content-Length {length} from {have}")
            with open(part, mode) as out:
                while True:
                    check(cancelled)
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    if done + len(chunk) > model.size:
                        out.close()
                        part.unlink(missing_ok=True)
                        raise _too_big(model, f"more than {model.size} bytes")
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    tell(progress, done, model.size)
                out.flush()
                os.fsync(out.fileno())
    except urllib.error.HTTPError as e:
        if e.code == 416:           # the part is not a beginning the server knows: start again next time
            part.unlink(missing_ok=True)
        raise ModelError(_("The download of {name} failed: the server answered {code}.").format(
            name=model.filename, code=e.code), str(e)) from None
    except (Cancelled, ModelError):
        raise
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        raise _cut(model, done, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None
    if done < model.size:
        raise _cut(model, done, f"{done} of {model.size} bytes")
    _phase(progress, "verifying")
    if digest.hexdigest() != model.sha256:
        part.unlink(missing_ok=True)
        raise ModelError(_("The file downloaded is not {name} as published (its checksum differs): thrown away.").format(
            name=model.filename), f"sha256 {digest.hexdigest()}")
    os.replace(part, final)
    return final


def _too_big(model: Model, detail: str) -> ModelError:
    return ModelError(_("The server sends more than {name} weighs ({size}): refused.").format(
        name=model.filename, size=megabytes(model.size)), detail)
