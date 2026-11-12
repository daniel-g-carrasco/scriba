"""The Linux CI audio fixture owns its server and cleans both children on failure."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup

SPEC = importlib.util.spec_from_file_location("ci_audio", _setup.ROOT / "tools/ci_audio.py")
audio = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audio)


@unittest.skipIf(os.name == "nt", "the private PulseAudio CI fixture runs on Linux")
class PrivateAudioTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        server = '''import os,signal,socket,sys
from pathlib import Path
Path(os.environ["FIXTURE_SERVER_PID"]).write_text(str(os.getpid()))
arg=next(arg for arg in sys.argv if "module-native-protocol-unix" in arg)
path=next(word[7:] for word in arg.split() if word.startswith("socket="))
stream=socket.socket(socket.AF_UNIX);stream.bind(path);stream.listen()
def stop(*_args):raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
try:
 while True:
  conn,_=stream.accept();conn.sendall(b"ready");conn.close()
finally:stream.close()
'''
        query = '''import os,socket,sys
stream=socket.socket(socket.AF_UNIX)
try:
 stream.connect(os.environ["PULSE_SERVER"].removeprefix("unix:"));assert stream.recv(10)==b"ready"
except OSError:raise SystemExit(1)
if sys.argv[1:]==["info"]:print("Default Sink: "+os.environ.get("FIXTURE_DEFAULT_SINK","scriba_ci"))
else:print("0\\tscriba_ci\\tmodule-null-sink.c\\ts16le 2ch 44100Hz\\tIDLE")
'''
        for name, body in (("pulseaudio", server), ("pactl", query)):
            path = self.bin / name
            path.write_text("#!" + sys.executable + "\n" + body)
            path.chmod(0o755)
        self.pid = self.root / "server.pid"
        self.enterContext(mock.patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                                                       "FIXTURE_SERVER_PID": str(self.pid)}))

    def released(self, pid):
        with self.assertRaises(ProcessLookupError):
            os.kill(int(Path(pid).read_text()), 0)

    def test_the_command_gets_only_the_private_endpoint_and_the_server_is_reaped(self):
        before = os.environ.get("PULSE_SERVER")
        result = self.root / "endpoint"
        command = [sys.executable, "-c", "import os,pathlib;pathlib.Path(" + repr(str(result)) +
                   ").write_text(os.environ['PULSE_SERVER']+' '+os.environ['PULSE_SINK'])"]
        self.assertEqual(audio.run(command, self.root / "server.log"), 0)
        endpoint = result.read_text().split()[0].removeprefix("unix:")
        self.assertFalse(Path(endpoint).exists())
        self.assertTrue(result.read_text().endswith(" scriba_ci"))
        self.assertEqual(os.environ.get("PULSE_SERVER"), before)
        self.released(self.pid)

    def test_a_failed_command_keeps_its_status_and_releases_the_server(self):
        self.assertEqual(audio.run([sys.executable, "-c", "raise SystemExit(7)"], self.root / "server.log"), 7)
        self.released(self.pid)

    def test_a_timed_out_command_and_its_server_are_both_reaped(self):
        child = self.root / "command.pid"
        code = "import os,pathlib,time;pathlib.Path(" + repr(str(child)) + ").write_text(str(os.getpid()));time.sleep(30)"
        with self.assertRaises(subprocess.TimeoutExpired):
            audio.run([sys.executable, "-c", code], self.root / "server.log", timeout=.2)
        self.released(child)
        self.released(self.pid)

    def test_an_unexpected_default_sink_cannot_start_the_command(self):
        marker = self.root / "unexpected"
        with mock.patch.dict(os.environ, {"FIXTURE_DEFAULT_SINK": "other_sink"}):
            with self.assertRaisesRegex(RuntimeError, "ready"):
                audio.run([sys.executable, "-c", "open(" + repr(str(marker)) + ",'w').close()"],
                          self.root / "server.log", ready_timeout=.25)
        self.assertFalse(marker.exists())
        self.released(self.pid)


class AudioWorkflowTest(unittest.TestCase):
    def test_all_linux_gui_commands_use_the_private_audio_fixture(self):
        text = (_setup.ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        gtk = text.split("  gtk:\n", 1)[1]
        self.assertIn("pulseaudio pulseaudio-utils", gtk)
        self.assertEqual(gtk.count("tools/ci_audio.py"), 3)
        for name in ("full-audio.log", "window-audio.log", "gui/audio.log"):
            self.assertIn(name, gtk)
        self.assertIn("test_window", gtk)
        self.assertIn("ModuleSkipped", gtk)
        self.assertNotIn("continue-on-error", gtk)
