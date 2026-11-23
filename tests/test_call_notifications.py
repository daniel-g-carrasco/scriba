import unittest
from unittest import mock
from xml.etree import ElementTree

import _setup


@unittest.skipUnless(_setup.WINDOWS, "WinRT notification actions")
class CallNotificationsTest(unittest.TestCase):
    def test_action_labels_and_arguments_are_escaped(self):
        from scriba.app import call_notifications
        payload = call_notifications.xml("Call ended", {"stop": ('Stop "now"', None)})
        action = ElementTree.fromstring(payload).find("actions/action")
        self.assertEqual(action.attrib["content"], 'Stop "now"')
        self.assertEqual(action.attrib["arguments"], "stop")

    def test_native_activation_dispatches_the_selected_action(self):
        from scriba.app import call_notifications
        selected = []
        handler = call_notifications._ActionHandler({
            "stop": ("Stop", lambda: selected.append("stopped")),
            "continue": ("Continue", lambda: selected.append("continued")),
        })
        with mock.patch.object(call_notifications, "_argument", return_value="continue"):
            handler._invoke(None, None, object())
        self.assertEqual(selected, ["continued"])
