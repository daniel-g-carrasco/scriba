#!/usr/bin/env python3
"""What the window asks of the engines: the work as the settings describe
it, where it would run, how long it would take."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup  # noqa: F401

from scriba.app import backend, jobs, models
from scriba.app.config import Config
from scriba.app.engines import whisper


class RequestTest(unittest.TestCase):
    def test_the_settings_become_the_work(self):
        cfg = Config(speech_language="en", quality="large", speakers=True, speaker_count=4, subtitled_video=True,
                     terms="QG&UPS, UPS; UPS", device="RTX A2000")
        request = backend.request_for(cfg, Path("call.mp4"), Path("out"), video=True)
        self.assertEqual((request.language, request.model, request.terms, request.speaker_count),
                         ("en", "large", ("QG&UPS", "UPS"), 4))
        self.assertTrue(request.video and request.speakers)
        self.assertEqual((request.cpu, request.device, request.out), (False, "RTX A2000", Path("out")))

    def test_a_video_is_asked_only_for_a_file_that_has_one_and_automatic_follows_the_test(self):
        cfg = Config(subtitled_video=True, measured_device="cpu", speaker_count=0, quality="nonsense")
        request = backend.request_for(cfg, Path("call.m4a"), None, video=False)
        self.assertFalse(request.video)
        self.assertTrue(request.cpu)
        self.assertIsNone(request.speaker_count)
        self.assertEqual(request.model, "turbo")
        self.assertEqual(backend.request_for(Config(measured_device="Iris"), Path("a"), None, False).device, "Iris")
        # chosen by hand, the card wins over the test's choice
        self.assertEqual(backend.request_for(Config(device="cpu", measured_device="Iris"), Path("a"), None, False).cpu,
                         True)


class EstimateTest(unittest.TestCase):
    def test_the_estimate_uses_the_speed_kept_for_the_card_that_would_be_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            speeds = Path(tmp) / "speeds.json"
            jobs.remember_speeds(speeds, {"turbo|Vulkan: RTX": 20.0, "turbo|cpu": 1.0, "speakers|cpu": 30.0})
            engines = backend.Backend(tmp, speeds)
            request = jobs.Request(Path("call.wav"), speakers=True, device="RTX")
            self.assertAlmostEqual(engines.estimate(600, request), 30 + 20)     # before the cards are listed
            cards = [whisper.Device(0, "Iris", "Intel", True), whisper.Device(1, "RTX", "NVIDIA", False)]
            with mock.patch.object(whisper, "list_devices", lambda cancelled=None: cards):
                self.assertEqual(engines.devices(), cards)
            self.assertAlmostEqual(engines.estimate(600, jobs.Request(Path("a"), speakers=False)), 30)
            self.assertAlmostEqual(engines.estimate(600, jobs.Request(Path("a"), cpu=True)), 600)

    def test_the_first_start_downloads_the_fast_model_the_filter_and_the_voices(self):
        self.assertEqual(backend.needed_models(), [models.WHISPER_TURBO, models.SILERO_VAD, models.WESPEAKER])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(backend.Backend(tmp, None).missing(), backend.needed_models())


if __name__ == "__main__":
    unittest.main()
