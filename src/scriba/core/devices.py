"""The sound devices of the computer, and which ones a meeting is recorded
from when the user leaves the choice to the app.

The system's default may point to silent speakers while a call plays in
Bluetooth earphones, or to a distant webcam microphone instead of the
headset's. What tells the call's devices apart is elsewhere, and
the platform gives it (app/winaudio.py on Windows): which program has a
device open (Teams has the headset's microphone and speaker), which
devices belong to one physical device (the headset's microphone and its
earphones share a container), the default *for communications* (Windows
keeps one apart from the multimedia default, and a headset usually is
it), and where there is sound now.

So the output is chosen by: a call program playing on it, sound on it
now, the communications default, the default. Since a wrong output loses
the others' voices for good, and a silent track costs nothing, the
automatic mode records every output anyway, each on a track of its own,
and keeps those that had sound (core/levels.py, kept); the choice says
which one is shown as "the call's". The input is chosen by: a call
program capturing it, being part of the chosen output's device (the
headset), the communications default, the default, and the voice heard
louder there than anywhere else while the page is open. One input only
is recorded: two microphones would write the user twice. A call program
that opens another microphone once the recording has started (the call
began after Record was pressed) is followed there (Follow): the recording
goes on from that one, on a new track.

Plain data in, plain data out: the platform's module fills the Device
records, the window shows the reasons.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

INPUT, OUTPUT = "input", "output"

# Programs that make calls: a device they have open is the call's. A browser
# may be a call (Meet, Teams on the web) or a video: it counts, for less.
CALL_PROGRAMS = frozenset((
    "ms-teams", "teams", "msteams", "zoom", "cpthost", "webex", "ciscocollabhost", "atmgr", "skype", "lync",
    "slack", "discord", "whatsapp", "telegram", "signal", "3cxdesktopapp", "gotomeeting", "g2mcomm", "jitsi",
    "element", "mattermost", "ringcentral", "facetime", "linphone", "zoiper", "microsip",
))
BROWSERS = frozenset(("msedge", "chrome", "firefox", "brave", "opera", "vivaldi", "chromium", "iexplore", "safari"))

# what each sign is worth in the choice; a call program outweighs all the
# others together (an input's: same device 4, voice 3, communications 2,
# default 1, a browser 4 besides: 14), the user's own voice heard there
# outweighs the defaults. At 8 a laptop's own microphone, of the same device
# as the loudspeakers the call plays on, default for both roles and with the
# user's voice loudest there (4 + 2 + 1 + 3 = 10), beat the webcam's
# microphone Teams had open.
CALL, BROWSER, OTHER_PROGRAM = 16, 4, 1
SAME_DEVICE = 4
SOUND_NOW = 4
VOICE_LOUDEST = 3
COMMUNICATIONS, DEFAULT = 2, 1

SOUND_ABOVE = -60.0         # dBFS of peak: an output where this was reached lately plays something
VOICE_ABOVE = -50.0         # dBFS of RMS: an input that hears a voice
VOICE_MARGIN = 10.0         # dB louder than every other input: that is where the user speaks
NO_CONTAINER = ("", "00000000-0000-0000-ffff-ffffffffffff")     # Windows' "belongs to no device"

# the reasons a device was chosen, as codes; the window says them in words
R_CALL, R_BROWSER, R_PROGRAM = "call", "browser", "program"
R_SAME_DEVICE, R_SOUND, R_VOICE = "same-device", "sound", "voice"
R_COMMUNICATIONS, R_DEFAULT, R_ONLY = "communications", "default", "only"


@dataclass(frozen=True)
class Device:
    id: str                             # the platform's own (WASAPI endpoint id, PulseAudio name)
    name: str                           # as the system shows it: "Cuffie (Headset BT)"
    kind: str                           # INPUT, or OUTPUT (recorded through its loopback, or its monitor)
    default: bool = False               # the system's default for this kind
    communications: bool = False        # Windows' default for communications (the same as default elsewhere)
    container: str = ""                 # the physical device it belongs to; "": not known
    bluetooth: bool = False
    programs: tuple[str, ...] = ()      # the programs that have it open now, as executable names, ours left out
    peak: float | None = None           # dBFS the system's meter gives now (Windows); None: not known


@dataclass(frozen=True)
class Choice:
    device: Device | None
    reasons: tuple[str, ...] = ()


def program_name(path: str) -> str:
    """"C:\\...\\ms-teams.exe" -> "ms-teams": the executable's name, as
    CALL_PROGRAMS has it."""
    name = re.split(r"[\\/]", path.strip())[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def product(name: str) -> str:
    """The physical device in a name as Windows writes it: "Cuffie (Headset
    BT)" -> "headset bt"; "" when the name has none. Two endpoints with
    the same product are one device when the system gives no container."""
    found = re.findall(r"\(([^()]*)\)", name)
    return found[-1].strip().casefold() if found else ""


def same_device(a: Device, b: Device) -> bool:
    if a.container not in NO_CONTAINER and b.container not in NO_CONTAINER:
        return a.container == b.container
    return bool(product(a.name)) and product(a.name) == product(b.name)


def _programs(device: Device) -> list[tuple[int, str]]:
    found = []
    for program in device.programs:
        name = program_name(program)
        if name in CALL_PROGRAMS:
            found.append((CALL, R_CALL))
        elif name in BROWSERS:
            found.append((BROWSER, R_BROWSER))
        else:
            found.append((OTHER_PROGRAM, R_PROGRAM))
    return found


def _score(device: Device, signs: list[tuple[int, str]]) -> tuple[int, tuple[str, ...]]:
    if device.communications:
        signs.append((COMMUNICATIONS, R_COMMUNICATIONS))
    if device.default:
        signs.append((DEFAULT, R_DEFAULT))
    best: dict[str, int] = {}
    for worth, reason in signs:
        best[reason] = max(best.get(reason, 0), worth)
    ordered = sorted(best.items(), key=lambda item: -item[1])
    return sum(best.values()), tuple(reason for reason, _worth in ordered)


def _best(scored: list[tuple[int, tuple[str, ...], int, Device]]) -> Choice:
    if not scored:
        return Choice(None)
    if len(scored) == 1:
        score, reasons, _n, device = scored[0]
        return Choice(device, reasons or (R_ONLY,))
    # the highest score; between equals, the order the system lists them in
    score, reasons, _n, device = max(scored, key=lambda s: (s[0], -s[2]))
    return Choice(device, reasons)


def choose_output(devices: Iterable[Device], peaks: Mapping[str, float] | None = None) -> Choice:
    """The call's output: see the module's docstring. `peaks` are the
    loudest peaks (dBFS) heard lately on each device, by id; a device's own
    `peak` counts where `peaks` has none."""
    peaks = peaks or {}
    scored = []
    for n, device in enumerate(d for d in devices if d.kind == OUTPUT):
        signs = _programs(device)
        peak = peaks.get(device.id, device.peak)
        if peak is not None and peak > SOUND_ABOVE:
            signs.append((SOUND_NOW, R_SOUND))
        score, reasons = _score(device, signs)
        scored.append((score, reasons, n, device))
    return _best(scored)


def choose_input(devices: Iterable[Device], output: Device | None = None,
                 voices: Mapping[str, float] | None = None) -> Choice:
    """The user's microphone: see the module's docstring. `output` is the
    call's output (its device's microphone is the headset's); `voices` the
    loud part of what each input heard lately (dBFS of RMS, by id)."""
    inputs = [d for d in devices if d.kind == INPUT]
    voices = voices or {}
    loudest = None
    heard = sorted(((level, device.id) for device in inputs if (level := voices.get(device.id)) is not None),
                   reverse=True)
    if heard and heard[0][0] > VOICE_ABOVE and (len(heard) == 1 or heard[0][0] - heard[1][0] >= VOICE_MARGIN):
        loudest = heard[0][1]
    scored = []
    for n, device in enumerate(inputs):
        signs = _programs(device)
        if output is not None and same_device(device, output):
            signs.append((SAME_DEVICE, R_SAME_DEVICE))
        if device.id == loudest:
            signs.append((VOICE_LOUDEST, R_VOICE))
        score, reasons = _score(device, signs)
        scored.append((score, reasons, n, device))
    return _best(scored)


# looks at the devices in a row (the recorder's, WATCH_EVERY apart) in
# which a call program must have another microphone open before the
# recording follows it: a call program that opens a microphone for a moment
# (the test of its settings) is not followed
FOLLOW_AFTER = 2


def call_program(device: Device) -> str:
    """The call program that has the device open, as people call it ("Teams");
    "" when none (a browser does not count: it may be a video)."""
    return next((program_label(p) for p in device.programs if program_name(p) in CALL_PROGRAMS), "")


def call_input(devices: Iterable[Device], recorded: str) -> Device | None:
    """The microphone a call program has open now, when it is not the one
    recorded (`recorded`, an id): None when a call program has the recorded
    one open too, or no call program has a microphone open."""
    inputs = [d for d in devices if d.kind == INPUT]
    if any(d.id == recorded and call_program(d) for d in inputs):
        return None
    return choose_input([d for d in inputs if d.id != recorded and call_program(d)]).device


class Follow:
    """The call's microphone, followed while recording: look() is given each
    listing of the devices and the microphone recorded, and answers the one
    to record from instead once a call program has had it open for
    FOLLOW_AFTER looks in a row (None otherwise). Scriba's own streams are
    not in the listing (app/winaudio.py leaves its process out), so the
    meters it keeps open are no sign."""

    def __init__(self) -> None:
        self._seen, self._count = "", 0

    def look(self, devices: Iterable[Device], recorded: str) -> Device | None:
        found = call_input(devices, recorded)
        if found is None:
            self._seen, self._count = "", 0
            return None
        self._count = self._count + 1 if found.id == self._seen else 1
        self._seen = found.id
        if self._count < FOLLOW_AFTER:
            return None
        self._seen, self._count = "", 0
        return found


def may_open(device: Device) -> bool:
    """Whether the app may open the device just to show its level, before
    anything is recorded. Not a Bluetooth microphone nobody has open:
    opening one switches the headset to its hands-free profile, and the
    music or the call in the earphones drops to telephone quality."""
    return not (device.kind == INPUT and device.bluetooth and not device.programs)


@dataclass(frozen=True)
class Plan:
    """What a recording is made of: the input (None: no microphone) and the
    outputs, each a track; `automatic` says whether the app chose."""
    input: Device | None
    outputs: tuple[Device, ...]
    automatic_input: bool = True
    automatic_output: bool = True


def plan(devices: list[Device], input_on: bool = True, output_on: bool = True, input_id: str = "",
         output_id: str = "", peaks: Mapping[str, float] | None = None,
         voices: Mapping[str, float] | None = None) -> Plan:
    """The tracks of a recording from the user's switches and choices
    ("" = automatic): the chosen input; every output in the automatic mode
    (each a track: see the module's docstring), the chosen one otherwise.
    A device chosen by hand that is not there any more falls back to the
    automatic choice (the window says so: Plan.automatic_*)."""
    outputs = [d for d in devices if d.kind == OUTPUT]
    by_id = {d.id: d for d in devices}
    output = choose_output(devices, peaks).device
    record_outputs: tuple[Device, ...] = ()
    automatic_output = True
    if output_on:
        if output_id and by_id.get(output_id) is not None and by_id[output_id].kind == OUTPUT:
            record_outputs, output, automatic_output = (by_id[output_id],), by_id[output_id], False
        else:
            # the call's output first: it is the track shown first
            record_outputs = tuple(sorted(outputs, key=lambda d: d.id != (output.id if output else "")))
    chosen_input, automatic_input = None, True
    if input_on:
        if input_id and by_id.get(input_id) is not None and by_id[input_id].kind == INPUT:
            chosen_input, automatic_input = by_id[input_id], False
        else:
            chosen_input = choose_input(devices, output, voices).device
    return Plan(chosen_input, record_outputs, automatic_input, automatic_output)


# the programs as people call them; any other is shown by its executable's name
PROGRAM_LABELS = {"ms-teams": "Teams", "teams": "Teams", "msteams": "Teams", "zoom": "Zoom", "cpthost": "Zoom",
                  "webex": "Webex", "ciscocollabhost": "Webex", "skype": "Skype", "lync": "Skype", "slack": "Slack",
                  "discord": "Discord", "whatsapp": "WhatsApp", "telegram": "Telegram", "signal": "Signal",
                  "msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox", "brave": "Brave", "opera": "Opera"}


def program_label(path: str) -> str:
    """"C:\\...\\ms-teams.exe" -> "Teams"."""
    name = program_name(path)
    return PROGRAM_LABELS.get(name, name)
