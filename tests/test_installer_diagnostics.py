"""Installer failures retain complete logs without changing the update scenario."""

import re
import unittest

import _setup


def step(name):
    text = (_setup.ROOT / '.github/workflows/windows.yml').read_text(encoding='utf-8')
    found = re.search(r'^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |^  release:|\Z)', text, re.M | re.S)
    if found is None:
        raise AssertionError('missing workflow step: ' + name)
    return found.group(1)


UPDATE = 'Update over a running app (Setup closes it, starts it again)'


class InstallerDiagnosticsTest(unittest.TestCase):
    def test_both_setup_logs_are_printed_in_finally_without_filtering(self):
        body = step(UPDATE)
        self.assertIn('/LOGCLOSEAPPLICATIONS', body)
        self.assertIn('$initialLog', body)
        self.assertIn('$updateLog', body)
        self.assertIn('finally {', body)
        final = body.rsplit('finally {', 1)[1]
        self.assertIn('@($initialLog, $updateLog)', final)
        self.assertIn('Get-Content -LiteralPath $log', final)
        self.assertNotIn('Select-String', body)
        self.assertIn('$held.Dispose()', final)

    def test_process_diagnostics_include_identity_and_paths_without_commands_or_environment(self):
        body = step(UPDATE)
        self.assertIn('Get-CimInstance Win32_Process', body)
        self.assertIn('Name, ProcessId, ParentProcessId, ExecutablePath', body)
        self.assertIn("Save-Processes 'before-install'", body)
        self.assertIn("Save-Processes 'before-update'", body)
        self.assertIn("Save-Processes 'after-update'", body)
        self.assertIn("Save-Processes 'finally'", body)
        self.assertNotIn('CommandLine', body)
        self.assertNotIn('Get-ChildItem Env:', body)

    def test_diagnostic_artifacts_survive_failures_and_are_separate_from_binaries(self):
        artifact = step('Preserve installer diagnostics')
        self.assertIn('if: always()', artifact)
        self.assertIn('actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9', artifact)
        self.assertIn('name: windows-installer-diagnostics', artifact)
        self.assertIn('path: installer-diagnostics/', artifact)
        self.assertNotIn('dist/', artifact)
        text = (_setup.ROOT / '.github/workflows/windows.yml').read_text(encoding='utf-8')
        binary = text.split('name: scriba-windows\n', 1)[1].split('\n  release:', 1)[0]
        self.assertNotIn('always()', binary)

    def test_existing_waits_and_shutdown_behaviour_are_preserved(self):
        body = step(UPDATE)
        self.assertEqual(body.count('Start-Sleep 15'), 2)
        self.assertIn('WaitForExit(300000)', body)
        self.assertIn("'/VERYSILENT', '/CURRENTUSER', '/SUPPRESSMSGBOXES', '/NORESTART'", body)
        self.assertNotIn('/FORCECLOSEAPPLICATIONS', body)
        self.assertNotIn('-Force', body)
        self.assertEqual(body.count('Get-Process Scriba | Stop-Process'), 1)


class IdleInstallerProfileTest(unittest.TestCase):
    def scripts(self):
        import textwrap
        names = ('An isolated idle profile for the installer checks',
                 'The installer preserves the paused profile and model cache')
        result = []
        for name in names:
            body = step(name)
            match = re.search(r"python - <<'PY'\n(.*?)\n          PY", body, re.S)
            self.assertIsNotNone(match)
            result.append(textwrap.dedent(match.group(1)))
        return result

    def fixture(self, root, script):
        import os
        import subprocess
        import sys
        environment = dict(os.environ, PYTHONPATH=str(_setup.ROOT / 'src'))
        environment.pop('MSYSTEM', None)
        for kind in ('CONFIG', 'STATE', 'DATA', 'CACHE'):
            environment['XDG_' + kind + '_HOME'] = str(root / kind.lower())
        return subprocess.run([sys.executable, '-c', script], env=environment, cwd=root,
                              capture_output=True, text=True, timeout=10)

    def test_the_real_config_api_creates_a_paused_profile_and_preserves_a_non_model_sentinel(self):
        import json
        import tempfile
        from pathlib import Path
        seed, verify = self.scripts()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            made = self.fixture(root, seed)
            self.assertEqual(made.returncode, 0, made.stderr)
            checked = self.fixture(root, verify)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            after = json.loads((root / 'installer-diagnostics/profile-after.json').read_text())
            self.assertIs(after['models_download_paused'], True)
            self.assertIs(after['check_updates'], False)
            self.assertEqual(after['measured_device'], 'cpu')
            before = json.loads((root / 'installer-diagnostics/profile-before.json').read_text())
            self.assertEqual(after['sentinel_sha256'], before['sentinel_sha256'])
            self.assertIn('this is not a model', Path(before['sentinel']).read_text())

    def test_a_changed_model_cache_sentinel_fails_the_preservation_check(self):
        import json
        import tempfile
        from pathlib import Path
        seed, verify = self.scripts()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            made = self.fixture(root, seed)
            self.assertEqual(made.returncode, 0, made.stderr)
            before = json.loads((root / 'installer-diagnostics/profile-before.json').read_text())
            Path(before['sentinel']).write_bytes(b'changed cache fixture')
            checked = self.fixture(root, verify)
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn('model cache sentinel changed', checked.stderr)

    def test_the_profile_is_complete_and_is_reused_without_screenshot_mode(self):
        seed = step('An isolated idle profile for the installer checks')
        for kind in ('CONFIG', 'STATE', 'DATA', 'CACHE'):
            self.assertIn('XDG_' + kind + '_HOME=', seed)
        self.assertIn('GITHUB_ENV', seed)
        self.assertIn('env -u MSYSTEM', seed)
        self.assertNotIn('SCRIBA_SCREENSHOT', seed)
        verify = step('The installer preserves the paused profile and model cache')
        self.assertIn("if: always() && steps.installer-profile.outcome == 'success'", verify)
