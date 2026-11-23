"""Scriba's settings and migrations for the historical JSON at the root."""

from collections.abc import Callable
from dataclasses import dataclass, field

from . import settings
from .settings import Notice as Notice, backup_file as backup_file

CONFIG_VERSION = 2
UPDATE_CHECK_INTERVAL_MINUTES = 30
MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: lambda data: {"diarization_backend": "auto", **data}}


def _check_backend(value: str) -> None:
    if value not in ("auto", "community", "legacy"):
        raise ValueError("is not a supported diarization backend")


@dataclass
class Config(settings.Fields):
    theme: str = "system"                # "system", "light", "dark"
    language: str = ""                   # "" = the desktop's; "en", "it"
    keep_running: bool = True            # keep working when the window is closed, if the desktop supports it
    models_download_paused: bool = False # a deliberate pause survives the next launch
    check_updates: bool = True           # Windows: a newer release, at start and when the window comes back
    # ISO date and time of the last successful check (updates.check_stamp;
    # a date alone, as 0.1.2 wrote it, is read as its midnight)
    last_update_check: str = ""
    last_update_offered: str = ""        # version last announced or shown in the update dialog
    last_folder: str = ""                # the folder of the last recording opened, for the next file dialog
    # the transcription's choices, remembered from one to the next
    speech_language: str = "it"          # "it", "en", "auto"
    quality: str = "turbo"               # a key of models.WHISPER: "turbo" (fast), "large" (precise)
    speakers: bool = True                # tell the voices apart
    diarization_backend: str = field(default="auto", metadata={"check": _check_backend})
    speaker_count: int = 0               # how many voices; 0: found by the engine
    subtitled_video: bool = False        # a copy of a video with the subtitles inside
    my_name: str = "Io"                 # the microphone's name
    terms: str = ""                      # names and acronyms, separated by commas
    term_files: str = ""                 # files of terms read at every transcription, one path a line, in order
    output_folder: str = ""              # "": next to the recording
    # with no output_folder, a file outside the recordings' folder: "scriba",
    # a folder of its own there with a link to the file (jobs.home_folder);
    # "beside", next to the file
    elsewhere: str = "scriba"
    device: str = ""                     # where whisper works: "" the best measured, "cpu", or a card's name
    measured_device: str = ""            # what the test of the cards chose: "" not tested yet, "cpu", a card
    # the recorder's choices
    recordings_folder: str = ""          # "": a Scriba folder in the user's Music (gui/ui.py, recordings_folder)
    record_input: bool = True            # the microphone
    record_output: bool = True           # the computer's sound (the others in a call)
    input_device: str = ""               # "": chosen by the app (core/devices.py); else the device's id
    output_device: str = ""              # the same for the output
    follow_call_mute: bool = True        # Windows 11: the microphone's speech not transcribed while muted in the call
    call_stop_minutes: int = 5          # 0: give the prompt without automatic stopping
    teams_names: bool = True             # Windows: propose names from observed Teams speaker borders

    def term_file_list(self) -> list[str]:
        """The files of terms, in their order, each once."""
        return list(dict.fromkeys(line.strip() for line in self.term_files.splitlines() if line.strip()))

    def set_term_files(self, paths) -> None:
        self.term_files = "\n".join(dict.fromkeys(str(p).strip() for p in paths if str(p).strip()))

STORE = settings.Store(Config, version=CONFIG_VERSION, migrations=MIGRATIONS)
load = STORE.load
save = STORE.save


def migrate(data: dict) -> dict:
    """Bring an older root configuration to the current format."""
    version = data.get("version", 1)
    if type(version) is not int:
        raise ValueError(f"format version {version!r}")
    return STORE.migrate(dict(data), version)
