"""Whose work Scriba runs on, and under which licence: what Informazioni
shows in its legal sections (the window adds them with
Adw.AboutDialog.add_legal_section, as Cartiglio does for MuPDF), and what
the licences ask to be said.

Two of them ask more than a line. WeSpeaker's weights are under Creative
Commons Attribution 4.0 (WeSpeaker's docs/pretrained.md: the models
trained on VoxCeleb follow the dataset's licence), which wants the source
named. diarize's grouping of the voices is copied into
app/engines/clustering.py under the Apache License 2.0, which wants its
text given with the work: LICENSES/diarize-Apache-2.0.txt, and the notice
at the top of that file.

The sample of speech the cards are tested on (samples/, with its
SOURCES.md) is in the public domain, as every LibriVox recording: nothing
is owed, and the reader and the source are named all the same.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Credit:
    name: str
    role: str               # what it does in Scriba (English: the window translates nothing here, it is a name)
    licence: str            # SPDX identifier
    holder: str             # the copyright holder, as the project writes it
    url: str


THIRD_PARTY = (
    Credit("whisper.cpp and ggml", "speech to text (whisper-cli, run as a program)", "MIT",
           "The ggml authors", "https://github.com/ggml-org/whisper.cpp"),
    Credit("Whisper large-v3 and large-v3-turbo", "the speech model's weights, in ggml", "MIT",
           "OpenAI", "https://huggingface.co/ggerganov/whisper.cpp"),
    Credit("Silero VAD", "finds where somebody speaks (the model and its state machine)", "MIT",
           "Silero Team", "https://github.com/snakers4/silero-vad"),
    Credit("WeSpeaker ResNet34-LM", "the voice embedder's weights, trained on VoxCeleb", "CC-BY-4.0",
           "WeSpeaker", "https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM"),
    Credit("diarize", "the grouping of the voices, copied into the app", "Apache-2.0",
           "Alexander Lukashov (FoxNoseTech)", "https://github.com/FoxNoseTech/diarize"),
    Credit("ONNX Runtime", "runs the speech filter and the voice embedder", "MIT",
           "Microsoft Corporation", "https://github.com/microsoft/onnxruntime"),
    Credit("NumPy, SciPy, scikit-learn", "the numbers of the voices", "BSD-3-Clause",
           "The NumPy, SciPy and scikit-learn developers", "https://scikit-learn.org"),
    # on Windows, ffmpeg and ffprobe built from FFmpeg 9.0.2's release
    # sources with no GPL part (tools/build_ffmpeg.sh; the configure line is
    # in the build's licenses/ffmpeg/BUILD.txt, as the LGPL asks); in the
    # Flatpak, the GNOME runtime's
    Credit("FFmpeg", "reads audio and video, makes the video with subtitles (run as a program; on Windows "
           "built from https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz without its GPL parts)",
           "LGPL-2.1-or-later", "The FFmpeg developers", "https://ffmpeg.org"),
    Credit("GStreamer", "records the microphone and the computer's sound, plays Listen (with its plugins)",
           "LGPL-2.1-or-later", "The GStreamer developers", "https://gstreamer.freedesktop.org"),
    Credit("«Le avventure di Pinocchio», chapter I, read by Simona Pagliari for LibriVox",
           "the sample of speech the graphics cards are tested on (the first 27 seconds; Carlo Collodi, 1883)",
           "CC-PDDC", "Public domain (LibriVox)", "https://archive.org/details/avventure_pinocchio_librivox"),
)
