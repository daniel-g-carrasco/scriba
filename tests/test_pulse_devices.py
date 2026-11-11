"""PulseAudio devices remain discoverable beside a higher-priority PipeWire provider."""

import unittest
from unittest import mock

import _setup  # noqa: F401 (make the source package importable)


class PulseDevicesTest(unittest.TestCase):
    def test_pulse_sources_and_monitors_are_requested_even_when_another_provider_hides_them(self):
        try:
            from scriba.app import recorder
        except (ImportError, ValueError):
            self.skipTest("GStreamer")

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
        pipewire = device("Microphone", "pipewiresrc", "57", "sound")
        provider = mock.Mock()
        # DeviceMonitor suppresses the Pulse provider by default on PipeWire.
        provider.get_devices.side_effect = lambda: ([pipewire, microphone, monitor]
                                                    if provider.set_show_all_devices.called else [pipewire])
        with mock.patch.object(recorder, "init"), \
                mock.patch.object(recorder.Gst.DeviceMonitor, "new", return_value=provider):
            found = recorder._pulse_devices()
        self.assertEqual([(d.id, d.kind) for d in found],
                         [("test.input", "input"), ("test.output.monitor", "output")])
        self.assertTrue(found[0].default)
        provider.stop.assert_called_once()
