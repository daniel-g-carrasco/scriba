"""Package workflows reject forbidden runtime files before exporting artifacts."""
import _setup
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest


class BundleAuditWorkflowTests(unittest.TestCase):
    def test_package_workflows_run_a_blocking_audit_on_the_completed_payload(self):
        cases = (
            ('windows.yml', 'dist/Scriba', 'python tools/finish_bundle.py', 'cd dist && zip'),
            ('build.yml', 'build-dir/files', 'flatpak-builder --disable-rofiles-fuse', 'flatpak build-bundle'),
        )
        forbidden = ('torch/__init__.py', 'pyannote/audio/__init__.py',
                     'huggingface_hub/__init__.py', 'models/weights.bin',
                     'DirectML.dll', 'onnxruntime_providers_dml.dll')
        for workflow, payload, built, exported in cases:
            with self.subTest(workflow=workflow):
                text = (_setup.ROOT/'.github/workflows'/workflow).read_text(encoding='utf-8')
                step = re.search(r'      - name: Audit the runtime payload\n        run: (.+)\n', text)
                self.assertIsNotNone(step, 'A separate blocking audit step is required')
                command = shlex.split(step.group(1))
                self.assertEqual(command[:4], [command[0], 'tools/pyannote_benchmark.py',
                                              'audit-bundle', '--bundle'])
                self.assertIn(command[0], ('python', 'python3'))
                self.assertEqual(command[4:], [payload, '--out', 'runtime-bundle-audit.json'])
                self.assertLess(text.index(built), step.start())
                self.assertLess(step.end(), text.index(exported))
                # Execute the workflow command with only its interpreter, input and
                # report paths relocated. No shell wrapper can swallow a failure.
                for contamination in (None, *forbidden):
                    with self.subTest(contamination=contamination), tempfile.TemporaryDirectory() as folder:
                        root = Path(folder)
                        bundle = root/'payload'
                        bundle.mkdir()
                        for name in ('onnxruntime.so', 'licenses/pyannote-MIT.txt',
                                     'licenses/VBx-Apache.txt'):
                            target = bundle/name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.touch()
                        if contamination:
                            target = bundle/contamination
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.touch()
                        report = root/command[6]
                        done = subprocess.run([sys.executable, str(_setup.ROOT/command[1]),
                                               *command[2:4], str(bundle), command[5], str(report)],
                                              capture_output=True)
                        self.assertEqual(done.returncode, 1 if contamination else 0, done.stderr)
                        result = json.loads(report.read_text(encoding='utf-8'))
                        self.assertEqual(result['passed'], contamination is None)
                        if contamination:
                            self.assertIn(contamination, result['failures'])
