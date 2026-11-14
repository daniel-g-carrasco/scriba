#!/usr/bin/env python3
"""The command line: its options, its exit status, and that it never
loads the toolkit or a heavy library before it works."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

import _setup

from scriba import cli

SRC = str(_setup.ROOT / "src")


def scriba(*args: str) -> subprocess.CompletedProcess:
    environ = {**os.environ, "PYTHONPATH": SRC, "LANGUAGE": "en"}
    return subprocess.run([sys.executable, "-m", "scriba", *args], capture_output=True, text=True, env=environ,
                          stdin=subprocess.DEVNULL, timeout=120)


class OptionsTest(unittest.TestCase):
    def parse(self, *args):
        return cli.build_parser().parse_args(["transcribe", "call.mp4", *args])

    def test_speakers_alone_lets_the_engine_count_and_with_a_number_fixes_it(self):
        self.assertIsNone(self.parse().speakers)
        self.assertEqual(self.parse("--speakers").speakers, 0)
        self.assertEqual(self.parse("--speakers", "3").speakers, 3)
        args = self.parse("--speakers", "--video", "--language", "auto", "--model", "large", "--cpu")
        self.assertEqual((args.video, args.language, args.model, args.cpu), (True, "auto", "large", True))

    def test_the_files_of_terms_can_be_given_more_than_once_and_reach_the_request(self):
        from unittest import mock

        from scriba.app import jobs
        from scriba.app.task import Failure
        self.assertEqual(self.parse().terms_file, [])
        args = self.parse("--terms", "Cantiere Esempio", "--terms-file", "a.txt", "--terms-file", "b.txt")
        made = []

        def run(request, *_args, **_kwargs):
            made.append(request)
            raise Failure("stop here", "test")
        with mock.patch.object(jobs, "run", run), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.transcribe(args), cli.FAILED)
        self.assertEqual(made[0].terms, ("Cantiere Esempio",))
        self.assertEqual([os.path.basename(p) for p in made[0].term_files], ["a.txt", "b.txt"])

    def test_a_wrong_option_is_argparse_s_exit_2(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            self.parse("--language", "fr")
        self.assertEqual(caught.exception.code, cli.USAGE)

    def test_the_benchmark_runs_on_the_sample_unless_a_recording_is_given(self):
        self.assertIsNone(cli.build_parser().parse_args(["benchmark"]).file)
        self.assertEqual(cli.build_parser().parse_args(["benchmark", "call.mkv"]).file.name, "call.mkv")

    def test_the_benchmark_says_each_card_s_speed_and_words_and_why_one_was_left_out(self):
        from unittest import mock

        from scriba.app import jobs
        choice = jobs.Choice("RTX A2000", "Vulkan: RTX A2000", 20.0, 400.0, (
            jobs.Tried("Iris Xe", "Vulkan: Iris Xe", 30.0, 5.0, 0.3),
            jobs.Tried("RTX A2000", "Vulkan: RTX A2000", 20.0, 2.0, 0.02),
            jobs.Tried("cpu", "cpu", 0.0, 0.0, None, "exit 3\nmore")))
        asked = []
        with mock.patch.object(jobs, "try_devices", lambda *a, **k: asked.append(a) or choice), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli.benchmark_command(cli.build_parser().parse_args(["benchmark"]))
        self.assertEqual(code, cli.OK)
        self.assertEqual(asked[0][0], None)                                 # the sample
        lines = out.getvalue().splitlines()
        self.assertIn("Vulkan: Iris Xe: 30.0 times real time (first copy 5.0 s); 30 % of the words differ", lines[0])
        self.assertEqual(lines[2], "ERR   processor: exit 3")
        self.assertIn("Chosen: RTX A2000", lines[3])
        self.assertIn("Iris Xe was faster, but wrote 30 % of the sample's words wrong", lines[4])

    def test_the_terms_are_split_as_trascrivi_split_them(self):
        self.assertEqual(cli.terms_of("QG&UPS, UPS; CRAH\nUPS, "), ("QG&UPS", "UPS", "CRAH"))
        self.assertEqual(cli.terms_of(""), ())


class CommandTest(unittest.TestCase):
    def test_the_entry_point_knows_the_commands_of_the_command_line(self):
        source = (_setup.ROOT / "src" / "scriba" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("in COMMANDS", source)
        self.assertEqual(cli.build_parser()._subparsers._group_actions[0].choices.keys(), set(cli.COMMANDS))

    def test_help_answers_without_the_toolkit(self):
        done = scriba("transcribe", "--help")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--speakers", done.stdout)
        self.assertIn("transcribe", scriba("--help").stdout)

    def test_a_recording_that_is_not_there_is_said_with_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = scriba("transcribe", os.path.join(tmp, "gone.mp3"), "--out", tmp)
            self.assertEqual(done.returncode, cli.FAILED)
            self.assertIn("gone.mp3 is not there", done.stderr)
            self.assertEqual(os.listdir(tmp), [])

    def test_the_command_line_and_the_chain_load_no_heavy_library_until_they_work(self):
        code = ("import sys; import scriba.cli, scriba.app.jobs; print(','.join(m for m in ('numpy', 'sklearn', "
                "'scipy', 'ctypes', 'urllib.request', 'subprocess', 'gi') if m in sys.modules))")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                              env={**os.environ, "PYTHONPATH": SRC})
        self.assertEqual(done.stdout.strip(), "")

    def test_the_models_are_listed_with_what_they_are(self):
        with tempfile.TemporaryDirectory() as tmp:
            environ = {**os.environ, "PYTHONPATH": SRC, "LANGUAGE": "en", "SCRIBA_MODELS_DIR": tmp}
            done = subprocess.run([sys.executable, "-m", "scriba", "models"], capture_output=True, text=True,
                                  env=environ, stdin=subprocess.DEVNULL, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("ggml-large-v3-turbo-q5_0.bin", done.stdout)
            self.assertIn("--", done.stdout)                                  # none is there
            self.assertEqual(os.listdir(tmp), [])

    def test_check_says_where_each_engine_is_and_the_models_that_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            environ = {**os.environ, "PYTHONPATH": SRC, "LANGUAGE": "en", "SCRIBA_MODELS_DIR": tmp}
            done = subprocess.run([sys.executable, "-m", "scriba", "check"], capture_output=True, text=True,
                                  env=environ, stdin=subprocess.DEVNULL, timeout=120)
            self.assertIn(done.returncode, (cli.OK, cli.FAILED), done.stderr)
            for name in ("whisper-cli", "ffmpeg", "ffprobe", "onnxruntime", "sample of speech", "recorder"):
                self.assertIn(name, done.stdout)
            self.assertIn("--    ggml-large-v3-turbo-q5_0.bin", done.stdout)

    @unittest.skipUnless(os.environ.get("SCRIBA_TEST_RECORD") == "1",
                         "records from the real devices: SCRIBA_TEST_RECORD=1")
    def test_record_makes_a_recording_of_a_few_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            environ = {**os.environ, "PYTHONPATH": SRC, "LANGUAGE": "en"}
            done = subprocess.run([sys.executable, "-m", "scriba", "record", "3", "--out", tmp], capture_output=True,
                                  text=True, env=environ, stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual(done.returncode, cli.OK, done.stderr)
            self.assertIn("Recording ", done.stderr)
            made = sorted(p.suffix for p in __import__("pathlib").Path(tmp).iterdir())
            # A silent room produces no listening mix; the recording is still kept.
            self.assertIn(made, ([".mka"], [".m4a", ".mka"]), done.stdout)
            from scriba.app.engines import ffmpeg
            recording = next(__import__("pathlib").Path(tmp).glob("*.mka"))
            self.assertGreater(ffmpeg.probe(recording).duration, 2.5)

    def test_record_wants_a_folder_and_something_to_record(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["record", "5"])
        args = cli.build_parser().parse_args(["record", "5", "--out", "x", "--no-microphone", "--no-sound"])
        with contextlib.redirect_stderr(io.StringIO()) as said:
            self.assertEqual(cli.record_command(args), cli.USAGE)
        self.assertIn("Nothing to record", said.getvalue())


if __name__ == "__main__":
    unittest.main()
