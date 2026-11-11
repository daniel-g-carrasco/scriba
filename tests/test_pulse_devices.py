"""Pulse discovery uses its own provider and survives an unavailable sound server."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup


class PulseDevicesTest(unittest.TestCase):
    def setUp(self):
        try:
            from scriba.app import recorder
        except (ImportError, ValueError):
            self.skipTest("GStreamer")
        self.recorder = recorder
        self.enterContext(mock.patch.object(recorder, "init"))
        self.enterContext(mock.patch.object(recorder.Gst.DeviceMonitor, "new",
                                           side_effect=AssertionError("unrelated providers must not be started")))
        self.provider = mock.Mock()
        self.provider.start.return_value = True
        self.provider.get_devices.return_value = []
        self.factory = self.enterContext(mock.patch.object(recorder.Gst.DeviceProviderFactory, "get_by_name",
                                                           return_value=self.provider))

    def test_pulse_sources_and_monitors_are_listed_without_unrelated_providers(self):
        def device(name, factory, identity, kind):
            item = mock.Mock()
            item.get_display_name.return_value = name
            element = item.create_element.return_value
            element.get_factory.return_value.get_name.return_value = factory
            element.get_property.return_value = identity
            props = item.get_properties.return_value
            values = {"device.class": kind, "is-default": True}
            props.has_field.side_effect = lambda key: key in values
            props.get_string.side_effect = lambda key: values.get(key)
            props.get_value.side_effect = lambda key: values.get(key)
            return item

        microphone = device("Microphone", "pulsesrc", "test.input", "sound")
        monitor = device("Output monitor", "pulsesrc", "test.output.monitor", "monitor")
        sink = device("Output", "pulsesink", "test.output", "sound")
        self.provider.get_devices.return_value = [sink, microphone, monitor]
        found = self.recorder._pulse_devices()
        self.assertEqual([(d.id, d.kind) for d in found],
                         [("test.input", "input"), ("test.output.monitor", "output")])
        self.assertTrue(found[0].default)
        self.factory.assert_called_once_with("pulsedeviceprovider")
        self.provider.stop.assert_called_once()

    def test_a_missing_provider_or_unavailable_server_returns_no_devices_without_stopping(self):
        self.factory.return_value = None
        self.assertEqual(self.recorder._pulse_devices(), [])
        self.factory.return_value = self.provider
        self.provider.start.return_value = False
        self.assertEqual(self.recorder._pulse_devices(), [])
        self.provider.get_devices.assert_not_called()
        self.provider.stop.assert_not_called()

    def test_a_started_provider_is_stopped_if_enumeration_fails(self):
        self.provider.get_devices.side_effect = RuntimeError("enumeration failed")
        with self.assertRaisesRegex(RuntimeError, "enumeration failed"):
            self.recorder._pulse_devices()
        self.provider.stop.assert_called_once()

    @unittest.skipUnless(sys.platform.startswith("linux"), "PulseAudio discovery on Linux")
    def test_an_unavailable_pulse_server_does_not_crash_repeated_discovery_in_a_child(self):
        # Native GStreamer failures abort Python; they must stay in a subprocess.
        code = (
            "import resource,sys; resource.setrlimit(resource.RLIMIT_CORE,(0,0)); "
            f"sys.path.insert(0,{str(_setup.ROOT / 'src')!r}); "
            "from scriba.app import recorder; "
            "assert all(recorder._pulse_devices()==[] for _ in range(8)); "
            "print('unavailable Pulse server: repeated discovery completed')"
        )
        with tempfile.TemporaryDirectory() as tmp:
            environ = {**os.environ, "PULSE_SERVER": f"unix:{Path(tmp) / 'missing-pulse.socket'}"}
            done = subprocess.run([sys.executable, "-c", code], env=environ, cwd=tmp,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        detail = done.stderr.decode("utf-8", "replace")
        self.assertEqual(done.returncode, 0, detail)
        self.assertIn(b"repeated discovery completed", done.stdout)
        self.assertNotIn("CRITICAL", detail)
